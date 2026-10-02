"""Independent no-network v2 evidence audit; no provider/key reads."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_support import bootstrap as paths
paths.bootstrap()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_drivers import b07_virtual_tasks_v2 as m
from validation_drivers import evaluate_virtual_closed_loop_v2 as v

OUT = paths.artifacts_path("2026-10-01-llm-v2-continuation")


def verify_sources(report, source_archive=None, source_roots=()):
    before, after = report.get("source_before"), report.get("source_after")
    if not isinstance(before, dict) or not before or before != after:
        raise ValueError("historical_source_before_after_mismatch")
    mappings = {}
    if source_archive is not None:
        manifest_path = Path(source_archive).resolve()
        if manifest_path.is_dir():
            manifest_path = manifest_path / "historical-source-map.json"
        manifest = v.llm.frozen.strict_json(manifest_path.read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "files"} or
            type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or
            not isinstance(manifest["files"], dict) or set(manifest["files"]) - set(before)):
            raise ValueError("invalid_source_archive_identity_map")
        mappings = manifest["files"]
    roots = []
    for old, new in source_roots:
        old_path = PureWindowsPath(old) if PureWindowsPath(old).is_absolute() else Path(old)
        if not old_path.is_absolute() or ".." in old_path.parts:
            raise ValueError("invalid_historical_source_root")
        roots.append((old_path, Path(new).resolve()))
    verified = []
    counts = Counter()
    for identity, expected in before.items():
        historical = PureWindowsPath(identity) if PureWindowsPath(identity).is_absolute() else Path(identity)
        if (not historical.is_absolute() or ".." in historical.parts or not isinstance(expected, str) or
            len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)):
            raise ValueError("invalid_historical_source_identity_or_sha")
        if identity in mappings:
            entry = mappings[identity]
            if (not isinstance(entry, dict) or set(entry) != {"path", "sha256"} or
                not isinstance(entry["path"], str) or not entry["path"] or entry["sha256"] != expected):
                raise ValueError("source_archive_identity_sha_mismatch")
            path = Path(entry["path"])
            if not path.is_absolute():
                path = manifest_path.parent / path
            origin = "archive"
        else:
            relocated = []
            for old, new in roots:
                if type(old) is not type(historical):
                    continue
                try:
                    relative = historical.relative_to(old)
                except ValueError:
                    continue
                relocated.append(new.joinpath(*relative.parts))
            if len(relocated) > 1:
                raise ValueError("ambiguous_historical_source_root")
            path = relocated[0] if relocated else Path(identity)
            origin = "source_root" if relocated else "original_path"
        # An explicitly mapped missing/tampered archive never falls back to current source.
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("historical_source_bytes_sha_mismatch")
        counts[origin] += 1
        verified.append({"historical_identity": identity, "sha256": actual,
                         "verified_path": str(path.resolve()), "origin": origin})
    return {"files_verified": len(verified), "origins": dict(counts),
            "historical_identity_sha256": m.digest(before), "verified_mapping_sha256": m.digest(verified),
            "source_before_equals_source_after": True}


def audit(evidence_dir=OUT, source_archive=None, source_roots=()):
    evidence_dir = Path(evidence_dir)
    report = v.llm.frozen.strict_json((evidence_dir / "report.json").read_text(encoding="utf-8"))
    source_verification = verify_sources(report, source_archive, source_roots)
    calls = [v.llm.frozen.strict_json(p.read_text(encoding="utf-8")) for p in sorted(evidence_dir.glob("call-*.json"))]
    by_ref = {r["call_ref"]: r for r in calls}
    assert len(calls) == len(by_ref)
    assert len(report["tasks"]) == 120
    assert len({(r["repeat"], r["task_id"], r["arm"]) for r in report["tasks"]}) == 120
    assert report["repeats"] == 3
    assert v.replay_audit(evidence_dir)["status"] == "passed"
    unknown = 0
    for record in calls:
        if record["service"] == "llm":
            request = record["request"]
            assert request["thinking"] == {"type": "disabled"}
            assert request["max_tokens"] == 512
            assert request["model"] == "deepseek-v4-flash"
            assert hashlib.sha256(m.canonical(request)).hexdigest() == record["input_sha256"]
            assert request["messages"][0]["content"] == (m.PLAN_SYSTEM if record["phase"] == "planner" else m.SELECT_SYSTEM)
            if record.get("response_model"):
                assert record["response_model"] in ("deepseek-v4-flash", "deepseek-flash")
            else:
                unknown += 1
            if record["validation_error"] is None:
                assert json.loads(record["raw_final_text"]) == record["output"]
        else:
            assert record["requested_model"] == "jev-1.13.0"
            assert record["request"]["model"] == "jev-1.13.0"
            assert record["execution_allowed"] is False
        text = json.dumps(record["request"])
        for name in ("expected_answers", "acceptable_joint_choices", "desired_operation", "reasonable_options"):
            assert name not in text
    assert unknown == report["uncertain_interrupted_attempts"] == 1
    all_tasks = {t["task_id"]: t for t in m.tasks()}
    for row in report["tasks"]:
        task = all_tasks[row["task_id"]]
        owned = [r for r in calls if (r["repeat"], r["task_id"], r["arm"]) == (row["repeat"], row["task_id"], row["arm"])]
        planners = [r for r in owned if r["phase"] == "planner"]
        assert len(planners) == 1
        assert planners[0]["request"]["messages"][1]["content"] == m.canonical(m.planner_input(task)).decode()
        if row["plan"] is not None:
            assert row["plan"] == m.parse_plan(planners[0]["output"])
        for step in row["steps"]:
            assert len(step["call_refs"]) <= 1
            if step["call_refs"]:
                call = by_ref[step["call_refs"][0]]
                assert call in owned
                assert step["choices"] == call["output"]
                request = call["request"]
                if call["service"] == "llm":
                    snapshot = json.loads(request["messages"][1]["content"])["snapshot"]
                else:
                    snapshot = request["state"]["snapshot"]
                expected = v.DecisionSnapshot.parse(m.snapshot(task, step["before"], step["goal_key"], report["session"])).to_dict()
                assert snapshot == expected
                assert m.digest(m.snapshot(task, step["before"], step["goal_key"], report["session"])) == step["snapshot_sha256"]
        assert row["model_calls"] == len(owned)
        assert row["llm_calls"] == sum(r["service"] == "llm" for r in owned)
        assert row["jev_calls"] == sum(r["service"] == "jev" for r in owned)
    llm_calls = sum(r["service"] == "llm" for r in calls)
    jev_calls = sum(r["service"] == "jev" for r in calls)
    assert llm_calls <= 300 and jev_calls <= 180
    assert llm_calls == report["llm_budget"]["calls_reserved"]
    assert jev_calls == report["jev_budget"]["attempted_http_calls"]
    assert Decimal(str(report["llm_budget"]["reserved_usd"])) <= 1
    assert Decimal(report["jev_budget"]["reserved_cost_upper_bound_usd"]) <= 1
    for arm in ("llm_only", "hybrid"):
        rows = [r for r in report["tasks"] if r["arm"] == arm]
        owned = [r for r in calls if r["arm"] == arm]
        summary = report["summaries"][arm]
        assert len(rows) == summary["tasks"] == 60
        assert sum(r["success"] for r in rows) == summary["successes"]
        assert len(owned) == summary["model_calls"]
        assert summary["task_end_to_end_ms"] == v.percentiles([r["elapsed_ms"] for r in rows])
        assert summary["request_latency_ms"] == v.percentiles([r["elapsed_ms"] for r in owned if r.get("elapsed_ms") is not None])
        assert summary["sum_task_end_to_end_ms"] == sum(r["elapsed_ms"] for r in rows)
    source_verification = verify_sources(report, source_archive, source_roots)
    return {"status": "passed", "source_verification": source_verification, "arm_tasks": 120, "llm_reservations": llm_calls,
            "observed_llm_responses": llm_calls - 1, "uncertain_interrupted_attempts": 1,
            "jev_http_calls": jev_calls, "all_call_inputs_and_outputs_linked": True,
            "terminal_state_success_replayed": True, "all_sources_and_v1_evidence_sha_verified": True,
            "network_calls": 0, "successes": dict(Counter(r["arm"] for r in report["tasks"] if r["success"]))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=OUT)
    parser.add_argument("--source-archive", type=Path,
                        help="identity-to-path/SHA JSON map, or directory containing historical-source-map.json")
    parser.add_argument("--source-root", nargs=2, action="append", default=[], metavar=("HISTORICAL_ROOT", "CURRENT_ROOT"),
                        help="explicit root relocation for sources not in the archive; repeatable")
    options = parser.parse_args()
    try:
        result = audit(options.evidence_dir, options.source_archive, options.source_root)
    except (ValueError, OSError, AssertionError, KeyError, TypeError):
        print(json.dumps({"status": "rejected", "code": "evidence_or_historical_source_verification_failed", "network_calls": 0}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
