"""New CLI cost/latency regressions only; fake usage and no real HTTP."""
from __future__ import annotations

import copy
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from validation_drivers import evaluate_virtual_closed_loop_v2 as v
from validation_drivers import evaluate_llm_live as llm
from validation_drivers import b07_virtual_tasks_v2 as machine


class VirtualMetricsTests(unittest.TestCase):
    def provider(self):
        return llm.Provider("https://test.invalid", "test-model", "openai", "fake-test-only",
                            pricing={"input_cost_per_million": ".14", "output_cost_per_million": ".28",
                                     "cache_read_cost_per_million": ".0028", "cache_creation_cost_per_million": "0",
                                     "basis": "test-only configuration"})

    def call(self, usage=None, elapsed_ms=10):
        return {"arm": "hybrid", "repeat": 1, "service": "jev", "usage": usage, "elapsed_ms": elapsed_ms}

    def row(self, elapsed_ms=20):
        return {"arm": "hybrid", "repeat": 1, "category": "normal", "success": True,
                "failure_reason": None, "elapsed_ms": elapsed_ms}

    def test_output_tokens_free_decimal_and_missing_usage_null_total(self):
        calls = [self.call({"input_tokens": 1000, "output_tokens": 99999}) for _ in range(3)]
        expected = float(Decimal(3000) * Decimal(".042") / Decimal(1000000))
        estimate = v.jev_cost_estimate(calls)
        self.assertEqual(estimate["estimated_usd"], expected)
        self.assertEqual(estimate["known_usage_subtotal_usd"], expected)
        self.assertEqual(estimate["missing_usage_calls"], 0)
        summary = v.summarize([self.row()], calls, self.provider())["hybrid"]
        self.assertEqual(summary["jev_estimated_usd"], expected)
        missing = calls + [self.call(None)]
        summary = v.summarize([self.row()], missing, self.provider())["hybrid"]
        self.assertIsNone(summary["jev_estimated_usd"])
        self.assertEqual(summary["jev_known_usage_subtotal_usd"], expected)
        self.assertEqual(summary["jev_missing_usage_calls"], 1)
        for usage in (None, {}, {"output_tokens": 12}, {"input_tokens": True}, {"input_tokens": -1}):
            self.assertIsNone(v.jev_cost_estimate([self.call(usage)])["estimated_usd"])
        self.assertEqual(v.jev_cost_estimate([self.call({"input_tokens": 0, "output_tokens": 999})])["estimated_usd"], 0)

    def test_missing_latency_not_zero_complete_sum_null_and_known_subtotal(self):
        calls = [self.call({"input_tokens": 1}, 40), self.call({"input_tokens": 1}, None),
                 self.call({"input_tokens": 1}, 80)]
        before = copy.deepcopy(calls)
        summary = v.summarize([self.row()], calls, self.provider())["hybrid"]
        self.assertEqual(summary["request_latency_ms"], {"p50": 40, "p95": 80, "max": 80})
        self.assertEqual(summary["unobserved_latency_attempts"], 1)
        self.assertIsNone(summary["sum_request_ms"])
        self.assertEqual(summary["sum_known_request_ms"], 120)
        self.assertEqual(calls, before)
        all_unknown = v.summarize([self.row()], [self.call(elapsed_ms=None)], self.provider())["hybrid"]
        self.assertEqual(all_unknown["request_latency_ms"], {"p50": None, "p95": None, "max": None})
        self.assertIsNone(all_unknown["sum_request_ms"])
        self.assertEqual(all_unknown["sum_known_request_ms"], 0)
        known = v.summarize([self.row()], calls[:1] + calls[2:], self.provider())["hybrid"]
        self.assertEqual(known["sum_request_ms"], known["sum_known_request_ms"])

    def test_missing_task_duration_not_reported_as_complete_total(self):
        summary = v.summarize([self.row(30), self.row(None)], [], self.provider())["hybrid"]
        self.assertEqual(summary["successes"], 2)
        self.assertIsNone(summary["sum_task_end_to_end_ms"])
        self.assertEqual(summary["sum_known_task_end_to_end_ms"], 30)
        self.assertEqual(summary["unobserved_task_latency_attempts"], 1)
        self.assertEqual(summary["task_end_to_end_ms"], {"p50": 30, "p95": 30, "max": 30})

    def test_per_call_jev_record_bills_input_only_no_http(self):
        fake_harness = SimpleNamespace(Ledger=lambda *a, **kw: object(), RecordingTransport=lambda *a: SimpleNamespace())
        task = machine.tasks()[0]
        projection = {"model": "jev-1.13.0", "usage": {"input_tokens": 1000, "output_tokens": 9000}}
        result = {"http": {"call_number": 1, "response": projection, "start_utc": "TEST_ONLY",
                           "request": {}, "status": 200, "transport_error": None},
                  "elapsed_ms": 10, "reject_code": None, "raw_choices": {"worker": "worker-start"},
                  "post_override_choices": {"worker": "worker-start"}, "adapter_record": None}
        fake_harness.one_decision = lambda *a: result
        with tempfile.TemporaryDirectory() as temp, patch.object(v, "load_jev_harness", return_value=fake_harness):
            session = v.Session(Path(temp), self.provider(), "test-only-not-a-key", "TEST_ONLY")
            session.context = {"repeat": 1, "task_id": task["task_id"], "arm": "hybrid"}
            with patch.object(llm, "call_http", side_effect=AssertionError("must not send HTTP")):
                session.jev_request(machine.snapshot(task, task["initial_state"], "prepare", "TEST_ONLY"))
            self.assertEqual(session.records[0]["estimated_usd"], .000042)


if __name__ == "__main__":
    unittest.main()
