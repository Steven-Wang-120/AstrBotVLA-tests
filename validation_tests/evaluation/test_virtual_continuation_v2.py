"""Continuation correction tests; fabricated saved responses, never network."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from validation_drivers import continue_virtual_closed_loop_v2 as c
from validation_drivers import evaluate_virtual_closed_loop_v2 as v
from validation_drivers import evaluate_llm_live as llm
from validation_drivers import b07_virtual_tasks_v2 as m


class ContinuationTests(unittest.TestCase):
    def provider(self):
        return llm.Provider("https://api.deepseek.com", "deepseek-v4-flash", "openai", "fake-no-key",
                            pricing={"input_cost_per_million": ".14", "output_cost_per_million": ".28",
                                     "cache_read_cost_per_million": ".0028", "cache_creation_cost_per_million": "0",
                                     "basis": "TEST_ONLY"})

    def test_saved_real_alias_plan_revalidation_no_retry(self):
        provider = self.provider()
        payload = m.planner_input(m.tasks()[0])
        body, _ = v.request_body(provider, m.PLAN_SYSTEM, payload)
        with tempfile.TemporaryDirectory() as temp:
            session = c.ContinuedSession(Path(temp), provider, "fake-jev-not-a-key", "TEST_ONLY")
            session.context = {"repeat": 1, "task_id": "virtual-01", "arm": "llm_only"}
            session.prior_hashes = {"llm-001": "TEST_ONLY_SHA"}
            session.prior = {(1, "virtual-01", "llm_only"): {**session.context,
                "service": "llm", "phase": "planner", "call_ref": "llm-001", "error": None,
                "finish_reason": "stop", "response_model": "deepseek-flash",
                "raw_final_text": '{"steps":["prepare","transform","finish"]}',
                "input_sha256": c.hashlib.sha256(body).hexdigest(), "elapsed_ms": 100,
                "validation_error": "response_model_mismatch",
                "usage": {"prompt_tokens": 100, "completion_tokens": 12}}}
            with patch.object(llm, "call_http", side_effect=AssertionError("must not retry")):
                value, error, refs = session.llm_request("planner", m.PLAN_SYSTEM, payload)
            self.assertIsNone(error)
            self.assertEqual(m.parse_plan(value), ["prepare", "transform", "finish"])
            self.assertEqual(refs, ["llm-001"])
            self.assertEqual(session.budget.count, 0)
            self.assertIn("OLD configured pricing", session.records[0]["cost"]["basis"])

    def test_recovered_input_mismatch_rejected(self):
        provider = self.provider()
        with tempfile.TemporaryDirectory() as temp:
            session = c.ContinuedSession(Path(temp), provider, "fake-jev-not-a-key", "TEST_ONLY")
            session.context = {"repeat": 1, "task_id": "virtual-01", "arm": "llm_only"}
            session.prior = {(1, "virtual-01", "llm_only"): {"input_sha256": "wrong"}}
            with self.assertRaises(llm.Rejected):
                session.llm_request("planner", m.PLAN_SYSTEM, m.planner_input(m.tasks()[0]))

    def test_new_alias_call_keeps_request_model_cap_and_thinking(self):
        provider = self.provider()
        with tempfile.TemporaryDirectory() as temp:
            session = c.ContinuedSession(Path(temp), provider, "fake-jev-not-a-key", "TEST_ONLY")
            session.context = {"repeat": 1, "task_id": "virtual-01", "arm": "llm_only"}
            session.prior, session.prior_hashes = {}, {}
            response = {"error": None, "finish_reason": "stop", "response_model": "deepseek-flash",
                        "raw_final_text": '{"steps":["prepare","transform","finish"]}',
                        "usage": {"prompt_tokens": 100, "completion_tokens": 12}, "elapsed_ms": 100}
            with patch.object(llm, "call_http", return_value=response) as call:
                _, error, _ = session.llm_request("planner", m.PLAN_SYSTEM, m.planner_input(m.tasks()[0]))
            request = json.loads(call.call_args.args[1])
            self.assertIsNone(error)
            self.assertEqual(request["model"], "deepseek-v4-flash")
            self.assertEqual(request["thinking"], {"type": "disabled"})
            self.assertEqual(request["max_tokens"], 512)
            self.assertEqual(session.budget.count, 1)

    def test_missing_saved_response_retains_failure_without_selector(self):
        with tempfile.TemporaryDirectory() as temp:
            session = c.ContinuedSession(Path(temp), self.provider(), "fake-jev-not-a-key", "TEST_ONLY")
            session.context = {"repeat": 1, "task_id": "virtual-01", "arm": "hybrid"}
            payload = m.planner_input(m.tasks()[0])
            body, _ = v.request_body(self.provider(), m.PLAN_SYSTEM, payload)
            session.prior_hashes = {"llm-001": "TEST_ONLY_SHA"}
            session.prior = {(1, "virtual-01", "hybrid"): {**session.context, "service": "llm",
                "call_ref": "llm-001", "input_sha256": c.hashlib.sha256(body).hexdigest(),
                "error": "interrupted_response_not_observed_no_retry", "elapsed_ms": None,
                "response_model": None, "usage": None}}
            with patch.object(llm, "call_http", side_effect=AssertionError("must not retry")):
                value, error, _ = session.llm_request("planner", m.PLAN_SYSTEM, payload)
            self.assertIsNone(value)
            self.assertEqual(error, "interrupted_response_not_observed_no_retry")
            self.assertIsNone(session.records[0]["elapsed_ms"])


if __name__ == "__main__":
    unittest.main()
