"""Fixture mechanics only: no EX imports or real-composition acceptance claims."""
from __future__ import annotations

import ast
import asyncio
import copy
import threading
import unittest
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_astrbotex_interaction.task_contracts import ActionCommand


def helper_tree():
    return ast.parse(Path(__file__).with_name("host_decision_helper.py").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Proof:
    command_id: str
    stopped: bool
    source: str
    reference: str


def mock_plugin(actions):
    literal = next(node.value.value for node in helper_tree().body
                   if isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id == "MOCK_SOURCE" for target in node.targets))
    tree = ast.parse(literal)
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom)]
    namespace = {"StopEvidence": Proof}
    exec(compile(tree, "<joint-mock-fixture>", "exec"), namespace)
    return namespace["Plugin"](SimpleNamespace(actions=actions))


class DeviceStopFixtureTests(unittest.TestCase):
    def test_actor_cancel_returns_before_device_proof_and_join_drains(self):
        reported = threading.Event()
        reports = []
        def report(command_id, status, **kwargs):
            reports.append((command_id, status, kwargs["stop_evidence"]))
            reported.set()
            result = Future()
            result.set_result(None)
            return result
        plugin = mock_plugin(SimpleNamespace(report=report))
        plugin.cancel_release.clear()
        try:
            self.assertEqual(plugin.on_action_cancel("command-1", "update"), "requested")
            self.assertTrue(plugin.cancel_entered.is_set())
            self.assertFalse(reported.is_set(), "Actor return must not imply device stopped")
            self.assertEqual(len(plugin.stop_threads), 1)
        finally:
            plugin.cancel_release.set()
            plugin.join_stops()
        self.assertEqual(reports, [("command-1", "canceled", Proof("command-1", True, "joint_mock", "parked-command-1"))])
        self.assertFalse(any(worker.is_alive() for worker in plugin.stop_threads))
        self.assertEqual(plugin.parked['command-1'], reports[0][2])
        self.assertEqual(plugin.on_action_cancel('command-1', 'duplicate'), 'requested')
        plugin.join_stops()
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(plugin.stop_threads), 1)
        failed_proof = Proof('failed-command', True, 'joint_mock', 'failed-parked-failed-command')
        plugin.device_stopped(failed_proof)
        self.assertEqual(plugin.on_action_cancel('failed-command', 'after-failure'), 'requested')
        plugin.join_stops()
        self.assertEqual(plugin.parked['failed-command'], failed_proof)
        self.assertEqual(len(reports), 1, 'already parked FAILED must not get a CANCELED report')
        self.assertEqual(len(plugin.stop_threads), 1)
        with self.assertRaisesRegex(ValueError, 'stop identity changed'):
            plugin.device_stopped(Proof('failed-command', True, 'joint_mock', 'another-proof'))

    def test_missing_proof_does_not_report_and_worker_error_is_not_swallowed(self):
        def rejected(*args, **kwargs):
            raise RuntimeError("SDK rejected actual report")
        plugin = mock_plugin(SimpleNamespace(report=rejected))
        plugin.stop_proof = False
        self.assertEqual(plugin.on_action_cancel("command-1", "stop"), "requested")
        plugin.join_stops()
        self.assertEqual(plugin.stop_threads, [])
        plugin.stop_proof = True
        plugin.on_action_cancel("command-2", "stop")
        with self.assertRaisesRegex(RuntimeError, "SDK rejected actual report"):
            plugin.join_stops()
        self.assertFalse(any(worker.is_alive() for worker in plugin.stop_threads))


class StartedFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_started_rechecks_goal_revision_index_and_live_ledger(self):
        method = next(node for cls in helper_tree().body if isinstance(cls, ast.ClassDef)
                      and cls.name == "JointHarness" for node in cls.body
                      if isinstance(node, ast.AsyncFunctionDef) and node.name == "started")
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     "<started-fixture>", "exec"), namespace)
        old = {"payload": {"goal_id": "old-failed"}, "revision": 2, "feedback_status": "failed"}
        new = {"payload": {"goal_id": "repair"}, "revision": 3, "feedback_status": "accepted"}
        task = {"active_step": 1, "turn_id": None, "plan_revision": 1,
                "ex_session": "ex", "current_goal": old}
        def command(command_id, goal_id, revision):
            return ActionCommand(command_id, "ex", goal_id, revision, "decision", "joint_mock", 1,
                                 "joint_mock.step.v1", "start", {}, 10000)
        old_command = command("old", "old-failed", 2)
        wrong_revision = command("wrong-revision", "repair", 2)
        foreign_task = command("foreign-task", "repair", 3)
        new_command = command("new", "repair", 3)
        self.assertFalse(hasattr(new_command, "task_id"))
        reads = []
        event_reads = []
        def get(command_id):
            reads.append(command_id)
            result = Future()
            result.set_result(SimpleNamespace(command_id=command_id, status="accepted",
                                             event_seq=10 if command_id == "foreign-task" else 11))
            return result
        def events(*, after_seq, limit):
            event_reads.append((after_seq, limit))
            cid = "foreign-task" if after_seq == 9 else "new"
            result = Future()
            result.set_result((SimpleNamespace(event_seq=after_seq + 1, command_id=cid,
                task_id="wrong-task" if cid == "foreign-task" else "task", ex_session="ex",
                goal_id="repair", goal_revision=3, status="accepted"),))
            return result
        async def wait(predicate, label):
            self.assertFalse(predicate(), "old failed goal must not be latched")
            task.update(plan_revision=2, current_goal=new, active_step=0)
            self.assertFalse(predicate(), "wrong step index must not be returned")
            task["active_step"] = 1
            self.assertTrue(predicate())
            await asyncio.sleep(0)
        harness = SimpleNamespace(task=lambda task_id: copy.deepcopy(task), wait=wait,
            owner=SimpleNamespace(starts=[old_command, wrong_revision, foreign_task, new_command]),
            server=SimpleNamespace(action_ledger=SimpleNamespace(get=get, events=events)))
        command = await namespace["started"](harness, "task", 1, plan_revision=2, after_goal_id="old-failed")
        self.assertIs(command, new_command)
        self.assertEqual(reads, ["foreign-task", "new"])
        self.assertEqual(event_reads, [(9, 1), (10, 1)])
