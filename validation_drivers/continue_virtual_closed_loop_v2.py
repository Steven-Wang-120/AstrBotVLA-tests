"""Transparent v2 continuation: no retry; official alias interpretation, original evidence immutable."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import multiprocessing
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_support import bootstrap as paths
paths.bootstrap()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_drivers import evaluate_virtual_closed_loop_v2 as v
from validation_drivers import b07_virtual_tasks_v2 as machine
from validation_drivers import evaluate_llm_live as llm

OUT = paths.artifacts_path("2026-10-01-llm-v2-continuation")
ALIASES = ("deepseek-v4-flash", "deepseek-flash")
ALIAS_SOURCES = ["https://api-docs.deepseek.com/updates", "https://api-docs.deepseek.com/quick_start/pricing"]


def configured_alias_cost(provider, records):
    # Explicit configuration-derived estimate only, never claim updated vendor tariff.
    interpreted = [{**r, "response_model": provider.model} if r.get("response_model") in ALIASES else r for r in records]
    result = llm.cost_estimate(provider, interpreted)
    result["basis"] += "; official alias deepseek-flash; OLD configured pricing, NOT verified V4.1 tariff or invoice"
    return result


class ContinuedSession(v.Session):
    def llm_request(self, phase, system, payload):
        body, size = v.request_body(self.provider, system, payload)
        prior = self.prior.get((self.context["repeat"], self.context["task_id"], self.context["arm"])) if phase == "planner" else None
        if prior is not None:
            if hashlib.sha256(body).hexdigest() != prior["input_sha256"]:
                raise llm.Rejected("recovered_planner_input_not_byte_identical")
            record = copy.deepcopy(prior)
            record["interpretation"] = "official_alias_revalidation_of_original_response_no_retry_no_new_call"
            record["original_validation_error"] = prior.get("validation_error")
            record["original_record_sha256"] = self.prior_hashes[record["call_ref"]]
        else:
            amount = self.budget.reserve()
            self.wait()
            ref = f"llm-{self.budget.count:03d}"
            plan = {**self.context, "session": self.session_id, "call_ref": ref, "service": "llm", "phase": phase,
                    **self.provider.public(), "start_utc": v.utc(), "input_bytes": size,
                    "request": json.loads(body), "input_sha256": hashlib.sha256(body).hexdigest(), "reserved_usd": amount}
            llm.write_json(self.directory / f"request-{ref}.json", plan, [self.provider.secret, self.key])
            record = {**plan, **llm.call_http(self.provider, body, timeout=30), "actual_invoice": None}
        error = record.get("error")
        value = None
        if error is None:
            if record.get("finish_reason") != "stop":
                error = "nonfinal_or_truncated_response"
            elif record.get("response_model") not in ALIASES:
                error = "response_model_outside_documented_alias"
            else:
                try:
                    value = llm.frozen.strict_json(record.get("raw_final_text"))
                    if phase == "planner":
                        machine.parse_plan(value)
                    else:
                        llm.parse_selection(record["raw_final_text"], payload["output_schema"])
                except (ValueError, TypeError, RecursionError):
                    error = "invalid_output_schema"
                    value = None
        record.update(validation_error=error, output=value, cost=configured_alias_cost(self.provider, [record]))
        result = self.persist(record)
        return value, error, [result["call_ref"]]


def hashes(jev_harness=None):
    manifest = v.source_manifest(jev_harness)
    manifest[str(Path(__file__))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest[str(ROOT / "docs/B07-VIRTUAL-V2-CONTINUATION.md")] = hashlib.sha256(
        (ROOT / "docs/B07-VIRTUAL-V2-CONTINUATION.md").read_bytes()).hexdigest()
    manifest.update({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in v.EVIDENCE.rglob("*") if p.is_file()})
    return manifest


def execute():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live-http", action="store_true")
    parser.add_argument("--jev-harness", type=Path,
                        help="read-only helper path; default: this repository's validation_drivers/evaluate_jev_live.py")
    options = parser.parse_args()
    if not options.allow_live_http:
        raise llm.Rejected("live_http_requires_explicit_opt_in")
    if OUT.exists():
        raise llm.Rejected("continuation_exists_no_overwrite")
    selected = llm.read_provider(llm.DB_DEFAULT, v.PROVIDER_ID, "openai")
    if v.urlsplit(selected.base_url).hostname != "api.deepseek.com" or selected.model != "deepseek-v4-flash":
        raise llm.Rejected("authorized_provider_changed")
    provider = replace(selected, base_url="https://api.deepseek.com", auth_header="Authorization")
    original_preflight = json.loads((v.EVIDENCE / "preflight.json").read_text())
    original_plans = [json.loads(p.read_text()) for p in sorted(v.EVIDENCE.glob("request-llm-*.json"))]
    originals = {r["call_ref"]: r for r in [json.loads(p.read_text()) for p in sorted(v.EVIDENCE.glob("call-*.json"))]}
    if len(original_plans) != 34 or len(originals) != 33 or any(p["phase"] != "planner" for p in original_plans):
        raise llm.Rejected("unexpected_interrupted_request_history")
    frozen_tasks = json.loads(v.SPEC.read_text())
    if frozen_tasks != {"version": machine.VERSION, "tasks": machine.tasks()}:
        raise llm.Rejected("frozen_spec_changed")
    key = v.read_first_key()
    before = hashes(options.jev_harness)
    OUT.mkdir()
    session = ContinuedSession(OUT, provider, key, original_preflight["session"],
                               jev_harness=options.jev_harness)
    session.prior, session.prior_hashes = {}, {}
    for plan in original_plans:
        original = originals.get(plan["call_ref"])
        if original is None:
            record = {**plan, "error": "interrupted_response_not_observed_no_retry", "validation_error": "interrupted_response_not_observed_no_retry",
                      "response_model": None, "usage": None, "elapsed_ms": None, "raw_final_text": None,
                      "finish_reason": None, "output": None, "actual_invoice": None,
                      "sent_status": "pre_send_reservation_exists_remote_send_not_proven"}
            hash_value = hashlib.sha256((v.EVIDENCE / f"request-{plan['call_ref']}.json").read_bytes()).hexdigest()
        else:
            record = original
            hash_value = hashlib.sha256((v.EVIDENCE / f"call-{int(plan['call_ref'].split('-')[1]):04d}.json").read_bytes()).hexdigest()
        session.prior[(plan["repeat"], plan["task_id"], plan["arm"])] = record
        session.prior_hashes[plan["call_ref"]] = hash_value
        session.budget.reserve()  # Already reserved calls carry forward, never refunded.
    llm.write_json(OUT / "preflight.json", {"version": machine.VERSION, "session": session.session_id,
                   "source_before": before, "original_session_preflight_sha256": hashlib.sha256((v.EVIDENCE / "preflight.json").read_bytes()).hexdigest(),
                   "official_aliases": ALIASES, "alias_sources": ALIAS_SOURCES, "documented_response_model": "deepseek-flash",
                   "prior_request_reservations": 34, "prior_observed_responses": 33,
                   "prior_uncertain_interrupted_attempts": 1, "prior_requests_retried": 0,
                   "prompts_cap_thinking_provider_requested_model_unchanged": True,
                   "source_interpretation_changed_since_original_run": True,
                   "old_configured_price_not_current_tariff": provider.pricing, "budget": session.budget.public(),
                   "execute_authorized": False}, [provider.secret, key])
    rows = []
    started = time.perf_counter()
    for repeat in range(1, 4):
        for task in frozen_tasks["tasks"]:
            order = ("llm_only", "hybrid") if repeat % 2 else ("hybrid", "llm_only")
            for arm in order:
                session.context = {"repeat": repeat, "task_id": task["task_id"], "arm": arm}
                task_start = time.perf_counter()
                start_record = len(session.records)
                value, error, planner_refs = session.llm_request("planner", machine.PLAN_SYSTEM, machine.planner_input(task))
                plan = None if error else machine.parse_plan(value)
                row = machine.run_arm(task, plan, session.selector, session.session_id)
                owned = session.records[start_record:]
                prior_ms = sum(r["elapsed_ms"] for r in owned
                               if r.get("interpretation") and r.get("elapsed_ms") is not None)
                unknown_task_ms = sum(r.get("elapsed_ms") is None for r in owned)
                l = [r for r in owned if r["service"] == "llm"]
                j = [r for r in owned if r["service"] == "jev"]
                jev_cost = v.jev_cost_estimate(j)
                known_task_ms = (time.perf_counter() - task_start) * 1000 + prior_ms
                row.update(session.context)
                row.update(elapsed_ms=None if unknown_task_ms else known_task_ms,
                           known_active_elapsed_ms=known_task_ms, unobserved_latency_attempts=unknown_task_ms,
                           timing_scope="task active durations plus recovered planner original RTT; interruption idle gap excluded; total null if a call duration is unknown",
                           model_calls=len(owned), llm_calls=len(l), jev_calls=len(j), planner_refs=planner_refs, planner_error=error,
                           response_models=dict(Counter(r.get("response_model") for r in owned)),
                           llm_token_estimated_cost=configured_alias_cost(provider, l),
                           jev_token_estimated_cost_usd=jev_cost["estimated_usd"],
                           jev_known_usage_subtotal_usd=jev_cost["known_usage_subtotal_usd"],
                           jev_missing_usage_calls=jev_cost["missing_usage_calls"], actual_invoice=None)
                llm.write_json(OUT / f"task-r{repeat}-{task['task_id']}-{arm}.json", row, [provider.secret, key])
                rows.append(row)
            if int(task["task_id"][-2:]) % 5 == 0:
                print(json.dumps({"repeat": repeat, "task": task["task_id"], "recorded_or_reserved_llm": session.budget.count,
                                  "jev": len(session.jev_ledger.reservations), "arm_tasks_completed": len(rows)}), flush=True)
    after = hashes(options.jev_harness)
    # One uncertain interrupted attempt has no measured latency; never fabricate one.
    summaries = v.summarize(rows, session.records, provider)
    for arm, summary in summaries.items():
        arm_records = [r for r in session.records if r["arm"] == arm]
        summary["llm_cost"] = configured_alias_cost(provider, [r for r in arm_records if r["service"] == "llm"])
        summary["model_calls_includes_one_uncertain_presend_reserved_attempt"] = arm == "hybrid"
    continuation_ms = (time.perf_counter() - started) * 1000
    report = {"version": machine.VERSION, "session": session.session_id, "status": "completed_with_disclosed_interruption" if before == after else "source_changed_reject",
              "source_before": before, "source_after": after, "source_stable_during_continuation": before == after,
              "original_interrupted_evidence_unchanged": before == after,
              "v1_immutable_evidence_unchanged": all(before.get(p) == h for p, h in original_preflight["source_before"].items()
                                                      if "llm-v2" not in p and "virtual" not in p and "B07-VIRTUAL" not in p),
              "source_interpretation_changed_original_to_continuation": True,
              "prompts_cap_thinking_unchanged": True, "official_aliases": ALIASES,
              "repeats": 3, "tasks_per_repeat_per_arm": 20, "summaries": summaries, "tasks": rows,
              "continuation_end_to_end_session_ms": continuation_ms,
              "complete_wall_elapsed_including_interruption_ms": (v.datetime.now(v.timezone.utc) - v.datetime.fromisoformat(original_plans[0]["start_utc"])).total_seconds() * 1000,
              "total_model_attempt_records": len(session.records), "observed_llm_responses": sum(r["service"] == "llm" and r.get("response_model") is not None for r in session.records),
              "uncertain_interrupted_attempts": 1, "no_retries": True, "llm_budget": session.budget.public(),
              "jev_budget": session.jev_ledger.report(), "actual_invoice": None, "hardware_dispatches": 0,
              "execute_authorized": False, "jev_execution_allowed": False,
              "no_unseen_holdout_claim": True, "no_jev_execute_acceptance": True}
    llm.write_json(OUT / "report.json", report, [provider.secret, key])
    llm.write_json(OUT / "replay-audit.json", v.replay_audit(OUT))
    for directory in (v.EVIDENCE, OUT, paths.artifacts_path("2026-10-01-llm-v2-checks")):
        scan = llm.scan_secrets(directory, [provider.secret, key])
        if scan["exact_in_memory_secret_matches"]:
            raise llm.Rejected("exact_secret_scan_failed")
        llm.write_json(OUT / (directory.name + "-secret-scan.json"), scan)
    print(json.dumps({"status": report["status"], "session": session.session_id,
                      "successes": {a: s["successes"] for a, s in summaries.items()},
                      "llm_attempts": session.budget.count, "jev_calls": len(session.jev_ledger.reservations),
                      "report": str(OUT / "report.json"), "secret_matches": 0}), flush=True)
    return 0 if before == after else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        raise SystemExit(execute())
    except llm.Rejected as exc:
        print(json.dumps({"status": "rejected", "code": str(exc)}), file=sys.stderr)
        raise SystemExit(2)
    except Exception:
        print(json.dumps({"status": "failed", "code": "continuation_local_error_redacted"}), file=sys.stderr)
        raise SystemExit(2)
