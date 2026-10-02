from __future__ import annotations

import copy
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from validation_drivers import evaluate_jev_live as live
from validation_drivers import evaluate_jev_offline as offline
from astrbot_ex.core.decision.backends import jev
from astrbot_ex.core.decision.models import DecisionSnapshot


class LiveEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes, cls.manifest = offline.load_corpus()

    def reply(self, body, *, choice_kind="start", confidence=1.0):
        request = offline.strict_json(body.decode())
        answers = {}
        for owner in request["state"]["snapshot"]["owners"]:
            options = [c for c in owner["candidates"] if c["eligible"]]
            chosen = next((c for c in options if c["kind"] == choice_kind), options[0])["option_id"]
            answers[owner["owner"]] = {"type": "choice", "choice": chosen, "confidence": confidence,
                                      "probabilities": {c["option_id"]: int(c["option_id"] == chosen) for c in options}}
        return jev.HTTPReply(200, offline.canonical({"model": jev.PINNED_MODEL, "answers": answers,
                                                     "usage": {"input_tokens": 400, "output_tokens": 45}}))

    def setup_backend(self, directory, inner, *, deadline_ms=1500):
        ledger = live.Ledger(directory)
        transport = live.RecordingTransport(ledger, inner=inner)
        backend = jev.JevBackend(jev.JevConfig(mode="shadow", allow_live_http=True, deadline_ms=deadline_ms,
                                              min_interval_ms=0, max_retries=0),
                                 transport=transport, secret_provider=lambda: "mock-secret-only")
        return backend, transport, ledger

    def test_context_reserve_310_calls_under_one_dollar_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            ledger = live.Ledger(path)
            for i in range(310):
                ledger.reserve("hash", 9000, "mock")
            self.assertLess(310 * live.RESERVE_TOKENS * live.PRICE, live.MAX_USD)
            with self.assertRaises(live.BudgetStop):
                ledger.reserve("hash", 9000, "mock")
            resumed = live.Ledger(path)
            self.assertEqual(resumed.report()["attempted_http_calls"], 310)
            with self.assertRaises(live.BudgetStop):
                resumed.reserve("hash", 9000, "mock")

    def test_budget_rejects_before_http_and_failures_never_refund(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = live.Ledger(Path(temp), max_calls=1)
            sends = []
            def inner(*args):
                sends.append(True)
                raise RuntimeError("mock-secret-only private reasoning")
            transport = live.RecordingTransport(ledger, inner=inner)
            backend = jev.JevBackend(jev.JevConfig(mode="shadow", allow_live_http=True, min_interval_ms=0),
                                     transport=transport, secret_provider=lambda: "mock-secret-only")
            first = live.one_decision(backend, transport, self.scenes[0]["snapshot"])
            self.assertEqual(first["reject_code"], "transport_failure")
            transport.last_started = float("-inf")
            with self.assertRaises(live.BudgetStop):
                live.one_decision(backend, transport, self.scenes[0]["snapshot"])
            self.assertEqual(len(sends), 1)
            self.assertEqual(ledger.report()["attempted_http_calls"], 1)
            self.assertNotIn("mock-secret-only", ledger.path.read_text())
            self.assertNotIn("private reasoning", ledger.path.read_text())

    def test_projection_discards_secret_headers_reasoning_unknown_ids(self):
        snapshot = self.scenes[0]["snapshot"]
        backend = jev.JevBackend()
        _, body = backend._request(DecisionSnapshot.parse(snapshot), backend.config)
        request = offline.strict_json(body.decode())
        hostile = {"model": "Bearer fake-private-token", "Authorization": "fake-private-token",
                   "reasoning": "hidden-reasoning-marker", "answers": {"base": {
                       "type": "choice", "choice": "fake-private-token", "confidence": 0.4,
                       "probabilities": {"fake-private-token": 1}}}, "usage": {"input_tokens": 42, "output_tokens": 3}}
        projection = live.response_projection(offline.canonical(hostile), request)
        text = json.dumps(projection)
        self.assertNotIn("fake-private-token", text)
        self.assertNotIn("hidden-reasoning-marker", text)
        self.assertNotIn("Authorization", text)
        self.assertEqual(projection["answers"]["base"]["choice"], "[unknown_option]")
        self.assertEqual(projection["usage"]["input_tokens"], 42)
        self.assertEqual(live.response_projection(b'{"a":1,"a":2}', request)["json_status"], "invalid")
        self.assertEqual(live.response_projection(b'{"a":NaN}', request)["json_status"], "invalid")

    def test_raw_bad_selection_is_not_hidden_by_low_confidence_override(self):
        scene = next(s for s in self.scenes if s["category"] == "stale")
        with tempfile.TemporaryDirectory() as temp:
            backend, transport, _ = self.setup_backend(Path(temp), lambda body, *a: self.reply(body, confidence=0.2))
            result = live.one_decision(backend, transport, scene["snapshot"])
            raw = offline.score_scene(scene, result["raw_choices"], result["elapsed_ms"])
            post = offline.score_scene(scene, result["post_override_choices"], result["elapsed_ms"])
            self.assertGreater(raw["bad_choice_count"], 0)
            self.assertEqual(post["bad_choice_count"], 0)
            self.assertEqual(result["adapter_record"]["conservative_overrides"], 2)
            self.assertFalse(result["execution_allowed"])
            self.assertEqual(result["http"]["response"]["answers"]["arm"]["confidence"], 0.2)

    def test_rejected_ineligible_unknown_owner_model_retains_raw_usage(self):
        for mutation in ("ineligible", "unknown", "owner", "model", "probabilities"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                def inner(body, *args):
                    reply = self.reply(body)
                    value = offline.strict_json(reply.body.decode())
                    if mutation == "ineligible":
                        snap = offline.strict_json(body.decode())["state"]["snapshot"]
                        value["answers"]["arm"]["choice"] = next(c["option_id"] for o in snap["owners"] if o["owner"] == "arm" for c in o["candidates"] if not c["eligible"])
                    elif mutation == "unknown":
                        value["answers"]["arm"]["choice"] = "mock-private-token"
                    elif mutation == "owner":
                        del value["answers"]["arm"]
                    elif mutation == "model":
                        value["model"] = "wrong-model"
                    else:
                        value["answers"]["arm"]["probabilities"] = {}
                    return jev.HTTPReply(200, offline.canonical(value))
                backend, transport, _ = self.setup_backend(Path(temp), inner)
                result = live.one_decision(backend, transport, self.scenes[0]["snapshot"])
                self.assertIsNotNone(result["reject_code"])
                self.assertIsNone(result["post_override_choices"])
                self.assertEqual(result["http"]["response"]["usage"]["input_tokens"], 400)
                self.assertNotIn("mock-private-token", json.dumps(result))
                if mutation == "ineligible":
                    self.assertTrue(offline.score_scene(self.scenes[0], result["raw_choices"], 0)["invalid_output"])

    def test_http_401_429_529_redirect_never_retry(self):
        for status in (401, 429, 529, 302):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                sends = []
                def inner(*args):
                    sends.append(True)
                    return jev.HTTPReply(status, b'{"error":"Bearer mock-private-token"}', "99")
                backend, transport, ledger = self.setup_backend(Path(temp), inner)
                result = live.one_decision(backend, transport, self.scenes[0]["snapshot"])
                self.assertEqual(len(sends), 1)
                self.assertEqual(ledger.report()["attempted_http_calls"], 1)
                self.assertIsNone(result["post_override_choices"])
                self.assertNotIn("mock-private-token", json.dumps(result))

    def test_deadline_late_reply_quarantined_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            def inner(body, *args):
                time.sleep(0.08)
                return self.reply(body)
            backend, transport, _ = self.setup_backend(Path(temp), inner, deadline_ms=20)
            result = live.one_decision(backend, transport, self.scenes[0]["snapshot"])
            self.assertEqual(result["reject_code"], "deadline_exceeded")
            self.assertLess(result["elapsed_ms"], 75)
            self.assertIsNone(result["post_override_choices"])
            self.assertIsNotNone(result["raw_choices"])
            self.assertFalse(backend._worker.is_alive())

    def test_rate_limiter_wait_inside_original_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            backend, transport, ledger = self.setup_backend(Path(temp), lambda *a: calls.append(True))
            transport.last_started = time.monotonic()
            result = live.one_decision(backend, transport, self.scenes[0]["snapshot"])
            # Callable returns invalid reply, but HTTP start was globally spaced.
            self.assertGreaterEqual(result["http"]["transport_elapsed_ms"], 480)
            self.assertEqual(len(calls), 1)
            transport.last_started = time.monotonic()
            backend.reconfigure(jev.JevConfig(mode="shadow", allow_live_http=True, deadline_ms=20, min_interval_ms=0))
            result = live.one_decision(backend, transport, self.scenes[0]["snapshot"])
            self.assertEqual(result["reject_code"], "deadline_exceeded")
            self.assertEqual(ledger.report()["attempted_http_calls"], 1)

    def test_usage_over_context_halts_before_next_call(self):
        with tempfile.TemporaryDirectory() as temp:
            def inner(body, *args):
                value = offline.strict_json(self.reply(body).body.decode())
                value["usage"]["input_tokens"] = 65537
                return jev.HTTPReply(200, offline.canonical(value))
            backend, transport, ledger = self.setup_backend(Path(temp), inner)
            with self.assertRaises(live.BudgetStop):
                live.one_decision(backend, transport, self.scenes[0]["snapshot"])
            self.assertEqual(ledger.report()["attempted_http_calls"], 1)
            self.assertEqual(live.measured_usage(transport.records)["input_tokens"], 65537)

    def test_all_300_mocked_rows_same_snapshot_no_labels_no_best_of(self):
        with tempfile.TemporaryDirectory() as temp:
            seen = []
            def inner(body, *args):
                request = offline.strict_json(body.decode())
                snap = request["state"]["snapshot"]
                self.assertNotIn("labels", snap)
                self.assertNotIn("split", snap)
                self.assertNotIn("category", snap)
                seen.append(offline.digest(snap))
                return self.reply(body)
            backend, transport, _ = self.setup_backend(Path(temp), inner)
            # Test-only disable wall waits; real harness always uses 500ms.
            original = transport.__class__.__call__
            def unpaced(instance, *args):
                instance.last_started = float("-inf")
                return original(instance, *args)
            with patch.object(live.RecordingTransport, "__call__", unpaced), patch.object(socket, "socket", side_effect=AssertionError("mock suite no cloud")):
                result = live.evaluate(backend, transport, self.scenes, self.manifest)
            self.assertTrue(result["complete"])
            self.assertEqual(len(result["decisions"]), 300)
            self.assertEqual(seen, [offline.digest(DecisionSnapshot.parse(s["snapshot"]).to_dict()) for s in self.scenes] * 3)
            self.assertEqual(seen, [c["input_snapshot_sha256"] for c in result["decisions"]])
            for name in result["rows"]:
                self.assertEqual(len(result["rows"][name]), 300)
                self.assertEqual(result["summaries"][name]["development"]["scene_runs"], 240)
                self.assertEqual(result["summaries"][name]["holdout"]["scene_runs"], 60)

    def test_report_exclusive_creation_and_usage_null_not_fake_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "result.json"
            live.write_new(p, {"actual_bill": None})
            with self.assertRaises(FileExistsError):
                live.write_new(p, {})
        self.assertIsNone(live.measured_usage([])["input_tokens"])
        self.assertIsNone(live.measured_usage([])["actual_bill"])


if __name__ == "__main__":
    unittest.main()
