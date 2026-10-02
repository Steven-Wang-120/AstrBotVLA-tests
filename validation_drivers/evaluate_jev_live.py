"""Explicit paid Jev shadow experiment; never imported by the pure offline runner."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_support import bootstrap as paths
paths.bootstrap()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation_drivers import evaluate_jev_offline as offline
from astrbot_ex.core.decision.backends import jev
from astrbot_ex.core.decision.models import DecisionSnapshot

VERSION = "b07-live-shadow-v1"
PRICE = Decimal("0.042") / 1000000
# Official total request context is 64k. Reserve its binary interpretation for
# EVERY attempted call, even 401/timeouts/no usage. Reservations never refunded.
RESERVE_TOKENS = 65536
MAX_CALLS = 310
MAX_USD = Decimal("1")
EVIDENCE = paths.artifacts_path("2026-10-01-live")
AUTHORIZED_KEYS = paths.explicit_secret_path("ASTRBOTVLA_JEV_KEY_FILE")
SOURCES = ["https://docs.typesafe.ai/models", "https://docs.typesafe.ai/api",
           "https://docs.typesafe.ai/primitives/choice", "https://docs.typesafe.ai/concepts/state",
           "https://docs.typesafe.ai/model-jaggedness/jev-1.13"]


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def source_hashes():
    paths = ["validation_drivers/evaluate_jev_live.py", "validation_drivers/evaluate_jev_offline.py",
             "astrbot_ex/core/decision/backends/jev.py", "astrbot_ex/core/decision/backends/registry.py",
             "astrbot_ex/core/actions/models.py", "astrbot_ex/core/decision/models.py",
             "docs/DECISION-CONTRACT.md", "docs/DECISION-API.md",
             "fixtures/decision/jev/offline-v1.jsonl", "fixtures/decision/jev/offline-v1.manifest.json"]
    return {name: hashlib.sha256(globals()["paths"].source_path(name).read_bytes()).hexdigest() for name in paths}


class BudgetStop(RuntimeError):
    pass


class Ledger:
    """One process-exclusive session directory; durable pre-send reservations."""

    def __init__(self, directory, *, max_calls=MAX_CALLS, max_usd=MAX_USD):
        if type(max_calls) is not int or not 1 <= max_calls <= MAX_CALLS:
            raise ValueError("invalid call limit")
        if not Decimal("0") < max_usd <= MAX_USD:
            raise ValueError("invalid dollar limit")
        self.path = directory / "budget.jsonl"
        self.max_calls, self.max_usd = max_calls, max_usd
        self.lock = threading.Lock()
        self.reservations = []
        if self.path.exists():
            events = [offline.strict_json(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
            self.reservations = [e for e in events if e["event"] == "reserve"]
            for i, e in enumerate(self.reservations, 1):
                if e["call_number"] != i or e["reserved_input_tokens"] != RESERVE_TOKENS:
                    raise ValueError("invalid budget history")

    def append(self, event):
        # os is used ONLY for fsync, never environment/credential discovery.
        import os
        with self.lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(offline.canonical(event).decode("utf-8") + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def reserve(self, input_hash, request_bytes, phase):
        number = len(self.reservations) + 1
        if number > self.max_calls or number * RESERVE_TOKENS * PRICE > self.max_usd:
            raise BudgetStop("budget_exhausted")
        event = {"event": "reserve", "call_number": number, "utc": utc(), "phase": phase,
                 "input_sha256": input_hash, "request_bytes": request_bytes,
                 "reserved_input_tokens": RESERVE_TOKENS,
                 "reserved_usd": str(RESERVE_TOKENS * PRICE)}
        self.append(event)
        self.reservations.append(event)
        return number

    def report(self):
        return {"attempted_http_calls": len(self.reservations), "max_calls": self.max_calls,
                "absolute_usd_limit": str(self.max_usd), "reserve_input_tokens_per_call": RESERVE_TOKENS,
                "reserved_cost_upper_bound_usd": str(len(self.reservations) * RESERVE_TOKENS * PRICE),
                "policy": "64k documented total context, binary 65536; all failures charged full reserve; no refunds"}


def read_keys(path=AUTHORIZED_KEYS):
    """Read this explicitly authorized file only. Never return tokens to stdout."""
    if path.resolve() != AUTHORIZED_KEYS.resolve():
        raise ValueError("unauthorized_key_file")
    with path.open(encoding="utf-8-sig") as stream:
        # File has one printable token per line; do not interpret account metadata.
        values = []
        count = 0
        for line in stream:
            value = line.strip()
            if not value:
                continue
            count += 1
            if len(values) < 3:
                if not re.fullmatch(r"[A-Za-z0-9_-]{20,4096}", value):
                    raise ValueError("unsupported_redacted_key_shape")
                values.append(value)
    if not values:
        raise ValueError("empty_authorized_key_file")
    return values, {"nonempty_token_lines": count, "in_memory_candidates": len(values),
                    "candidate_lengths": [len(v) for v in values], "secret_reads": 1}


def response_projection(body, request):
    """Preserve semantic outputs only. Unknown strings/fields/headers are not logs.

    Even a hostile server echoing the Bearer token or hidden reasoning cannot
    persist it: strings must be exact public IDs from this synthetic request.
    """
    try:
        value = offline.strict_json(body.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return {"json_status": "invalid", "body_bytes": len(body)}
    if not isinstance(value, dict):
        return {"json_status": "not_object", "body_bytes": len(body)}
    output = {"json_status": "object", "body_bytes": len(body),
              "model": value.get("model") if value.get("model") == jev.PINNED_MODEL else "[redacted_or_unknown]",
              "answers": {}, "usage": None,
              "unexpected_top_level_field_count": len(set(value) - {"model", "answers", "usage"})}
    answers = value.get("answers")
    if isinstance(answers, dict):
        for owner, question in request["questions"].items():
            answer = answers.get(owner)
            if not isinstance(answer, dict):
                continue
            choice = answer.get("choice")
            owner_record = next(o for o in request["state"]["snapshot"]["owners"] if o["owner"] == owner)
            known_ids = {c["option_id"] for c in owner_record["candidates"]}
            clean = {"choice": choice if isinstance(choice, str) and choice in known_ids else "[unknown_option]",
                     "type": "choice" if answer.get("type") == "choice" else "[invalid_type]"}
            confidence = answer.get("confidence")
            clean["confidence"] = confidence if jev._probability(confidence) else None
            probabilities = answer.get("probabilities")
            if isinstance(probabilities, dict):
                clean["probabilities"] = {option: p if jev._probability(p) else None
                                           for option, p in probabilities.items() if option in question["criteria"]}
            output["answers"][owner] = clean
    usage = value.get("usage")
    if isinstance(usage, dict) and all(type(usage.get(k)) is int and 0 <= usage[k] <= 2**53 - 1
                                     for k in ("input_tokens", "output_tokens")):
        output["usage"] = {k: usage[k] for k in ("input_tokens", "output_tokens")}
    return output


class RecordingTransport:
    def __init__(self, ledger, *, inner=jev._http_transport, clock=time.monotonic, sleep=time.sleep):
        self.ledger, self.inner = ledger, inner
        self.clock, self.sleep = clock, sleep
        self.last_started = float("-inf")
        self.records = []
        self.finished = threading.Event()
        self.finished.set()
        self.phase = "probe"
        self.fatal_code = None

    def __call__(self, body, headers, deadline, cancel, max_bytes):
        self.finished.clear()
        started = self.clock()
        record = None
        try:
            wait = max(0, self.last_started + 0.5 - started)
            if wait:
                if wait >= deadline - started:
                    raise jev.JevBackendError("deadline_exceeded")
                self.sleep(wait)
            jev._remaining(deadline, cancel)
            request = offline.strict_json(body.decode("utf-8"))
            number = self.ledger.reserve(hashlib.sha256(body).hexdigest(), len(body), self.phase)
            self.last_started = self.clock()
            record = {"event": "http_result", "call_number": number, "phase": self.phase,
                      "start_utc": utc(), "input_sha256": hashlib.sha256(body).hexdigest(),
                      "request_bytes": len(body), "request": request, "status": None,
                      "response": None, "transport_error": None}
            self.records.append(record)
            # No redirect/proxy override: unchanged adapter's actual HTTP boundary.
            reply = self.inner(body, headers, deadline, cancel, max_bytes)
            record["status"] = reply.status
            record["response"] = response_projection(reply.body, request)
            usage = record["response"].get("usage")
            if usage is not None and usage["input_tokens"] > RESERVE_TOKENS:
                record["transport_error"] = "usage_exceeds_documented_context"
                raise BudgetStop("usage_exceeds_documented_context")
            return reply
        except Exception as exc:
            if isinstance(exc, BudgetStop):
                self.fatal_code = str(exc)
            if record is not None and record["transport_error"] is None:
                # Exception messages may contain credentials or response bodies.
                record["transport_error"] = ("deadline_exceeded" if isinstance(exc, TimeoutError) else
                                             "transport_failure")
            raise
        finally:
            if record is not None:
                record["end_utc"] = utc()
                record["transport_elapsed_ms"] = (self.clock() - started) * 1000
                self.ledger.append(record)
            self.finished.set()


def semantic_choices(record):
    projection = record.get("response") if record else None
    if not projection:
        return None
    return {owner: answer["choice"] for owner, answer in projection.get("answers", {}).items()}


def one_decision(backend, transport, snapshot):
    normalized = DecisionSnapshot.parse(snapshot)
    before = len(transport.records)
    started = time.monotonic()
    error, decision = None, None
    try:
        if backend.execution_allowed is not False:
            raise ValueError("execution_not_forbidden")
        decision = backend.decide(normalized)
    except jev.JevBackendError as exc:
        error = exc.code
    elapsed = (time.monotonic() - started) * 1000
    # Do not start another call while a canceled HTTP worker remains alive. This
    # wait is quarantine cleanup, NOT an extension of the accepted 1500ms result.
    if not transport.finished.wait(10):
        raise BudgetStop("quarantined_transport_still_running")
    worker = backend._worker
    if worker is not None:
        worker.join(timeout=0.1)
        if worker.is_alive():
            raise BudgetStop("quarantined_adapter_worker_still_running")
    record = copy.deepcopy(transport.records[-1]) if len(transport.records) > before else None
    if transport.fatal_code is not None:
        raise BudgetStop(transport.fatal_code)
    post = {c["owner"]: c["option_id"] for c in decision.choices} if decision is not None else None
    return {"elapsed_ms": elapsed, "reject_code": error, "execution_allowed": False,
            "input_snapshot_sha256": offline.digest(normalized.to_dict()),
            "adapter_record": asdict(backend.last_record) if backend.last_record else None,
            "http": record, "raw_choices": semantic_choices(record), "post_override_choices": post}


def aggregate(rows, scope):
    result = offline.summary(rows)
    result["latency_ms"]["scope"] = scope
    errors = sum(r.get("cloud_reject_code", r["selector_error"]) is not None for r in rows)
    timeouts = sum(r.get("cloud_reject_code", r["selector_error"]) in
                   ("deadline_exceeded", "retry_exceeds_deadline") for r in rows)
    result["error_rate"] = errors / len(rows) if rows else None
    result["timeout_rate"] = timeouts / len(rows) if rows else None
    return result


def groups(rows, scope):
    return {"overall": aggregate(rows, scope),
            **{s: aggregate([r for r in rows if r["split"] == s], scope) for s in ("development", "holdout")},
            "by_category": {c: aggregate([r for r in rows if r["category"] == c], scope) for c in offline.COUNTS},
            "by_split_and_category": {s: {c: aggregate([r for r in rows if r["category"] == c and r["split"] == s], scope)
                                            for c in offline.COUNTS} for s in ("development", "holdout")},
            "by_repeat_and_split": {str(i): {s: aggregate([r for r in rows if r["repeat"] == i and r["split"] == s], scope)
                                              for s in ("development", "holdout")} for i in (1, 2, 3)}}


def measured_usage(records):
    usages = [r["response"]["usage"] for r in records if r.get("response") and r["response"].get("usage")]
    tokens = sum(u["input_tokens"] for u in usages)
    return {"input_tokens": tokens if usages else None,
            "output_tokens": sum(u["output_tokens"] for u in usages) if usages else None,
            "requests_with_usage": len(usages), "requests_without_usage": len(records) - len(usages),
            "usage_estimated_cost_usd": str(tokens * PRICE) if usages else None,
            "price_input_usd_per_million": "0.042", "price_output_usd_per_million": "0",
            "actual_bill": None}


def evaluate(backend, transport, scenes, manifest, *, journal=None):
    rows = {"jev_raw": [], "jev_post_override": [], "rule": []}
    calls = []
    for repeat in (1, 2, 3):
        for scene in scenes:
            # Only the snapshot goes to selectors. Labels are consulted AFTER
            # both selections; neither category/split nor labels enter request.
            snapshot = DecisionSnapshot.parse(scene["snapshot"]).to_dict()
            result = one_decision(backend, transport, copy.deepcopy(snapshot))
            started = time.perf_counter_ns()
            rule_choices = offline.rule(copy.deepcopy(snapshot))
            rule_elapsed = (time.perf_counter_ns() - started) / 1000000
            call = {"scene_id": scene["scene_id"], "repeat": repeat, **result}
            calls.append(call)
            if journal is not None:
                journal(call)
            raw_error = result["reject_code"] if result["raw_choices"] is None else None
            for name, choices, elapsed, error in (
                ("jev_raw", result["raw_choices"], result["elapsed_ms"], raw_error),
                ("jev_post_override", result["post_override_choices"], result["elapsed_ms"], result["reject_code"]),
                ("rule", rule_choices, rule_elapsed, None),
            ):
                row = offline.score_scene(scene, choices, elapsed, error)
                row["repeat"] = repeat
                if name != "rule":
                    row["cloud_reject_code"] = result["reject_code"]
                rows[name].append(row)
    return {"dataset_version": manifest["dataset_version"], "corpus_sha256": manifest["corpus_sha256"],
            "split_sha256": manifest["split_sha256"], "repeats": 3, "complete": len(calls) == 300,
            "summaries": {k: groups(v, "local_rule_only" if k == "rule" else "cloud_adapter_decide_including_validation_excluding_rate_wait_cleanup")
                          for k, v in rows.items()}, "rows": rows, "decisions": calls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live-http", action="store_true")
    parser.add_argument("--phase", choices=("probe", "evaluate"), required=True)
    options = parser.parse_args()
    if not options.allow_live_http:
        parser.error("explicit --allow-live-http required; no secret read or HTTP by default")
    if AUTHORIZED_KEYS is None:
        parser.error("explicit --jev-key-file required; no ambient credential discovery")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    # A stale lock is NOT automatically deleted; coordinator must inspect first.
    lock_path = EVIDENCE / "live-session.lock"
    lock = lock_path.open("x", encoding="utf-8")
    lock.write(utc())
    lock.flush()
    backend = None
    started = utc()
    hashes = source_hashes()
    report = {"version": VERSION, "phase": options.phase, "start_utc": started,
              "source_sha256": hashes, "real_jev_measured": False, "real_llm_measured": False,
              "execution_allowed": False, "execute_authorized": False, "actual_bill": None,
              "scope": "synthetic_snapshot_selection_not_robot_tasks_or_physical_execution_safety",
              "official_sources": SOURCES}
    exit_code = 0
    try:
        scenes, manifest = offline.load_corpus()
        ledger = Ledger(EVIDENCE)
        transport = RecordingTransport(ledger)
        transport.phase = options.phase
        if options.phase == "evaluate":
            probe = offline.strict_json((EVIDENCE / "probe-report.json").read_text(encoding="utf-8"))
            if not probe.get("probe_accepted"):
                raise BudgetStop("probe_not_accepted_report_to_coordinator")
            key_index = probe["selected_key_index"]
        else:
            if (EVIDENCE / "probe-report.json").exists():
                raise BudgetStop("probe_report_already_exists")
            key_index = 0
        if (EVIDENCE / (options.phase + "-report.json")).exists():
            raise BudgetStop("report_already_exists")
        keys, shape = read_keys()
        report["credential_shape"] = shape
        config = jev.JevConfig(model=jev.PINNED_MODEL, mode="shadow", allow_live_http=True,
                               deadline_ms=1500, min_interval_ms=500, max_retries=0, min_confidence=0.6)
        report["config"] = asdict(config)
        if options.phase == "probe":
            probes = []
            while True:
                backend = jev.JevBackend(config, transport=transport, secret_provider=lambda: keys[key_index])
                result = one_decision(backend, transport, scenes[0]["snapshot"])
                probes.append({"key_index": key_index, **result})
                # Only 401 permits selecting next authorized key; never rotate on
                # timeout/429/overload. No automatic retries on any failure.
                if result["http"] and result["http"]["status"] == 401 and key_index < len(keys) - 1:
                    backend.close()
                    key_index += 1
                    continue
                break
            if result["reject_code"] is None:
                transport.sleep(max(0, transport.last_started + 0.5 - time.monotonic()))
                probes.append({"key_index": key_index, **one_decision(backend, transport, scenes[1]["snapshot"])})
            report.update({"probes": probes, "selected_key_index": key_index,
                           "probe_accepted": all(p["reject_code"] is None for p in probes if p["key_index"] == key_index)})
            if not report["probe_accepted"]:
                exit_code = 2
        else:
            backend = jev.JevBackend(config, transport=transport, secret_provider=lambda: keys[key_index])
            def journal(call):
                ledger.append({"event": "decision", **call})
                transport.sleep(max(0, transport.last_started + 0.5 - time.monotonic()))
            report.update(evaluate(backend, transport, scenes, manifest, journal=journal))
        report["budget"] = ledger.report()
        all_events = [offline.strict_json(line) for line in ledger.path.read_text(encoding="utf-8").splitlines()]
        all_http = [e for e in all_events if e["event"] == "http_result"]
        report["total_session_usage_including_probes"] = measured_usage(all_http)
        report["phase_usage"] = measured_usage(transport.records)
        report["real_jev_measured"] = bool(transport.records)
    except Exception as exc:
        # Never print exception message, traceback, locals, headers, raw bodies.
        report["fatal_code"] = str(exc) if isinstance(exc, BudgetStop) else "local_harness_failure"
        exit_code = 2
    finally:
        if backend is not None:
            backend.close()
        report["end_utc"] = utc()
        report["source_unchanged"] = hashes == source_hashes()
        report["exit_code"] = exit_code
        output = EVIDENCE / (options.phase + "-report.json")
        if not output.exists():
            write_new(output, report)
        lock.close()
        lock_path.unlink()
    print(json.dumps({k: report.get(k) for k in ("phase", "exit_code", "probe_accepted", "complete", "fatal_code", "budget", "phase_usage")}, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
