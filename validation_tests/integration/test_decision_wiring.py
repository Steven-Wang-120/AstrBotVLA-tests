from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import AsyncExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from astrbot_ex.core.api_server import build_server
from astrbot_ex.core.actions.ledger import OwnerBinding, StopEvidence
from astrbot_ex.core.contracts import DecisionState, EventsReply
from astrbot_ex.core.decision.backends import MockBackend
from astrbot_ex.core.decision.feedback_journal import FeedbackJournal
from astrbot_ex.core.plugin_actor import PluginActor
from tests.test_decision_service import ActionOwner, wait_for
from tests.test_goal_manager import goal_payload, make_catalog

from tests.wiring_fixture import WiringFixture

AEB_PURE_MODULES = ("task_contracts", "task_models", "task_store", "task_coordinator", "zmq_transport")


@contextmanager
def aeb_modules():
    # Explicit audited checkout only; never import its Host __init__ or providers.
    configured = os.environ.get("ASTRBOTEX_TEST_AEB_CHECKOUT")
    if not configured or not Path(configured).is_absolute():
        raise RuntimeError("ASTRBOTEX_TEST_AEB_CHECKOUT must name an absolute real checkout")
    source = Path(configured).resolve() / "astrbot_plugin_astrbotex_interaction"
    missing = [name + ".py" for name in AEB_PURE_MODULES if not (source / (name + ".py")).is_file()]
    if not source.is_dir() or missing:
        raise RuntimeError("Explicit AEB checkout lacks pure modules: " + ", ".join(missing))
    package = "offline_aeb_wiring"
    previous = {key: value for key, value in sys.modules.items()
                if key == package or key.startswith(package + ".")}
    try:
        for key in previous:
            del sys.modules[key]
        namespace = types.ModuleType(package)
        namespace.__path__ = [str(source)]
        sys.modules[package] = namespace
        def load(name):
            if name not in AEB_PURE_MODULES:
                raise ValueError("Only audited pure AEB modules may be loaded")
            return importlib.import_module(package + "." + name)
        yield load
    finally:
        for key in list(sys.modules):
            if key == package or key.startswith(package + "."):
                del sys.modules[key]
        sys.modules.update(previous)


