"""Offline meaningful transitions and fail-closed paths; fabricated selectors only."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from validation_drivers import b07_virtual_tasks_v2 as m
from validation_drivers import evaluate_virtual_closed_loop_v2 as v
from validation_drivers import evaluate_llm_live as llm
from astrbot_ex.core.decision.models import DecisionSnapshot


class VirtualMachineTests(unittest.TestCase):
    def setUp(self):
        self.task = m.tasks()[0]
        self.plan = ["prepare", "transform", "finish"]

    def fixed(self, kinds):
        iterator = iter(kinds)
        return lambda snapshot: ({"worker": "worker-" + next(iterator)}, None, [])

    def test_real_three_step_state_transitions(self):
        row = m.run_arm(self.task, self.plan, self.fixed(["start"] * 3), "test")
        self.assertTrue(row["success"])
        self.assertEqual([s["after"]["phase"] for s in row["steps"]], ["ready", "processed", "completed"])
        self.assertEqual(row["final_state"]["completed_item"], self.task["initial_state"]["bound_item"])
        self.assertEqual(self.task["initial_state"]["phase"], "raw")

    def test_goal_order_is_not_scored_or_repaired_by_oracle(self):
        row = m.run_arm(self.task, ["finish", "prepare", "transform"], self.fixed(["start"] * 3), "test")
        self.assertFalse(row["success"])
        self.assertEqual(len(row["steps"]), 1)
        self.assertEqual(row["final_state"]["phase"], "raw")

    def test_planner_failure_zero_selector_calls(self):
        selector = unittest.mock.Mock(side_effect=AssertionError("must not call"))
        row = m.run_arm(self.task, None, selector, "test")
        selector.assert_not_called()
        self.assertFalse(row["success"])
        self.assertEqual(row["steps"], [])

    def test_unknown_wrong_owner_ineligible_and_extra_fail_closed(self):
        for choice in (None, {}, {"worker": "unknown"}, {"other": "worker-start"},
                       {"worker": "worker-start", "extra": "x"}, {"worker": []}):
            state, reason = m.transition(self.task, self.task["initial_state"], "prepare", choice)
            self.assertEqual(state["phase"], "raw")
            self.assertEqual(reason, "invalid_or_ineligible_selection")
        ambiguous = m.tasks()[4]
        state, reason = m.transition(ambiguous, ambiguous["initial_state"], "prepare", {"worker": "worker-start"})
        self.assertEqual(state["observation"], "ambiguous")
        self.assertEqual(reason, "invalid_or_ineligible_selection")

    def test_wait_ambiguity_stale_missing_are_actual_observation_ticks(self):
        for index in (4, 8):
            row = m.run_arm(m.tasks()[index], self.plan, self.fixed(["wait", "start", "start"]), "test")
            self.assertTrue(row["success"])
            self.assertEqual(row["steps"][0]["before"]["phase"], "raw")
            self.assertEqual(row["steps"][0]["after"]["observation"], "fresh")
        row = m.run_arm(m.tasks()[11], self.plan, self.fixed(["wait"] * 3), "test")
        self.assertFalse(row["success"])
        self.assertEqual(row["final_state"]["phase"], "raw")
        self.assertEqual(row["final_state"]["observation"], "missing")
        self.assertEqual(len(row["steps"]), 3)

    def test_keep_retains_command_not_restart(self):
        row = m.run_arm(m.tasks()[2], self.plan, self.fixed(["keep", "start", "start"]), "test")
        self.assertTrue(row["success"])
        row = m.run_arm(m.tasks()[16], self.plan, self.fixed(["keep"] * 3), "test")
        self.assertFalse(row["success"])
        self.assertEqual(row["final_state"]["running"], "failed_preparation")

    def test_conflict_cancel_and_failure_recovery_require_stop_proof(self):
        for index in (12, 16):
            row = m.run_arm(m.tasks()[index], self.plan, self.fixed(["cancel", "start", "start"]), "test")
            self.assertTrue(row["success"])
            self.assertIsNone(row["steps"][0]["after"]["running"])
        for index in (15, 19):
            row = m.run_arm(m.tasks()[index], self.plan, self.fixed(["cancel"] * 3), "test")
            self.assertFalse(row["success"])
            self.assertEqual(row["failure_reason"], "cancellation_unproven")
            self.assertFalse(row["final_state"]["stopped_proven"])
            self.assertEqual(len(row["steps"]), 1)

    def test_timeout_invalid_and_replan_cannot_be_success(self):
        row = m.run_arm(self.task, self.plan, lambda s: (None, "timeout", ["test"]), "test")
        self.assertFalse(row["success"])
        self.assertEqual(len(row["steps"]), 1)
        row = m.run_arm(self.task, self.plan, self.fixed(["request_replan"] * 3), "test")
        self.assertFalse(row["success"])
        self.assertEqual(len(row["steps"]), 1)

    def test_success_checks_bound_identity_and_stopped_evidence(self):
        row = m.run_arm(self.task, self.plan, self.fixed(["start"] * 3), "test")
        for field, value in (("completed_item", "invented"), ("stopped_proven", False),
                             ("running", "late"), ("failure", "timeout")):
            state = copy.deepcopy(row["final_state"])
            state[field] = value
            self.assertFalse(m.success(self.task, state))

    def test_fixed_spec_and_snapshot_contract_no_expected_labels(self):
        frozen = json.loads(v.SPEC.read_text())
        self.assertEqual(frozen, {"version": m.VERSION, "tasks": m.tasks()})
        self.assertEqual(len(frozen["tasks"]), 20)
        self.assertEqual(len({m.digest(t["initial_state"]) for t in m.tasks()}), 20)
        for task in m.tasks():
            payload = m.planner_input({**task, "expected_answers": "SECRET_ORACLE", "labels": "SECRET_ORACLE"})
            snap = m.snapshot(task, task["initial_state"], "prepare", "test")
            DecisionSnapshot.parse(snap)
            text = m.canonical({"planner": payload, "selector": snap}).decode()
            self.assertNotIn("SECRET_ORACLE", text)
            for word in ("expected_answers", "labels", "acceptable_joint_choices", "desired_operation"):
                self.assertNotIn(word, text)

    def test_plan_schema_rejects_invented_parameters_and_targets(self):
        self.assertEqual(m.parse_plan({"steps": self.plan}), self.plan)
        for value in ({"steps": self.plan, "target": "invented"}, {"steps": ["prepare"]},
                      {"steps": ["prepare", "invented", "finish"]}, None, {"steps": [{}, {}, {}]}):
            with self.assertRaises(ValueError):
                m.parse_plan(value)


class TransportBudgetTests(unittest.TestCase):
    def provider(self):
        return llm.Provider("https://api.deepseek.com", "deepseek-v4-flash", "openai", "test-secret-not-real",
                            pricing={"input_cost_per_million": ".14", "output_cost_per_million": ".28",
                                     "cache_read_cost_per_million": ".0028", "cache_creation_cost_per_million": "0",
                                     "basis": "test pricing"})

    def test_explicit_nonthinking_fixed_cap_first_request_and_no_oracle(self):
        provider = self.provider()
        body, size = v.request_body(provider, m.PLAN_SYSTEM, m.planner_input(m.tasks()[0]))
        request = json.loads(body)
        self.assertEqual(request["thinking"], {"type": "disabled"})
        self.assertEqual(request["max_tokens"], 512)
        self.assertNotIn(provider.secret, body.decode())
        self.assertLess(size, v.MAX_INPUT)
        with self.assertRaises(llm.Rejected):
            v.request_body(provider, "x", {"too_big": "x" * (v.MAX_INPUT + 1)})

    def test_complete_run_reservations_and_caps_no_failure_refunds(self):
        budget = v.LlmBudget(self.provider())
        for _ in range(300):
            budget.reserve()
        self.assertEqual(budget.count, 300)
        self.assertLessEqual(budget.reserved, Decimal(1))
        with self.assertRaises(llm.Rejected):
            budget.reserve()
        with self.assertRaises(llm.Rejected):
            v.LlmBudget(replace(self.provider(), pricing=None))
        harness = v.load_jev_harness()
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp) / "validation_drivers/evaluate_jev_live.py"
            local.parent.mkdir()
            local.write_bytes(Path(harness.__file__).read_bytes())
            with patch.object(v, "DEFAULT_JEV_HARNESS", local):
                self.assertEqual(Path(v.load_jev_harness().__file__), local.resolve())
            self.assertEqual(Path(v.load_jev_harness(local).__file__), local.resolve())
            self.assertEqual(v.source_manifest(local)[str(local.resolve())],
                             v.hashlib.sha256(local.read_bytes()).hexdigest())
            self.assertFalse((local.parent / "__pycache__").exists())
            with self.assertRaises(FileNotFoundError):
                v.load_jev_harness(Path(temp) / "missing.py")
            ledger = harness.Ledger(Path(temp), max_calls=180, max_usd=Decimal(1))
            for _ in range(180):
                ledger.reserve("testhash", 1024, "TEST_ONLY")
            with self.assertRaises(harness.BudgetStop):
                ledger.reserve("testhash", 1024, "TEST_ONLY")
            self.assertEqual(ledger.report()["attempted_http_calls"], 180)
            self.assertLessEqual(Decimal(ledger.report()["reserved_cost_upper_bound_usd"]), Decimal(1))

    def test_redaction_and_response_projection_do_not_persist_reasoning(self):
        secret = self.provider().secret
        raw = {"model": "deepseek-v4-flash", "choices": [{"finish_reason": "stop",
               "message": {"content": '{"worker":"worker-wait"}', "reasoning_content": secret}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        projected = llm.public_response(raw, "openai")
        self.assertNotIn(secret, json.dumps(projected))
        self.assertNotIn("reasoning_content", projected)
        self.assertNotIn(secret, json.dumps(llm.redact({"secret": secret}, [secret])))

    def test_only_first_jev_token_is_read(self):
        from io import StringIO
        class FirstTokenOnly(StringIO):
            def __next__(self):
                if self.tell() > 0:
                    raise AssertionError("second token must not be read")
                return super().__next__()
        with patch.object(v, "KEY_FILE", Path("test-only-never-read.keys")), \
             patch.object(Path, "open", return_value=FirstTokenOnly("a" * 32 + "\n" + "b" * 32)):
            self.assertEqual(v.read_first_key(), "a" * 32)

    def test_no_live_without_opt_in_and_no_overwrite(self):
        from argparse import Namespace
        with self.assertRaises(llm.Rejected):
            v.execute(Namespace(allow_live_http=False))
        with patch.object(Path, "exists", return_value=True):
            with self.assertRaises(llm.Rejected):
                v.execute(Namespace(allow_live_http=True))

    def test_session_counts_real_sent_requests_not_phantom_selector(self):
        provider = self.provider()
        with tempfile.TemporaryDirectory() as temp:
            session = v.Session(Path(temp), provider, "test-key-not-real", "TEST_ONLY")
            session.context = {"arm": "llm_only", "repeat": 1, "task_id": "virtual-01"}
            response = {"http_status": 200, "error": None, "response_model": provider.model,
                        "finish_reason": "stop", "raw_final_text": '{"steps":["prepare","transform","finish"]}',
                        "usage": {"prompt_tokens": 20, "completion_tokens": 10}, "elapsed_ms": 5}
            with patch.object(llm, "call_http", return_value=response):
                value, error, refs = session.llm_request("planner", m.PLAN_SYSTEM, m.planner_input(m.tasks()[0]))
            self.assertIsNone(error)
            self.assertEqual(session.budget.count, 1)
            self.assertEqual(len(session.records), 1)
            self.assertEqual(refs, ["llm-001"])
            self.assertEqual(m.parse_plan(value), ["prepare", "transform", "finish"])

    def test_jev_no_transport_does_not_invent_request(self):
        with tempfile.TemporaryDirectory() as temp:
            session = v.Session(Path(temp), self.provider(), "test-key-not-real", "TEST_ONLY")
            session.context = {"arm": "hybrid", "repeat": 1, "task_id": "virtual-01"}
            fake = {"http": None, "reject_code": "invalid_snapshot"}
            with patch.object(session.harness, "one_decision", return_value=fake):
                choice, error, refs = session.jev_request(m.snapshot(m.tasks()[0], m.tasks()[0]["initial_state"], "prepare", "test"))
            self.assertIsNone(choice)
            self.assertEqual(error, "invalid_snapshot")
            self.assertEqual(refs, [])
            self.assertEqual(session.records, [])
            self.assertEqual(session.jev_ledger.reservations, [])


if __name__ == "__main__":
    unittest.main()
