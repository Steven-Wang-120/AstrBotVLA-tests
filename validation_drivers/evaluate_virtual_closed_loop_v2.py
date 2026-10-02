"""Opt-in B07 v2 public virtual closed-loop experiment; no execution service."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import multiprocessing
import re
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_support import bootstrap as paths
paths.bootstrap()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_drivers import b07_virtual_tasks_v2 as machine
from validation_drivers import evaluate_llm_live as llm
from astrbot_ex.core.decision.backends import jev
from astrbot_ex.core.decision.models import DecisionSnapshot

EVIDENCE = paths.artifacts_path("2026-10-01-llm-v2")
SPEC = ROOT / "fixtures/decision/virtual-v2.tasks.json"
DEFAULT_JEV_HARNESS = ROOT / "validation_drivers/evaluate_jev_live.py"
KEY_FILE = paths.explicit_secret_path("ASTRBOTVLA_JEV_KEY_FILE")
PROVIDER_ID = "4a5dc05f-ad51-437f-b137-60469993368c"
MAX_INPUT = 12000
MAX_LLM = 300
MAX_JEV = 180
OUTPUT_CAP = 512
JEV_RESERVE = Decimal(65536) * Decimal("0.042") / 1000000
SOURCES = ["validation_drivers/b07_virtual_tasks_v2.py", "validation_drivers/evaluate_virtual_closed_loop_v2.py",
           "validation_drivers/capture_virtual_evidence_v2.py", "validation_tests/evaluation/test_virtual_closed_loop_v2.py",
           "fixtures/decision/virtual-v2.tasks.json", "docs/B07-VIRTUAL-V2-PROTOCOL.md",
           "validation_drivers/evaluate_llm_live.py", "validation_drivers/evaluate_jev_offline.py",
           "astrbot_ex/core/decision/backends/jev.py", "astrbot_ex/core/decision/models.py",
           "astrbot_ex/core/actions/models.py"]
OFFICIAL = ["https://api-docs.deepseek.com/guides/thinking_mode",
            "https://api-docs.deepseek.com/api/create-chat-completion",
            "https://api-docs.deepseek.com/guides/anthropic_api",
            "https://docs.typesafe.ai/models", "https://docs.typesafe.ai/api"]


def utc():
    return datetime.now(timezone.utc).isoformat()


def source_manifest(jev_harness=None):
    harness_path = Path(jev_harness) if jev_harness is not None else DEFAULT_JEV_HARNESS
    files = [paths.source_path(p) for p in SOURCES] + [harness_path.resolve()]
    # Entire v1 evidence and all v1 new source/docs are immutable, not just corpus.
    files += [p for p in paths.artifacts_path("2026-10-01-llm").rglob("*") if p.is_file()]
    files += [ROOT / p for p in ("validation_drivers/capture_llm_evidence.py", "validation_drivers/compare_llm_jev_evidence.py",
                               "validation_drivers/summarize_llm_evidence.py", "validation_tests/evaluation/test_llm_live_evaluation.py",
                               "docs/B07-LLM-LIVE.md", "docs/B07-LLM-RESULTS.md",
                               "fixtures/decision/jev/offline-v1.jsonl",
                               "fixtures/decision/jev/offline-v1.manifest.json")]
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def read_first_key():
    # First token only; no second account/key discovery or credential persistence.
    if KEY_FILE is None:
        raise llm.Rejected("explicit_jev_key_file_required")
    with KEY_FILE.open(encoding="utf-8-sig") as stream:
        for line in stream:
            value = line.strip()
            if value:
                if not re.fullmatch(r"[A-Za-z0-9_-]{20,4096}", value):
                    raise llm.Rejected("invalid_first_authorized_key")
                return value
    raise llm.Rejected("empty_authorized_key_file")


def load_jev_harness(path=None):
    path = (Path(path) if path is not None else DEFAULT_JEV_HARNESS).resolve()
    spec = importlib.util.spec_from_file_location("b07_readonly_jev_harness", path)
    module = importlib.util.module_from_spec(spec)
    # Compile the read-only source without creating a helper __pycache__ file.
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def request_body(provider, system, payload):
    message = machine.canonical(payload).decode()
    size = len(message.encode()) + len(system.encode())
    if size > MAX_INPUT:
        raise llm.Rejected("v2_input_cap_exceeded")
    body = {"model": provider.model, "stream": False, "max_tokens": OUTPUT_CAP,
            "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": message}]}
    return machine.canonical(body), size


class LlmBudget:
    def __init__(self, provider):
        self.provider = provider
        if provider.pricing is None:
            raise llm.Rejected("v2_exact_pricing_required")
        self.count = 0
        self.reserved = Decimal(0)
        rate = max(llm.decimal_rate(provider.pricing[k]) for k in
                   ("input_cost_per_million", "cache_read_cost_per_million", "cache_creation_cost_per_million"))
        self.each = ((MAX_INPUT + 4096) * rate + OUTPUT_CAP *
                     llm.decimal_rate(provider.pricing["output_cost_per_million"])) / 1000000
        if self.each * MAX_LLM > 1:
            raise llm.Rejected("v2_complete_run_reserve_over_1usd")

    def reserve(self):
        if self.count >= MAX_LLM or self.reserved + self.each > 1:
            raise llm.Rejected("v2_llm_budget_exhausted")
        self.count += 1
        self.reserved += self.each
        return float(self.each)

    def public(self):
        return {"calls_reserved": self.count, "max_requests": MAX_LLM, "limit_usd": 1,
                "reserved_usd": float(self.reserved), "complete_run_upper_reserve_usd": float(self.each * MAX_LLM),
                "basis": "12000 UTF8 input bytes + 4096 token overhead, 512 output, maximum configured input/cache rate",
                "actual_invoice": None, "financial_cap_verified": False}


def percentiles(values):
    values = sorted(values)
    if not values:
        return {"p50": None, "p95": None, "max": None}
    return {"p50": values[math.ceil(len(values) * .5) - 1],
            "p95": values[math.ceil(len(values) * .95) - 1], "max": values[-1]}


def jev_cost_estimate(records):
    known = Decimal(0)
    missing = 0
    for record in records:
        usage = record.get("usage")
        if not isinstance(usage, dict) or type(usage.get("input_tokens")) is not int or usage["input_tokens"] < 0:
            missing += 1
            continue
        known += Decimal(usage["input_tokens"]) * Decimal("0.042") / Decimal(1000000)
    return {"estimated_usd": None if missing else float(known),
            "known_usage_subtotal_usd": float(known), "missing_usage_calls": missing,
            "basis": "Jev input tokens $0.042/M; output tokens free; actual invoice unknown"}


def summarize(rows, records, provider):
    result = {}
    for arm in ("llm_only", "hybrid"):
        tasks = [r for r in rows if r["arm"] == arm]
        calls = [r for r in records if r["arm"] == arm]
        l = [r for r in calls if r["service"] == "llm"]
        j = [r for r in calls if r["service"] == "jev"]
        jev_cost = jev_cost_estimate(j)
        known_request_ms = [r["elapsed_ms"] for r in calls if r.get("elapsed_ms") is not None]
        unknown_request_ms = len(calls) - len(known_request_ms)
        known_task_ms = [r["elapsed_ms"] for r in tasks if r.get("elapsed_ms") is not None]
        unknown_task_ms = len(tasks) - len(known_task_ms)
        result[arm] = {"tasks": len(tasks), "successes": sum(r["success"] for r in tasks),
                       "success_rate": sum(r["success"] for r in tasks) / len(tasks) if tasks else None,
                       "model_calls": len(calls), "llm_calls": len(l), "jev_calls": len(j),
                       "request_latency_ms": percentiles(known_request_ms),
                       "unobserved_latency_attempts": unknown_request_ms,
                       "sum_request_ms": None if unknown_request_ms else sum(known_request_ms),
                       "sum_known_request_ms": sum(known_request_ms),
                       "task_end_to_end_ms": percentiles(known_task_ms),
                       "unobserved_task_latency_attempts": unknown_task_ms,
                       "sum_task_end_to_end_ms": None if unknown_task_ms else sum(known_task_ms),
                       "sum_known_task_end_to_end_ms": sum(known_task_ms),
                       "llm_cost": llm.cost_estimate(provider, l), "llm_usage": llm.usage_summary(l),
                       "jev_usage": llm.usage_summary(j),
                       "jev_estimated_usd": jev_cost["estimated_usd"],
                       "jev_known_usage_subtotal_usd": jev_cost["known_usage_subtotal_usd"],
                       "jev_missing_usage_calls": jev_cost["missing_usage_calls"],
                       "actual_invoice": None,
                       "by_repeat": {str(n): {"tasks": sum(r["repeat"] == n for r in tasks),
                                              "successes": sum(r["repeat"] == n and r["success"] for r in tasks),
                                              "calls": sum(r["repeat"] == n for r in calls)} for n in range(1, 4)},
                       "by_category": {cat: {"tasks": sum(r["category"] == cat for r in tasks),
                                             "successes": sum(r["category"] == cat and r["success"] for r in tasks)}
                                       for cat in sorted({r["category"] for r in tasks})},
                       "failure_reasons": dict(Counter(r["failure_reason"] for r in tasks if not r["success"]))}
    return result


class Session:
    def __init__(self, directory, provider, key, session_id, *, jev_harness=None):
        self.directory, self.provider, self.key, self.session_id = directory, provider, key, session_id
        self.budget = LlmBudget(provider)
        self.records = []
        self.last_started = float("-inf")
        self.harness = load_jev_harness(jev_harness)
        self.jev_ledger = self.harness.Ledger(directory, max_calls=MAX_JEV, max_usd=Decimal(1))
        self.context = {}

    def wait(self):
        delay = max(0, self.last_started + .5 - time.monotonic())
        if delay:
            time.sleep(delay)
        self.last_started = time.monotonic()

    def persist(self, record):
        record = llm.redact(record, [self.provider.secret, self.key])
        llm.write_json(self.directory / f"call-{len(self.records)+1:04d}.json", record,
                       [self.provider.secret, self.key])
        self.records.append(record)
        return record

    def llm_request(self, phase, system, payload):
        body, size = request_body(self.provider, system, payload)
        amount = self.budget.reserve()
        self.wait()
        ref = f"llm-{self.budget.count:03d}"
        plan = {**self.context, "session": self.session_id, "call_ref": ref, "service": "llm", "phase": phase,
                **self.provider.public(), "start_utc": utc(), "input_bytes": size,
                "request": json.loads(body), "input_sha256": hashlib.sha256(body).hexdigest(),
                "reserved_usd": amount}
        llm.write_json(self.directory / f"request-{ref}.json", plan, [self.provider.secret, self.key])
        response = llm.call_http(self.provider, body, timeout=30)
        error = response.get("error")
        value = None
        if error is None:
            if response.get("finish_reason") != "stop":
                error = "nonfinal_or_truncated_response"
            elif response.get("response_model") != self.provider.model:
                error = "response_model_mismatch"
            else:
                try:
                    value = llm.frozen.strict_json(response.get("raw_final_text"))
                    if phase == "planner":
                        machine.parse_plan(value)
                    else:
                        llm.parse_selection(response["raw_final_text"], payload["output_schema"])
                except (ValueError, TypeError, RecursionError):
                    error = "invalid_output_schema"
                    value = None
        record = self.persist({**plan, **response, "validation_error": error, "output": value,
                               "cost": llm.cost_estimate(self.provider, [response]), "actual_invoice": None})
        return value, error, [record["call_ref"]]

    def jev_request(self, snapshot):
        normalized = DecisionSnapshot.parse(snapshot)
        self.wait()
        transport = self.harness.RecordingTransport(self.jev_ledger)
        transport.phase = "virtual_v2"
        backend = jev.JevBackend(jev.JevConfig(mode="shadow", allow_live_http=True, deadline_ms=1500,
                                             min_interval_ms=500, max_retries=0, min_confidence=.6,
                                             observation_guides=(("virtual.machine", machine.GUIDE),)),
                                 transport=transport, secret_provider=lambda: self.key)
        try:
            if backend.execution_allowed is not False:
                raise llm.Rejected("jev_execution_gate_not_false")
            result = self.harness.one_decision(backend, transport, normalized.to_dict())
        finally:
            backend.close()
        http = result["http"]
        if http is None:
            # No transport call means no request/cost/call invented.
            return None, result["reject_code"] or "jev_not_sent", []
        projection = http.get("response") or {}
        usage = projection.get("usage")
        record = self.persist({**self.context, "session": self.session_id,
                               "call_ref": f"jev-{http['call_number']:03d}", "service": "jev", "phase": "selector",
                               "requested_model": jev.PINNED_MODEL, "response_model": projection.get("model"),
                               "usage": usage, "elapsed_ms": result["elapsed_ms"], "start_utc": http["start_utc"],
                               "request": http["request"], "public_response": projection,
                               "http_status": http["status"], "transport_error": http["transport_error"],
                               "validation_error": result["reject_code"], "raw_choices": result["raw_choices"],
                               "output": result["post_override_choices"], "adapter_record": result["adapter_record"],
                               "reserved_usd": float(JEV_RESERVE), "execution_allowed": False,
                               "estimated_usd": jev_cost_estimate([{"usage": usage}])["estimated_usd"],
                               "actual_invoice": None})
        return record["output"], record["validation_error"], [record["call_ref"]]

    def selector(self, snapshot):
        if self.context["arm"] == "hybrid":
            return self.jev_request(snapshot)
        payload = {"snapshot": DecisionSnapshot.parse(snapshot).to_dict(),
                   "output_schema": llm.output_schema(snapshot)}
        return self.llm_request("selector", machine.SELECT_SYSTEM, payload)


def replay_audit(directory):
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    task_map = {t["task_id"]: t for t in machine.tasks()}
    calls = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("call-*.json"))]
    refs = {c["call_ref"] for c in calls}
    by_ref = {c["call_ref"]: c for c in calls}
    if len(refs) != len(calls):
        raise ValueError("duplicate_call_ref")
    for row in report["tasks"]:
        task = task_map[row["task_id"]]
        if row["initial_state_sha256"] != machine.digest(task["initial_state"]):
            raise ValueError("initial_state_mismatch")
        state = copy.deepcopy(task["initial_state"])
        for step in row["steps"]:
            if state != step["before"] or any(r not in refs for r in step["call_refs"]):
                raise ValueError("step_link_mismatch")
            if any(by_ref[r].get("validation_error") for r in step["call_refs"]):
                state["failure"] = step["reason"]
            else:
                state, reason = machine.transition(task, state, step["goal_key"], step["choices"])
                if reason != step["reason"]:
                    raise ValueError("transition_reason_mismatch")
            if state != step["after"]:
                raise ValueError("actual_transition_mismatch")
        if row["plan"] is None:
            state["failure"] = "planner_failed_no_oracle_fallback"
        if state != row["final_state"] or machine.success(task, state) != row["success"]:
            raise ValueError("terminal_success_mismatch")
        owned = [c for c in calls if c["task_id"] == row["task_id"] and c["repeat"] == row["repeat"] and c["arm"] == row["arm"]]
        if len(owned) != row["model_calls"]:
            raise ValueError("request_count_mismatch")
    return {"status": "passed", "tasks_replayed": len(report["tasks"]), "real_call_records": len(calls),
            "successes": dict(Counter(r["arm"] for r in report["tasks"] if r["success"])), "network_calls": 0}


def execute(options):
    if not options.allow_live_http:
        raise llm.Rejected("live_http_requires_explicit_opt_in")
    if EVIDENCE.exists():
        raise llm.Rejected("v2_evidence_exists_no_rerun_or_overwrite")
    frozen_tasks = json.loads(SPEC.read_text())
    if frozen_tasks != {"version": machine.VERSION, "tasks": machine.tasks()}:
        raise llm.Rejected("v2_frozen_spec_mismatch")
    selected = llm.read_provider(llm.DB_DEFAULT, PROVIDER_ID, "openai")
    if urlsplit(selected.base_url).hostname != "api.deepseek.com" or selected.model != "deepseek-v4-flash":
        raise llm.Rejected("v2_authorized_provider_changed")
    # Official chat endpoint differs from the configured Anthropic base path.
    provider = replace(selected, base_url="https://api.deepseek.com", auth_header="Authorization")
    key = read_first_key()
    budget = LlmBudget(provider)
    before = source_manifest(options.jev_harness)
    EVIDENCE.mkdir()
    session_id = "b07-v2-" + hashlib.sha256(utc().encode()).hexdigest()[:12]
    session = Session(EVIDENCE, provider, key, session_id, jev_harness=options.jev_harness)
    llm.write_json(EVIDENCE / "preflight.json", {
        "version": machine.VERSION, "session": session_id, "source_before": before, "task_spec_sha256": machine.digest(frozen_tasks),
        "scope": "new_public_synthetic_virtual_state_machine_not_unseen_holdout_not_hardware_not_actor_test",
        "provider": provider.public(), "thinking": {"type": "disabled"}, "output_cap": OUTPUT_CAP,
        "system_prompts": {"planner": machine.PLAN_SYSTEM, "selector": machine.SELECT_SYSTEM},
        "guide": machine.GUIDE, "official_sources": OFFICIAL, "llm_budget": budget.public(),
        "jev_complete_reserve_usd": float(MAX_JEV * JEV_RESERVE), "jev_price_usd_per_million": .042,
        "jev_max_requests": MAX_JEV, "llm_max_requests": MAX_LLM, "deadline_llm_s": 30,
        "deadline_jev_ms": 1500, "single_active_request": True, "max_starts_per_second": 2,
        "retries": 0, "execute_authorized": False, "actual_invoice": None,
        "arm_budget": "one independent real LLM plan and maximum three selector calls per arm/task"}, [provider.secret, key])
    rows = []
    started = time.perf_counter()
    for repeat in range(1, 4):
        for task in frozen_tasks["tasks"]:
            # Alternate paired order without changing frozen prompts, caps or tasks.
            order = ("llm_only", "hybrid") if repeat % 2 else ("hybrid", "llm_only")
            for arm in order:
                session.context = {"repeat": repeat, "task_id": task["task_id"], "arm": arm}
                task_start = time.perf_counter()
                calls_before = len(session.records)
                value, error, planner_refs = session.llm_request("planner", machine.PLAN_SYSTEM, machine.planner_input(task))
                plan = None if error else machine.parse_plan(value)
                row = machine.run_arm(task, plan, session.selector, session_id)
                owned = session.records[calls_before:]
                l = [r for r in owned if r["service"] == "llm"]
                j = [r for r in owned if r["service"] == "jev"]
                jev_cost = jev_cost_estimate(j)
                row.update(session.context)
                row.update(elapsed_ms=(time.perf_counter() - task_start) * 1000, model_calls=len(owned),
                           llm_calls=len(l), jev_calls=len(j), planner_refs=planner_refs, planner_error=error,
                           response_models=dict(Counter(r.get("response_model") for r in owned)),
                           llm_token_estimated_cost=llm.cost_estimate(provider, l),
                           jev_token_estimated_cost_usd=jev_cost["estimated_usd"],
                           jev_known_usage_subtotal_usd=jev_cost["known_usage_subtotal_usd"],
                           jev_missing_usage_calls=jev_cost["missing_usage_calls"],
                           actual_invoice=None)
                llm.write_json(EVIDENCE / f"task-r{repeat}-{task['task_id']}-{arm}.json", row, [provider.secret, key])
                rows.append(row)
            if int(task["task_id"][-2:]) % 5 == 0:
                print(json.dumps({"repeat": repeat, "task": task["task_id"], "calls": len(session.records),
                                  "llm": session.budget.count, "jev": len(session.jev_ledger.reservations)}), flush=True)
    elapsed = (time.perf_counter() - started) * 1000
    after = source_manifest(options.jev_harness)
    report = {"version": machine.VERSION, "session": session_id, "status": "completed" if before == after else "source_changed_reject",
              "source_before": before, "source_after": after, "source_stable": before == after,
              "v1_immutable_evidence_unchanged": before == after, "repeats": 3, "tasks_per_repeat_per_arm": 20,
              "end_to_end_session_ms": elapsed, "summaries": summarize(rows, session.records, provider),
              "tasks": rows, "total_model_calls": len(session.records), "llm_budget": session.budget.public(),
              "jev_budget": session.jev_ledger.report(), "actual_invoice": None, "execute_authorized": False,
              "hardware_dispatches": 0, "jev_execution_allowed": False,
              "no_unseen_holdout_claim": True, "no_jev_execute_acceptance": True}
    llm.write_json(EVIDENCE / "report.json", report, [provider.secret, key])
    llm.write_json(EVIDENCE / "replay-audit.json", replay_audit(EVIDENCE))
    scan = llm.scan_secrets(EVIDENCE, [provider.secret, key])
    llm.write_json(EVIDENCE / "secret-scan.json", scan)
    if scan["exact_in_memory_secret_matches"]:
        raise llm.Rejected("v2_exact_secret_scan_failed")
    print(json.dumps({"status": report["status"], "session": session_id, "model_calls": len(session.records),
                      "successes": {a: s["successes"] for a, s in report["summaries"].items()},
                      "report": str(EVIDENCE / "report.json"), "secret_matches": 0}), flush=True)
    return 0 if before == after else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live-http", action="store_true")
    parser.add_argument("--jev-harness", type=Path,
                        help="read-only helper path; default: this repository's validation_drivers/evaluate_jev_live.py")
    parser.add_argument("--freeze-spec", action="store_true")
    parser.add_argument("--output", type=Path, help="new output path required for --freeze-spec; corpus is immutable")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--scan", action="store_true")
    options = parser.parse_args()
    try:
        if options.freeze_spec:
            if options.output is None or options.output.resolve() == SPEC.resolve():
                raise llm.Rejected("freeze_requires_new_external_output")
            llm.write_json(options.output, {"version": machine.VERSION, "tasks": machine.tasks()})
            print(json.dumps({"frozen_spec": str(options.output), "tasks": 20, "network_calls": 0}))
            return 0
        if options.audit:
            print(json.dumps(replay_audit(EVIDENCE)))
            return 0
        if options.scan:
            if not options.allow_live_http:
                raise llm.Rejected("secret_scan_requires_explicit_opt_in")
            provider = llm.read_provider(llm.DB_DEFAULT, PROVIDER_ID, "openai")
            result = llm.scan_secrets(EVIDENCE, [provider.secret, read_first_key()])
            print(json.dumps(result))
            return int(result["exact_in_memory_secret_matches"] != 0)
        return execute(options)
    except llm.Rejected as exc:
        print(json.dumps({"status": "rejected", "code": str(exc)}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"status": "failed", "code": "v2_local_harness_error_redacted"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
