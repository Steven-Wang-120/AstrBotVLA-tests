from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from astrbot_plugin_astrbotex_interaction.task_coordinator import TaskCoordinator
from astrbot_plugin_astrbotex_interaction.task_store import TaskStore
from astrbot_plugin_astrbotex_interaction.zmq_transport import ZmqRemoteError
import unittest
from astrbot_plugin_astrbotex_interaction.tests import test_plugin_channels as channels
from astrbot_plugin_astrbotex_interaction.tests.test_plugin_channels import envelope
from astrbot_plugin_astrbotex_interaction.tests.test_task_coordinator import goal_args, FakeDecision
from astrbot_plugin_astrbotex_interaction.tests.test_task_store import authority, steps


class DecisionRejectionZmqTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = channels.PluginChannelTests.asyncSetUp
    asyncTearDown = channels.PluginChannelTests.asyncTearDown
    _connect = channels.PluginChannelTests._connect
    _request = channels.PluginChannelTests._request
    async def test_enabled_initialize_injects_real_plugin_turn_transport(self):
        import os
        from unittest.mock import patch
        from astrbot_plugin_astrbotex_interaction.task_coordinator import TaskCoordinator
        from astrbot_plugin_astrbotex_interaction import main as plugin_module
        from astrbot_plugin_astrbotex_interaction.tests.test_task_admission import event
        from astrbot_plugin_astrbotex_interaction.tests.test_planning_tools import response
        await self.plugin.terminate()
        with tempfile.TemporaryDirectory() as directory:
            async def generate(**kwargs):
                return response(["finish_planning_turn"], [{"outcome": "waiting_input"}])
            self.context.llm_generate = generate
            self.plugin.task_planning_enabled = True
            self.plugin.task_provider_id = "offline"
            self.plugin.task_robot_id = "robot"
            with patch.dict(os.environ, {"ASTRBOTEX_TASK_DB_PATH": str(Path(directory) / "enabled.db")}):
                await self.plugin.initialize()
            client = await self._connect("text", self.plugin.text_port)
            captured = []
            async def respond():
                for _ in range(2):
                    raw = await asyncio.wait_for(client.recv_multipart(), 1)
                    request = json.loads(raw[0])
                    captured.append(request)
                    payload = ({"ok": True, "ex_session": "ex1", "revision": 0, "actions": []}
                               if request["method"] == "decision.context.get" else {"ok": True})
                    reply = envelope("text", request["method"], payload)
                    reply.update(kind="response", reply_to=request["id"])
                    await client.send_json(reply)
            worker = asyncio.create_task(respond())
            item = event("ex_task legal silent entry")
            await self.plugin.ex_task(item)
            await worker
            for _ in range(100):
                task = self.plugin.task_store.active_tasks()[0]
                if task["status"] == "waiting_input":
                    break
                await asyncio.sleep(0.001)
            self.assertEqual(task["status"], "waiting_input")
            self.assertEqual([r["method"] for r in captured], ["decision.context.get", "interaction.task.turn"])
            self.assertEqual(captured[1]["payload"]["operation"], "bind")
            self.assertEqual(self.plugin.task_coordinator.turn_sync, self.plugin._sync_task_turn)
            self.assertEqual(item.get_result().get_plain_text(), "")
            self.plugin.task_coordinator.turn_sync = None
            await self.plugin.terminate()

    async def test_localhost_turn_extension_bind_invalidate_exact_payload(self):
        client = await self._connect("text", self.plugin.text_port)
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "turn.db"))
            self.plugin.task_store = store
            self.plugin.admit_task_route(authority(), b"test-text")
            coordinator = TaskCoordinator(store, self.plugin._request_decision, turn_sync=self.plugin._sync_task_turn)
            task = store.create(authority(), "private", "create")
            store.mutate(task["task_id"], lambda t: t.update(ex_session="ex1"))
            turn = store.begin_turn(task["task_id"])
            received = []
            async def respond():
                raw = await asyncio.wait_for(client.recv_multipart(), 1)
                request = json.loads(raw[0])
                self.assertEqual(request["method"], "interaction.task.turn")
                self.assertEqual(request["version"], 1)
                received.append(request["payload"])
                reply = envelope("text", request["method"], {"ok": True})
                reply.update(kind="response", reply_to=request["id"])
                await client.send_json(reply)
            worker = asyncio.create_task(respond())
            await coordinator.sync_turn(task["task_id"], "bind", turn=turn)
            await worker
            worker = asyncio.create_task(respond())
            await coordinator.user_input(task["task_id"], authority(), "changed")
            await worker
            self.assertEqual([p["operation"] for p in received], ["bind", "invalidate"])
            from astrbot_plugin_astrbotex_interaction.tests.test_task_turn_sync import FIELDS
            self.assertEqual(set(received[0]), FIELDS)
            self.assertEqual(received[0]["turn_id"], turn.turn_id)
            self.assertIsNone(received[1]["turn_id"])
            self.assertGreater(received[1]["generation"], received[0]["generation"])
            coordinator.turn_sync = None
            await coordinator.close()
            store.close()
            self.plugin.task_store = None

    async def test_localhost_legal_rejected_is_durable_and_unknown_remote_error_still_raises(self):
        client = await self._connect("text", self.plugin.text_port)
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.db"))
            self.plugin.task_store = store
            self.plugin.admit_task_route(authority(), b"test-text")
            coordinator = TaskCoordinator(store, self.plugin._request_decision)
            task = store.create(authority(), "task", "create")
            store.mutate(task["task_id"], lambda t: t.update(ex_session="ex1"))
            turn = store.begin_turn(task["task_id"])
            store.save_plan(turn, steps(), 0)
            async def responder(mode):
                raw = await asyncio.wait_for(client.recv_multipart(), 1)
                request = json.loads(raw[0])
                payload = request["payload"]
                rejected = {"ok": False, "request_id": payload["request_id"],
                    "ex_session": payload["ex_session"], "goal_id": payload["goal_id"],
                    "revision": 0, "phase": "rejected",
                    "error": {"code": "revision_conflict", "path": "expected_revision", "message": "CAS rejected"}}
                if mode == "foreign":
                    rejected["goal_id"] = "wrong-goal"
                elif mode == "malformed":
                    rejected = {"ok": False, "error": "SECRET remote exception"}
                reply = envelope("text", request["method"], rejected)
                reply.update(kind="response", reply_to=request["id"])
                await client.send_json(reply)
            worker = asyncio.create_task(responder("legal"))
            result = await coordinator.submit(turn, goal_args(), FakeDecision().context, "submit")
            await worker
            self.assertEqual(result["phase"], "rejected")
            self.assertEqual(result["revision"], 0)
            self.assertIsNone(store.get(task["task_id"])["current_goal"])
            self.assertEqual(store.request(result["request_id"])["result"], result)
            self.assertNotIn(task["task_id"], coordinator._lease_deadlines)
            self.assertNotIn(task["task_id"], coordinator._lease_identities)
            # Only the framework seam opts in; ordinary channel/request keeps errors.
            payload = store.request(result["request_id"])["payload"]
            for mode in ("legal", "foreign", "malformed"):
                worker = asyncio.create_task(responder(mode))
                with self.assertRaises(ZmqRemoteError):
                    if mode == "legal":
                        await self.plugin.request_text("decision.goal.submit", payload, peer=b"test-text")
                    else:
                        await self.plugin._request_decision("r1", "route-s1", "decision.goal.submit", payload)
                await worker
            await coordinator.close()
            store.close()
            self.plugin.task_store = None