class RealControllerTests(WiringFixture):
    def test_default_disabled_route_context_and_contract_state(self):
        self.assertEqual(self.s.mode, "disabled")
        self.bind()
        context = self.request("decision.context.get", {"schema_version": 1})
        self.assertEqual(context["ex_session"], self.s.goals.ex_session)
        self.assertFalse(context["execution"]["execution_allowed"])
        result = self.submit()
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "rejected")
        self.assertEqual(result["error"]["code"], "decision_disabled")
        for conn, feature, binary in [("foreign", "text", None), ("trusted", "audio", None), ("trusted", "text", b"x")]:
            self.assertFalse(self.request("decision.context.get", {"schema_version": 1}, conn, feature, binary)["ok"])
        DecisionState.parse(self.request("decision.state.get", {"schema_version": 1}))
        self.assertIsNone(self.s.goals.active)
        self.assertFalse(self.server.action_dispatcher._gate)

    def test_real_success_journal_clears_goal_and_no_auto_next(self):
        self.execute()
        self.bind()
        result = self.submit()
        self.assertTrue(result["ok"])
        self.bind()  # same framework turn, refreshed legal revision
        cid = self.succeed()
        self.assertTrue(wait_for(lambda: self.s.goals.active is None and self.c.journal.snapshot()["feedback"] is not None))
        state = DecisionState.parse(self.c.state()).to_dict()
        fact = state["execution"]["feedback"]
        self.assertEqual(fact["status"], "succeeded")
        evidence = fact["details"]["completion_evidence"]
        self.assertTrue(evidence["verified"])
        self.assertEqual(evidence["goal_id"], result["goal_id"])
        self.assertEqual(evidence["goal_revision"], result["revision"])
        self.assertEqual(evidence["goal_id"], fact["goal_id"])
        self.assertEqual(evidence["goal_revision"], fact["goal_revision"])
        self.assertEqual(evidence["commands"][0]["command_id"], cid)
        self.assertEqual(evidence["required_actions"], ["arm.move.v1"])
        self.assertIsNone(state["pending_goal_id"])
        self.assertFalse(state["execution"]["gate_open"])
        page = EventsReply.parse(self.c.journal.events(0))
        self.assertEqual([e["event_seq"] for e in page.events], list(range(1, page.latest_event_seq + 1)))
        action_success = [e for e in page.events if e["details"].get("action_status") == "succeeded"]
        self.assertEqual(action_success[0]["status"], "running")  # never premature goal success
        self.assertEqual(len(self.owners["arm"].commands), 1)

    def test_multiple_required_actions_not_single_success(self):
        self.execute(("arm", "camera"))
        self.bind()
        self.assertTrue(self.submit(owners=("arm", "camera"))["ok"])
        self.succeed("arm")
        self.assertIsNone(self.c.journal.snapshot()["feedback"])
        self.assertIsNotNone(self.s.goals.active)
        self.succeed("camera")
        self.assertTrue(wait_for(lambda: self.s.goals.active is None))
        self.assertEqual(self.c.state()["execution"]["feedback"]["details"]["completion_evidence"]["succeeded_actions"],
                         ["arm.move.v1", "camera.move.v1"])

    def test_missing_stop_and_late_proof_remain_blocked(self):
        self.execute(stop_proof=False)
        self.server.action_service.stop_timeout = 0.1
        self.bind()
        self.submit()
        self.assertTrue(self.owners["arm"].started.wait(2))
        cid = self.owners["arm"].commands[0].command_id
        result = self.request("decision.goal.cancel", {"schema_version": 1, "request_id": "cancel",
            "ex_session": self.s.goals.ex_session, "goal_id": "goal-1", "goal_revision": 1})
        self.assertTrue(result["accepted"])
        self.assertFalse(result["stopped"])
        self.assertTrue(wait_for(lambda: self.s.goals.phase == "blocked"
            and any(row.command_id == cid and row.status in {"unknown", "timed_out"}
                    for row in self.s._rows)
            and bool(self.c.state()["execution"]["unresolved"])), self.diagnostics())
        state = self.c.state()
        self.assertEqual(state["pending_phase"], "blocked")
        self.assertIsNone(state["execution"]["feedback"])
        self.assertTrue(state["execution"]["unresolved"])
        self.server.action_dispatcher.reconcile_stop(cid, OwnerBinding("arm", 1),
            StopEvidence(cid, True, "arm", "late-stop")).result(1)
        self.s.tick()
        threading.Event().wait(0.1)
        self.assertEqual(self.s.goals.phase, "blocked")
        self.assertIsNotNone(self.s.goals.active)
        self.assertIsNone(self.c.state()["execution"]["feedback"])

    def test_turn_watermark_owner_revision_and_no_raw_self_binding(self):
        turn = self.bind()
        for key in ("task_id", "robot_id", "session_id", "user_id", "route_ref", "turn_id"):
            self.assertFalse(self.request("interaction.task.turn", {**turn, key: "foreign"})["ok"])
        for conn, feature, binary in [("foreign", "text", None), ("trusted", "audio", None), ("trusted", "text", b"x")]:
            self.assertFalse(self.request("interaction.task.turn", turn, conn, feature, binary)["ok"])
        for delta in [{"generation": True}, {"expected_revision": 9}, {"extra": "x"}, {"turn_id": None}]:
            self.assertFalse(self.request("interaction.task.turn", {**turn, **delta})["ok"])
        self.assertFalse(self.request("interaction.reply", self.public({**turn, "user_id": "foreign"}))["ok"])
        self.assertFalse(self.request("interaction.reply", self.public({**turn, "session_id": "foreign"}))["ok"])
        self.assertTrue(self.request("interaction.task.turn", {**turn, "operation": "invalidate", "turn_id": None})["ok"])
        self.assertFalse(self.request("interaction.task.turn", turn)["ok"])
        newer = self.bind(2, turn_id="new")
        self.assertFalse(self.request("interaction.task.turn", turn)["ok"])
        self.c.invalidate_local()
        self.assertFalse(self.request("interaction.task.turn", newer)["ok"])

    def test_public_text_xor_tts_recheck_after_blocked_provider_and_finish(self):
        turn = self.bind()
        core = self.server.interaction_core
        audio, text = [], []
        core.topic_bus.subscribe("interaction_core.audio.play", lambda m: audio.append(m.payload))
        core.topic_bus.subscribe("interaction_core.message.outgoing", lambda m: text.append(m.payload))
        entered, release = threading.Event(), threading.Event()
        class BlockedOfflineProvider:
            async def get_audio(self, value):
                entered.set()
                await asyncio.to_thread(release.wait, 2)
                return "offline-no-device.wav"
        core.tts_provider = BlockedOfflineProvider()
        payload = self.public(turn, delivery="tts")
        self.assertTrue(self.request("interaction.reply", payload)["ok"])
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.request("interaction.reply", payload)["duplicate"])
        # Finishing a planning turn sends no invalidation: legal late TTS remains authorized.
        release.set()
        self.assertTrue(wait_for(lambda: len(audio) == 1))
        self.assertEqual(text, [])
        entered.clear(); release.clear()
        self.assertTrue(self.request("interaction.reply", self.public(turn, "old-tts", "tts"))["ok"])
        self.assertTrue(entered.wait(1))
        # A queued text behind blocked TTS is canceled together with old audio.
        self.assertTrue(self.request("interaction.reply", self.public(turn, "old-text"))["ok"])
        core._generation += 1
        core.topic_bus.publish_payload("interaction_core.audio.stop", timestamp=time.time(), source="interaction_core", payload={"reason": "user_speech"})
        release.set()
        self.assertTrue(wait_for(lambda: self.server.task_public_delivery._queue.unfinished_tasks == 0))
        self.assertEqual(len(audio), 1)
        self.assertEqual(text, [])
        new = self.bind(2, turn_id="new")
        self.assertTrue(self.request("interaction.reply", self.public(new, "new-text"))["ok"])
        self.assertTrue(wait_for(lambda: len(text) == 1))
        self.assertEqual(len(audio), 1)
        self.assertFalse(self.request("interaction.reply", self.public(new, "fake-complete", claim="completed", evidence_seq=99))["ok"])

    def test_journal_individual_ack_pagination_trim_recovery_and_restart(self):
        self.execute()
        self.bind()
        self.submit()
        self.succeed()
        self.assertTrue(wait_for(lambda: self.s.goals.active is None))
        journal = self.c.journal
        facts = journal.pending()
        self.assertGreaterEqual(len(facts), 4)
        self.assertTrue(journal.ack(facts[-1]))
        self.assertEqual(journal.pending()[0]["event_seq"], 1)
        self.assertFalse(journal.ack({**facts[0], "status": "failed"}))
        journal.page_size = 2
        page = journal.events(0)
        self.assertEqual(page["latest_event_seq"], page["events"][-1]["event_seq"])
        self.assertEqual(page["latest_event_seq"], 2)
        journal.retention = 2
        with journal._lock, journal._db:
            # Use another committed ledger fact rather than inventing completion.
            journal._db.execute("UPDATE sessions SET trimmed=2 WHERE session=?", (journal.session,))
            journal._db.execute("DELETE FROM facts WHERE session=? AND seq<=2", (journal.session,))
        page = journal.events(0)
        self.assertTrue(page["resync_required"])
        self.assertEqual(page["events"], [])
        self.assertTrue(journal.snapshot()["feedback"]["details"]["completion_evidence"]["verified"])
        other = FeedbackJournal(Path(self.tmp.name) / "execution/feedback.sqlite3", "new-session")
        try:
            self.assertEqual(other.snapshot()["event_seq"], 0)
            self.assertTrue(other.snapshot()["previous_session_manual_review"])
            self.assertEqual(other.pending(), [])
            self.assertIsNone(other.snapshot()["feedback"])
        finally:
            other.close()

    def test_superseded_completion_epoch_cannot_clear_new_goal(self):
        self.execute()
        self.bind()
        self.submit()
        self.assertTrue(wait_for(lambda: self.s.goals.active is not None), self.diagnostics())
        old, epoch, _ = self.s.goals.current()
        self.s.goals.stop("old-canceled")
        self.s.goals.submit(goal_payload(self.s.goals, 2, expected_revision=1))
        self.assertFalse(self.s.goals.finish_stopped(old, epoch))
        self.assertEqual(self.s.goals.pending_replace.payload()["goal_id"], "goal-2")


