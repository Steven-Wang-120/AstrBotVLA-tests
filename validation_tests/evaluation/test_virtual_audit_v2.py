"""Replay audit synthetic fixtures and tampering tests; no network or key reads."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from validation_drivers import b07_virtual_tasks_v2 as m
from validation_drivers import evaluate_virtual_closed_loop_v2 as v


class ReplayAuditTests(unittest.TestCase):
    def fixture(self, path, error=None):
        task = m.tasks()[0]
        plan = ["prepare", "transform", "finish"]
        records = [{"call_ref": "planner", "phase": "planner", "repeat": 1, "arm": "llm_only",
                    "task_id": task["task_id"], "validation_error": None}]
        n = 0
        def selector(snapshot):
            nonlocal n
            n += 1
            choice = {"worker": "worker-start"}
            # A timeout with an otherwise public selected choice must fail, not replay as success.
            records.append({"call_ref": f"selector-{n}", "repeat": 1, "arm": "llm_only",
                            "task_id": task["task_id"], "validation_error": error})
            return choice, error, [f"selector-{n}"]
        row = m.run_arm(task, plan, selector, "TEST_ONLY")
        row.update(repeat=1, arm="llm_only", model_calls=len(records))
        report = {"tasks": [row]}
        (path / "report.json").write_text(json.dumps(report), encoding="utf-8")
        for i, record in enumerate(records, 1):
            (path / f"call-{i:04d}.json").write_text(json.dumps(record), encoding="utf-8")
        return report

    def test_multistep_replay_and_timeout_with_present_choice(self):
        for error in (None, "timeout"):
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp)
                report = self.fixture(path, error)
                result = v.replay_audit(path)
                self.assertEqual(result["status"], "passed")
                self.assertEqual(report["tasks"][0]["success"], error is None)

    def test_terminal_and_real_request_count_tampering_rejected(self):
        for field, value in (("success", False), ("model_calls", 99), ("initial_state_sha256", "wrong")):
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp)
                report = self.fixture(path)
                report["tasks"][0][field] = value
                (path / "report.json").write_text(json.dumps(report), encoding="utf-8")
                with self.assertRaises(ValueError):
                    v.replay_audit(path)

    def test_intermediate_transition_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            report = self.fixture(path)
            report["tasks"][0]["steps"][0]["after"]["phase"] = "completed"
            (path / "report.json").write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(ValueError):
                v.replay_audit(path)


if __name__ == "__main__":
    unittest.main()
