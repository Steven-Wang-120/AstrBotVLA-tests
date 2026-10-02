from __future__ import annotations

import argparse
import copy
import http.client
import json
import multiprocessing
import sqlite3
import tempfile
import threading
import time
import unittest
from decimal import Decimal
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from validation_drivers import evaluate_llm_live as live


def stalled_child(pipe, provider, body):
    time.sleep(5)
    pipe.close()


class LiveHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes, cls.manifest = live.frozen.load_corpus()

    def provider(self, **changes):
        values = dict(base_url="https://selected.invalid", model="[free]test-model",
                      protocol="anthropic", secret="synthetic-test-credential", labeled_free=True)
        values.update(changes)
        return live.Provider(**values)

    def test_opt_in_precedes_db_or_http(self):
        opts = argparse.Namespace(allow_live_http=False)
        with patch.object(live, "read_provider", side_effect=AssertionError), \
             patch.object(live, "call_http", side_effect=AssertionError):
            with self.assertRaisesRegex(live.Rejected, "opt_in"):
                live.execute(opts)

    def test_snapshot_only_no_labels_and_same_rule_input(self):
        for scene in self.scenes:
            normalized, schema, message, size = live.make_input(scene["snapshot"])
            self.assertLessEqual(size, live.MAX_INPUT_BYTES)
            self.assertEqual(live.frozen.digest(normalized), live.frozen.digest(live.DecisionSnapshot.parse(scene["snapshot"]).to_dict()))
            self.assertEqual(set(json.loads(message)), {"snapshot", "output_schema"})
            for key in ("category", "labels", "split", "subtype", "rationale"):
                self.assertNotIn(key, normalized)
            body, metadata = live.make_request(self.provider(), scene["snapshot"])
            changed = copy.deepcopy(scene)
            changed["labels"] = {"private": "must not enter request"}
            changed["category"] = "poison"
            self.assertEqual(live.make_request(self.provider(), changed["snapshot"])[0], body)
            self.assertEqual(metadata["snapshot_sha256"], live.frozen.digest(normalized))
            self.assertEqual(schema["additionalProperties"], False)
            self.assertEqual(json.loads(body)["max_tokens"], 512)
            self.assertFalse(json.loads(body)["stream"])

    def test_strict_owner_options_no_fallback(self):
        snapshot = self.scenes[0]["snapshot"]
        schema = live.output_schema(snapshot)
        good = live.frozen.rule(snapshot)
        self.assertEqual(live.parse_selection(json.dumps(good), schema), good)
        other_owner_id = next(iter(schema["properties"]["base"]["enum"]))
        for text in ("{}", "[]", "not JSON", "```json\n{}\n```", '{"arm":"a","arm":"b"}',
                     json.dumps({**good, "arm": other_owner_id}),
                     json.dumps({**good, "arm": True}), json.dumps({**good, "params": {}}),
                     json.dumps({**good, "arm": "invented"}), '{"arm":NaN}'):
            with self.subTest(text=text), self.assertRaises(live.Rejected):
                live.parse_selection(text, schema)

    def test_response_records_model_usage_not_thinking_or_headers(self):
        response = {"model": "service-reported-model", "content": [
            {"type": "thinking", "thinking": "hidden-test-data", "signature": "private"},
            {"type": "text", "text": "{}"}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 8, "cache_read_input_tokens": 20,
                      "cache_creation_input_tokens": 30, "secret": "dont persist"}, "headers": {"private": "dont persist"}}
        out = live.public_response(response, "anthropic")
        self.assertEqual(out["response_model"], "service-reported-model")
        self.assertEqual(out["raw_final_text"], "{}")
        self.assertEqual(out["usage"]["cache_read_input_tokens"], 20)
        self.assertNotIn("hidden-test-data", json.dumps(out))
        self.assertNotIn("dont persist", json.dumps(out))
        openai = {"model": "reported", "choices": [{"finish_reason": "stop", "message": {
            "content": "{}", "reasoning_content": "hidden-test-data"}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 12, "prompt_tokens_details": {"cached_tokens": 80}}}
        self.assertNotIn("hidden-test-data", json.dumps(live.public_response(openai, "openai")))
        for invalid in ({}, [], {"model": "reported", "choices": []}):
            with self.assertRaises(live.Rejected):
                live.public_response(invalid, "openai")

    def test_redaction_scan_and_no_error_body_persisted(self):
        secret = self.provider().secret
        raw = {"text": f"{secret} Bearer opaque-other-token sk-synthetic012345678901", secret: [secret]}
        clean = live.redact(raw, [secret])
        self.assertNotIn(secret, json.dumps(clean))
        self.assertNotIn("opaque-other-token", json.dumps(clean))
        self.assertNotIn("sk-synthetic", json.dumps(clean))
        with tempfile.TemporaryDirectory(prefix="llm-test-") as tmp:
            root = Path(tmp)
            live.write_json(root / "record.json", raw, [secret])
            self.assertEqual(live.scan_secrets(root, [secret])["exact_in_memory_secret_matches"], 0)
            (root / "leak.txt").write_text(secret, encoding="utf-8")
            self.assertEqual(live.scan_secrets(root, [secret])["exact_in_memory_secret_matches"], 1)
        self.assertNotIn(secret, repr(self.provider()))

    def test_readonly_db_single_authorized_provider_exact_pricing(self):
        with tempfile.TemporaryDirectory(prefix="llm-test-db-") as tmp:
            db = Path(tmp) / "selected.db"
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("CREATE TABLE providers(id TEXT,app_type TEXT,settings_config TEXT,cost_multiplier TEXT)")
                config = {"env": {"ANTHROPIC_BASE_URL": "https://selected.invalid/anthropic",
                                  "ANTHROPIC_AUTH_TOKEN": "synthetic-test-credential",
                                  "ANTHROPIC_DEFAULT_HAIKU_MODEL": "[free]test-model"}}
                conn.execute("INSERT INTO providers VALUES (?,?,?,?)", (live.AUTHORIZED_IDS[0], "claude", json.dumps(config), "1"))
                conn.execute("CREATE TABLE model_pricing(model_id TEXT,input_cost_per_million TEXT,output_cost_per_million TEXT,cache_read_cost_per_million TEXT,cache_creation_cost_per_million TEXT)")
                conn.execute("INSERT INTO model_pricing VALUES (?,?,?,?,?)", ("[free]test-model", "2", "8", "0.2", "2.5"))
            before = db.read_bytes()
            provider = live.read_provider(db, live.AUTHORIZED_IDS[0])
            self.assertEqual(provider.model, "[free]test-model")
            self.assertEqual(provider.pricing["input_cost_per_million"], "2")
            self.assertEqual(provider.auth_header, "Authorization")
            self.assertEqual(db.read_bytes(), before)
            with self.assertRaisesRegex(live.Rejected, "not_authorized"):
                live.read_provider(db, "unrelated")

    def test_budget_rejects_unknown_paid_negative_nan_and_reserves_failures(self):
        with self.assertRaisesRegex(live.Rejected, "unknown_pricing"):
            live.Budget(self.provider(model="paid", labeled_free=False))
        for amount in (Decimal("NaN"), Decimal("Infinity"), -1, 0, 6):
            with self.assertRaises(live.Rejected):
                live.Budget(self.provider(), amount)
        pricing = {"input_cost_per_million": "2", "output_cost_per_million": "8",
                   "cache_read_cost_per_million": "0.2", "cache_creation_cost_per_million": "2.5", "basis": "test"}
        budget = live.Budget(self.provider(pricing=pricing), Decimal("0.02"))
        first = budget.reserve(1000)
        self.assertGreater(first, 0)
        reserved = budget.reserved
        with self.assertRaisesRegex(live.Rejected, "reservation_exceeded"):
            budget.reserve(1000)
        self.assertEqual(budget.reserved, reserved)
        self.assertIsNone(live.Budget(self.provider()).reserve(1000))
        self.assertFalse(live.Budget(self.provider()).public()["financial_cap_verified"])
        for n in (0, -1, True, live.MAX_INPUT_BYTES + 1):
            with self.assertRaises(live.Rejected):
                live.Budget(self.provider()).reserve(n)
        record = {"response_model": "[free]test-model", "usage": {"input_tokens": 100, "output_tokens": 5,
                  "cache_read_input_tokens": 10, "cache_creation_input_tokens": 20}}
        result = live.cost_estimate(self.provider(pricing=pricing), [record])
        self.assertAlmostEqual(result["estimated_usd"], (200 + 40 + 2 + 50) / 1000000)
        self.assertIsNone(live.cost_estimate(self.provider(pricing=pricing), [record, {"error": "timeout"}])["estimated_usd"])
        self.assertIsNone(live.cost_estimate(self.provider(), [record])["estimated_usd"])

    def test_endpoint_no_credentials_query_nonhttps_or_crossdomain_redirect(self):
        self.assertEqual(live.endpoint("https://selected.invalid/anthropic", "anthropic"),
                         "https://selected.invalid/anthropic/v1/messages")
        self.assertEqual(live.endpoint("https://selected.invalid/v1", "openai"),
                         "https://selected.invalid/v1/chat/completions")
        for url in ("http://selected.invalid", "https://user:password@selected.invalid", "https://selected.invalid?key=x",
                    "https://selected.invalid/#fragment", "https://selected.invalid:8080"):
            with self.assertRaises(live.Rejected):
                live.endpoint(url, "anthropic")
        calls = []
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(self.path)
                self.send_response(307)
                self.send_header("Location", "https://unrelated.invalid/must-not-receive-auth")
                self.end_headers()
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Only test transport substitutions permit local plaintext, never CLI.
            with patch.object(live.http.client, "HTTPSConnection", lambda host, port, timeout:
                              http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=timeout)):
                status, payload, error = live.http_exchange("https://selected.invalid/v1/messages",
                                                          {"Authorization": "Bearer synthetic-test-credential"}, b"{}", 1)
            self.assertEqual((status, payload, error), (307, None, "redirect_rejected"))
            self.assertEqual(calls, ["/v1/messages"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_subprocess_total_deadline_no_lingering_worker(self):
        before = {p.pid for p in multiprocessing.active_children()}
        started = time.perf_counter()
        with patch.object(live, "_http_child", stalled_child):
            result = live.call_http(self.provider(), b"{}", timeout=0.4)
        self.assertEqual(result["error"], "timeout")
        self.assertLess(time.perf_counter() - started, 2)
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)

    def test_full_mock_replay_retains_300_cases_and_fails_without_repair(self):
        # Test-only fabricated transport results are never a model baseline.
        schema = live.output_schema(live.DecisionSnapshot.parse(self.scenes[0]["snapshot"]).to_dict())
        good = {k: v["enum"][0] for k, v in schema["properties"].items()}
        responses = [{"http_status": 200, "error": None, "elapsed_ms": 1,
                      "finish_reason": "end_turn", "response_model": "[free]test-model",
                      "raw_final_text": json.dumps(good), "usage": {"input_tokens": 10, "output_tokens": 5}}]
        responses += [{"http_status": 200, "error": None, "elapsed_ms": 2,
                       "finish_reason": "max_tokens", "response_model": "[free]test-model",
                       "raw_final_text": "", "usage": {"input_tokens": 10, "output_tokens": 512}}
                      for _ in range(300)]
        responses[101] = {"http_status": None, "error": "timeout", "elapsed_ms": 30000}
        with tempfile.TemporaryDirectory(prefix="llm-test-full-") as tmp:
            root = Path(tmp) / "evidence"
            opts = argparse.Namespace(allow_live_http=True, evidence=root / "mock-only", db=Path("unused"),
                                      provider_id=live.AUTHORIZED_IDS[0], protocol="anthropic",
                                      budget_usd=Decimal(5), probe_only=False)
            with patch.object(live, "EVIDENCE_ROOT", root), \
                 patch.object(live, "read_provider", return_value=self.provider()), \
                 patch.object(live, "call_http", side_effect=responses) as transport, \
                 patch.object(live.time, "sleep"), patch("builtins.print"):
                self.assertEqual(live.execute(opts), 0)
            report = json.loads((opts.evidence / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(transport.call_count, 301)
            self.assertEqual(report["evaluation_requests"], 300)
            self.assertEqual(report["summaries"]["real_llm"]["holdout"]["scene_runs"], 60)
            self.assertEqual(report["summaries"]["real_llm"]["development"]["scene_runs"], 240)
            self.assertEqual(report["summaries"]["real_llm"]["all"]["invalid_output_scene_runs"], 300)
            self.assertEqual(report["summaries"]["real_llm"]["all"]["bad_choice_count"], 600)
            self.assertEqual(report["timeouts_including_probe"], 1)
            self.assertEqual(report["repeats"], 3)
            self.assertTrue(report["source_stable_through_run"])
            for run in report["runs"]["real_llm"]:
                self.assertEqual(len(run["rows"]), 100)
                self.assertTrue(all(r["choices"] == {} for r in run["rows"]))
            self.assertEqual(live.ledger_counts(root)[:2], (301, 1))
            from validation_drivers import summarize_llm_evidence as audit
            with patch.object(live, "EVIDENCE_ROOT", root):
                rebuilt = audit.rebuild(opts.evidence)
            self.assertEqual(rebuilt["status"], "completed_evidence")
            self.assertEqual(rebuilt["summaries"]["real_llm"]["all"], report["summaries"]["real_llm"]["all"])
            self.assertIsNone(rebuilt["summaries"]["rule_quality_replay"]["all"]["latency_ms"])
            self.assertEqual(rebuilt["source_sha256"], report["source_sha256"])
            from validation_drivers import compare_llm_jev_evidence as comparison
            decisions = []
            for run in report["runs"]["real_llm"]:
                for row, scene in zip(run["rows"], self.scenes):
                    snap = live.DecisionSnapshot.parse(scene["snapshot"]).to_dict()
                    choices = live.frozen.rule(snap)
                    decisions.append({"repeat": run["repeat"], "scene_id": scene["scene_id"],
                                      "input_snapshot_sha256": row["snapshot_sha256"],
                                      "elapsed_ms": 1, "raw_choices": choices,
                                      "post_override_choices": choices, "reject_code": None,
                                      "adapter_record": {"conservative_overrides": 0},
                                      "http": {"transport_elapsed_ms": 1,
                                               "request": {"state": {"snapshot": snap}}}})
            fake_jev = {"complete": True, "source_unchanged": True, "real_jev_measured": True,
                        "corpus_sha256": report["corpus_sha256"], "split_sha256": report["split_sha256"],
                        "repeats": 3, "source_sha256": {k.replace("\\", "/"): v for k, v in report["source_sha256"].items()},
                        "decisions": decisions, "config": {"model": "test-not-a-model", "deadline_ms": 1500, "min_confidence": 0.6},
                        "budget": {"attempted_http_calls": 300},
                        "phase_usage": {"usage_estimated_cost_usd": None},
                        "total_session_usage_including_probes": {"usage_estimated_cost_usd": None}}
            fake_path = root / "test-only-fake-jev.json"
            live.write_json(fake_path, fake_jev)
            with patch.object(live, "EVIDENCE_ROOT", root):
                compared = comparison.compare(opts.evidence / "report.json", fake_path)
                self.assertEqual(compared["snapshots_checked"], 300)
                self.assertEqual(compared["summaries"]["jev_raw"]["all"]["joint_label_matches"], 300)
                self.assertIsNone(compared["task_success"])
                self.assertFalse(compared["prompts_identical"])
                fake_jev["decisions"][0]["input_snapshot_sha256"] = "tampered"
                bad_path = root / "test-only-tampered-jev.json"
                live.write_json(bad_path, fake_jev)
                with self.assertRaisesRegex(ValueError, "snapshot mismatch"):
                    comparison.compare(opts.evidence / "report.json", bad_path)
            self.assertIsNone(report["actual_bill"])
            self.assertIsNone(report["task_success"])
            self.assertEqual(report["action_dispatches"], 0)
            with patch.object(live, "EVIDENCE_ROOT", root), self.assertRaisesRegex(live.Rejected, "no_overwrite"):
                live.execute(opts)

    def test_fixed_transport_diagnostics_never_include_exception_secret(self):
        import socket
        import ssl
        provider = self.provider()
        exceptions = ((socket.gaierror(provider.secret), "dns_failed"),
                      (ssl.SSLCertVerificationError(provider.secret), "tls_certificate_failed"),
                      (ConnectionError(provider.secret), "connection_failed"),
                      (RuntimeError(provider.secret), "transport_failed"))
        for exc, code in exceptions:
            class Pipe:
                result = None
                def send(self, result):
                    self.result = result
                def close(self):
                    pass
            pipe = Pipe()
            with patch.object(live, "http_exchange", side_effect=exc):
                live._http_child(pipe, provider, b"{}")
            self.assertEqual(pipe.result["error"], code)
            self.assertNotIn(provider.secret, json.dumps(pipe.result))

    def test_usage_missing_not_zero_latency_and_timeout_denominators(self):
        scene = self.scenes[0]
        good = live.frozen.score_scene(scene, live.frozen.rule(scene["snapshot"]), 10)
        bad = live.frozen.score_scene(scene, None, 30000, "timeout")
        bad["request_error"] = "timeout"
        metrics = live.metrics([good, bad], True)
        self.assertEqual(metrics["joint_label_match_rate"], 0.5)
        self.assertEqual(metrics["timeout_scene_runs"], 1)
        self.assertEqual(metrics["latency_ms"]["p95"], 30000)
        self.assertIn("cloud", metrics["latency_ms"]["scope"])
        totals = live.usage_summary([{"usage": {"input_tokens": 1, "cache_read_input_tokens": 5}}, {"error": "timeout"}])
        self.assertEqual(totals["calls_without_usage"], 1)
        self.assertEqual(totals["actual_response_usage_totals"]["cache_read_input_tokens"], 5)


if __name__ == "__main__":
    unittest.main()