class RealZmqCompositionTests(WiringFixture):
    def test_aeb_transport_durable_feedback_two_steps_new_authority(self):
        with aeb_modules() as load:
            self.execute()
            loop = asyncio.SelectorEventLoop() if os.name == "nt" else asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._two_steps(load))
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()

    async def _two_steps(self, load):
        async with AsyncExitStack() as cleanup:
            await self._two_steps_with_resources(load, cleanup)

    async def _two_steps_with_resources(self, load, cleanup):
        channel = load("zmq_transport").ZmqRouterChannel("text", "tcp://127.0.0.1:0")
        cleanup.push_async_callback(channel.close)
        await channel.start()
        endpoint = channel.socket.getsockopt(__import__("zmq").LAST_ENDPOINT).decode()
        record = self.m._records["trusted"]
        record.config["endpoint"] = endpoint
        record.enabled = False
        cleanup.callback(self.m.close)
        self.m.start("trusted")
        store = load("task_store").TaskStore(str(Path(self.tmp.name) / "aeb-tasks.sqlite3"))
        cleanup.callback(store.close)
        authority = load("task_models").TaskAuthority("robot", "session", "user", "route", True)
        task = store.create(authority, "two bounded steps", "create")
        task_id = task["task_id"]
        context = self.c.context()
        store.mutate(task_id, lambda t: t.update(ex_session=context["ex_session"]))
        calls, received, acknowledgments = [], [], []
        async def request(robot, route, method, payload):
            calls.append(method)
            reply = await channel.request(method, payload, peer=b"trusted", timeout_sec=2)
            return reply.payload
        async def turn_sync(payload):
            reply = await channel.request("interaction.task.turn", payload, peer=b"trusted", timeout_sec=2)
            if reply.payload.get("ok") is not True:
                raise RuntimeError("binding refused")
        coordinator = load("task_coordinator").TaskCoordinator(store, request, turn_sync=turn_sync)
        cleanup.push_async_callback(coordinator.close)
        async def feedback(peer, envelope, binary):
            fact = envelope["payload"]
            received.append(fact)
            reply = await coordinator.feedback("robot", "route", fact)
            self.assertIsNone(binary)
            self.assertIs(reply.get("ok"), True)
            self.assertEqual(reply.get("ex_session"), fact["ex_session"])
            self.assertIs(type(reply.get("acked_event_seq")), int)
            self.assertEqual(reply["acked_event_seq"], fact["event_seq"])
            acknowledgments.append(reply["acked_event_seq"])
            return reply
        channel.register_handler("decision.feedback", feedback)
        try:
            for _ in range(100):
                if channel.online_peers(): break
                await asyncio.sleep(0.01)
            self.assertTrue(channel.online_peers())
            plan_turn = store.begin_turn(task_id)
            store.save_plan(plan_turn, [
                {"step_id": "s1", "intent": "First step", "completion_condition": "Observed success"},
                {"step_id": "s2", "intent": "Second step", "completion_condition": "Observed success"}], 0)
            coordinator.finish(plan_turn, "waiting_input")
            for index in range(2):
                turn = store.begin_turn(task_id)
                await coordinator.sync_turn(task_id, "bind", turn=turn)
                context = await request("robot", "route", "decision.context.get", {"schema_version": 1})
                result = await coordinator.submit(turn, {"step_id": "s" + str(index + 1),
                    "goal_text_en": "Perform bounded offline action.", "allowed_actions": ["arm.move.v1"],
                    "parameters": {"arm.move.v1": {"meters": 1}},
                    "completion": {"required_success_actions": ["arm.move.v1"]}, "lease_ms": 10000}, context, "submit")
                self.assertTrue(result["ok"])
                coordinator.finish(turn, "waiting_feedback")
                for _ in range(200):
                    if len(self.owners["arm"].commands) > index: break
                    await asyncio.sleep(0.005)
                cid = self.owners["arm"].commands[index].command_id
                for _ in range(100):
                    if self.server.action_ledger.get(cid).result(1).status == "accepted": break
                    await asyncio.sleep(0.005)
                self.server.action_dispatcher.report(cid, OwnerBinding("arm", 1), "running").result(1)
                self.server.action_dispatcher.report(cid, OwnerBinding("arm", 1), "succeeded").result(1)
                for _ in range(300):
                    await asyncio.sleep(0.01)
                    if (store.get(task_id)["active_step"] == index + 1
                            and len(received) >= 4 * (index + 1) and not self.c.journal.pending()):
                        break
                await coordinator.sync(task_id)
                self.assertEqual(store.get(task_id)["active_step"], index + 1)
                self.assertIsNone(store.get(task_id)["current_goal"])
            for _ in range(100):
                if not self.c.journal.pending(): break
                await asyncio.sleep(0.01)
            self.assertGreaterEqual(len(received), 8)
            journal_events = self.c.journal.events(0)["events"]
            expected_sequences = {event["event_seq"] for event in journal_events}
            received_sequences = {fact["event_seq"] for fact in received}
            self.assertGreaterEqual(len(received_sequences), 8)
            self.assertEqual(received_sequences, expected_sequences)
            self.assertEqual(set(acknowledgments), expected_sequences)
            for event in journal_events:
                fact = next(fact for fact in received if fact["event_seq"] == event["event_seq"])
                self.assertEqual(fact, {"schema_version": 1, **{
                    key: event[key] for key in ("ex_session", "task_id", "goal_id", "goal_revision",
                                                "event_seq", "status", "reason_code", "details")}})
            terminal_facts = [fact for fact in self.c.journal.snapshot()["goal_summaries"]]
            self.assertEqual(len(terminal_facts), 2)
            for fact in terminal_facts:
                self.assertEqual(fact["status"], "succeeded")
                proof = fact["details"]["completion_evidence"]
                self.assertIs(proof["verified"], True)
                self.assertEqual(proof["goal_id"], fact["goal_id"])
                self.assertEqual(proof["goal_revision"], fact["goal_revision"])
                self.assertEqual(proof["required_actions"], ["arm.move.v1"])
                self.assertEqual(proof["commands"][0]["status"], "succeeded")
            self.assertEqual(len(self.owners["arm"].commands), 2)
            self.assertEqual(self.s.goals.revision, 2)
            self.assertEqual([s["status"] for s in store.get(task_id)["steps"]], ["completed", "completed"])
            self.assertGreaterEqual(calls.count("decision.state.get"), 2)
            for _ in range(100):
                if not self.c.journal.pending(): break
                await asyncio.sleep(0.01)
            self.assertEqual(self.c.journal.pending(), [])
        finally:
            await cleanup.aclose()
