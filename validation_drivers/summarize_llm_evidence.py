"""Rebuild a live-run summary from immutable per-request evidence, no HTTP/DB.

Useful after an interrupted orchestration wrapper. Never turns fabricated tests
into model evidence: only explicitly named workspace evidence is processed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_support import bootstrap as paths
paths.bootstrap()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_drivers import evaluate_llm_live as live


def rebuild(run):
    run = run.resolve()
    if run.parent != live.EVIDENCE_ROOT.resolve():
        raise ValueError("run outside assigned workspace evidence root")
    preflight = json.loads((run / "preflight.json").read_text(encoding="utf-8"))
    scenes, manifest = live.frozen.load_corpus()
    if preflight["corpus_sha256"] != manifest["corpus_sha256"]:
        raise ValueError("corpus changed")
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(run.glob("result-*.json"))]
    plans = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(run.glob("request-*.json"))]
    for record in records:
        if record["host"] != preflight["provider"]["host"] or record["requested_model"] != preflight["provider"]["requested_model"]:
            raise ValueError("provider changed")
    evaluation = [r for r in records if r["phase"] == "evaluation"]
    llm_runs, rule_runs = [], []
    for repeat in range(1, 4):
        rr = [r for r in evaluation if r["repeat"] == repeat]
        if not rr:
            continue
        if [r["scene_id"] for r in rr] != [s["scene_id"] for s in scenes[:len(rr)]]:
            raise ValueError("noncontiguous or duplicate corpus replay")
        llm_rows, rule_rows = [], []
        for record, scene in zip(rr, scenes):
            _, metadata = live.make_request(live.Provider("https://" + record["host"], record["requested_model"],
                                                          record["protocol"], "unused-no-network-secret"), scene["snapshot"])
            if metadata["snapshot_sha256"] != record["snapshot_sha256"] or metadata["input_sha256"] != record["input_sha256"]:
                raise ValueError("input changed")
            row = live.frozen.score_scene(scene, record["choices"], record["elapsed_ms"], record["validation_error"])
            row.update({"request_error": record["error"], "snapshot_sha256": record["snapshot_sha256"]})
            llm_rows.append(row)
            snap = live.DecisionSnapshot.parse(scene["snapshot"]).to_dict()
            # Quality replay only: do not invent historical rule latency here.
            rule_rows.append(live.frozen.score_scene(scene, live.frozen.rule(snap), 0))
        llm_runs.append({"repeat": repeat, "rows": llm_rows})
        rule_runs.append({"repeat": repeat, "rows": rule_rows})
    complete = len(llm_runs) == 3 and all(len(r["rows"]) == 100 for r in llm_runs)
    totals = live.usage_summary(records)
    pricing = preflight["budget_preflight"]["pricing"]
    provider = live.Provider("https://" + preflight["provider"]["host"], preflight["provider"]["requested_model"],
                             preflight["provider"]["protocol"], "unused", pricing=pricing)
    result = {"status": "completed_evidence" if complete else "partial_interrupted_evidence_not_complete_benchmark",
              "scope": preflight["scope"], "provider": preflight["provider"],
              "corpus_sha256": manifest["corpus_sha256"], "prompt_version": preflight["prompt_version"],
              "source_sha256": preflight["source_sha256"], "source_matches_current": live.source_manifest() == preflight["source_sha256"],
              "completed_requests": len(records), "started_requests": len(plans), "evaluation_requests": len(evaluation),
              "unreturned_requests": sorted(set(r["request_number"] for r in plans) - set(r["request_number"] for r in records)),
              "complete_repeats": sum(len(r["rows"]) == 100 for r in llm_runs),
              "failure_counts": dict(Counter(r["validation_error"] for r in records if r["validation_error"])),
              "response_models": dict(Counter(r.get("response_model") for r in records if r.get("response_model"))),
              "actual_usage": totals, "cost_estimate": live.cost_estimate(provider, records),
              "actual_bill": None, "task_success": None, "execute_authorized": False,
              "cloud_serial_sum_ms_including_probe": sum(r["elapsed_ms"] for r in records),
              "cloud_serial_sum_ms_evaluation_only": sum(r["elapsed_ms"] for r in evaluation),
              "summaries": {"real_llm": live.groups(llm_runs, True), "rule_quality_replay": live.groups(rule_runs, False)},
              "rule_latency": "not remeasured; null here; use original report for measured rule latency",
              "jev_comparison": "pending_sibling_report"}
    def omit_replay_latency(value):
        if isinstance(value, dict):
            if "latency_ms" in value:
                value["latency_ms"] = None
            for item in value.values():
                omit_replay_latency(item)
    omit_replay_latency(result["summaries"]["rule_quality_replay"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    opts = parser.parse_args()
    if not opts.output.resolve().is_relative_to(live.EVIDENCE_ROOT.resolve()):
        raise SystemExit("output outside assigned workspace")
    result = rebuild(opts.run)
    live.write_json(opts.output, result)
    print(json.dumps({"status": result["status"], "evaluation_requests": result["evaluation_requests"],
                      "complete_repeats": result["complete_repeats"], "output": str(opts.output)}))


if __name__ == "__main__":
    main()
