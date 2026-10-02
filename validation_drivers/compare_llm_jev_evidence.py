"""Compare public real selector reports with a common scorer; no HTTP or keys."""
from __future__ import annotations

import argparse
import hashlib
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


def compare(llm_path, jev_path):
    llm_bytes, jev_bytes = llm_path.read_bytes(), jev_path.read_bytes()
    llm, jev = json.loads(llm_bytes), json.loads(jev_bytes)
    scenes, manifest = live.frozen.load_corpus()
    if (llm["status"] != "completed" or not llm["source_stable_through_run"] or
        not jev["complete"] or not jev["source_unchanged"] or not jev["real_jev_measured"]):
        raise ValueError("reports not complete and source-stable")
    if llm["corpus_sha256"] != jev["corpus_sha256"] or llm["corpus_sha256"] != manifest["corpus_sha256"]:
        raise ValueError("corpus mismatch")
    if llm["split_sha256"] != jev["split_sha256"] or llm["repeats"] != 3 or jev["repeats"] != 3:
        raise ValueError("split/repeat mismatch")
    llm_sources = {k.replace("\\", "/"): v for k, v in llm["source_sha256"].items()}
    for key in ("astrbot_ex/core/actions/models.py", "astrbot_ex/core/decision/models.py", "validation_drivers/evaluate_jev_offline.py"):
        if llm_sources[key] != jev["source_sha256"][key]:
            raise ValueError("common source mismatch")
    if len(jev["decisions"]) != 300:
        raise ValueError("incomplete Jev choices")
    mapping = {(d["repeat"], d["scene_id"]): d for d in jev["decisions"]}
    if len(mapping) != 300:
        raise ValueError("duplicate Jev choices")
    raw_runs, post_runs = [], []
    transport_latencies, reject_counts, confidence_overrides = [], Counter(), 0
    for run in llm["runs"]["real_llm"]:
        if len(run["rows"]) != 100 or [r["scene_id"] for r in run["rows"]] != [s["scene_id"] for s in scenes]:
            raise ValueError("incomplete or unordered LLM replay")
        raw_rows, post_rows = [], []
        for row, scene in zip(run["rows"], scenes):
            decision = mapping[(run["repeat"], row["scene_id"])]
            snap = live.DecisionSnapshot.parse(scene["snapshot"]).to_dict()
            expected_hash = live.frozen.digest(snap)
            if expected_hash != row["snapshot_sha256"] or expected_hash != decision["input_snapshot_sha256"]:
                raise ValueError("normalized snapshot mismatch")
            request_snap = decision["http"]["request"]["state"]["snapshot"]
            if live.frozen.digest(request_snap) != expected_hash:
                raise ValueError("Jev request snapshot mismatch")
            transport_latencies.append(decision["http"]["transport_elapsed_ms"])
            error = decision["reject_code"]
            if error:
                reject_counts[error] += 1
            confidence_overrides += decision["adapter_record"].get("conservative_overrides", 0)
            # Raw vendor choices are an unconstrained semantic diagnostic. They
            # may survive despite probability/confidence admission rejection.
            raw = live.frozen.score_scene(scene, decision["raw_choices"], decision["elapsed_ms"])
            post = live.frozen.score_scene(scene, decision["post_override_choices"], decision["elapsed_ms"], error)
            raw["request_error"] = "timeout" if error == "timeout" else None
            post["request_error"] = "timeout" if error == "timeout" else error
            raw_rows.append(raw)
            post_rows.append(post)
        raw_runs.append({"repeat": run["repeat"], "rows": raw_rows})
        post_runs.append({"repeat": run["repeat"], "rows": post_rows})
    raw_summaries, post_summaries = live.groups(raw_runs, True), live.groups(post_runs, True)
    def set_scope(value):
        if isinstance(value, dict):
            if "latency_ms" in value:
                value["latency_ms"]["scope"] = "Jev_adapter_decide_including_validation_excluding_rate_wait_cleanup"
            for child in value.values():
                set_scope(child)
    set_scope(raw_summaries)
    set_scope(post_summaries)
    result = {"scope": "same_snapshot_scheduler_selection_microbenchmark_not_LLM_planning_plus_Jev_task_success",
              "corpus_sha256": manifest["corpus_sha256"], "split_sha256": manifest["split_sha256"],
              "snapshots_checked": 300, "common_frozen_sources_match": True,
              "input_report_sha256": {"llm": hashlib.sha256(llm_bytes).hexdigest(), "jev": hashlib.sha256(jev_bytes).hexdigest()},
              "source_sha256": {"llm": llm["source_sha256"], "jev": jev["source_sha256"]},
              "llm_provider": llm["provider"], "llm_response_models": llm["response_models"],
              "jev_model": jev["config"]["model"], "prompts_identical": False,
              "timeout_s": {"llm": llm["operating_parameters"]["timeout_s"], "jev": jev["config"]["deadline_ms"] / 1000},
              "llm_output_token_cap": 512, "jev_confidence_threshold": jev["config"]["min_confidence"],
              "jev_confidence_overrides": confidence_overrides, "jev_adapter_reject_counts": dict(reject_counts),
              "selection_notes": {"llm": "actual JSON selection; truncated responses remain invalid; no fallback",
                                  "jev_raw": "raw vendor choices before confidence overrides and full distribution validation; NOT admissible commands",
                                  "jev_post_override": "adapter validated result after confidence overrides; admission rejects retained",
                                  "rule": "corpus-aware hand-authored deterministic selector; not B04 safety gate"},
              "latency_scope_warning": "LLM includes spawned-process startup/cleanup, Jev adapter scope does not; distinct protocols/prompts/deadlines. No pure model speed attribution.",
              "evaluation_calls": {"llm": 300, "jev": 300, "rule_cloud_calls": 0},
              "session_calls_including_probes": {"llm_successful_provider": llm["network_requests"],
                                                "llm_all_authorized_providers": live.ledger_counts(live.EVIDENCE_ROOT)[0],
                                                "jev": jev["budget"]["attempted_http_calls"]},
              "actual_usage_evaluation_only": {"llm": llm["actual_usage_evaluation_only"], "jev": jev["phase_usage"]},
              "actual_usage_session": {"llm_successful_provider": llm["actual_usage_including_probe"],
                                       "jev": jev["total_session_usage_including_probes"]},
              "estimated_cost_evaluation_only": {"llm": llm["cost_evaluation_only"], "jev": jev["phase_usage"]["usage_estimated_cost_usd"]},
              "estimated_cost_including_probes": {"llm_successful_provider": llm["cost_including_probe"],
                                                 "jev": jev["total_session_usage_including_probes"]["usage_estimated_cost_usd"],
                                                 "llm_all_providers": None},
              "actual_bill": {"llm": None, "jev": None}, "task_success": None,
              "execute_authorized": False, "real_multistep_LLM_planning_plus_Jev_comparison": "NOT_MEASURED",
              "sums_evaluation_latency_ms": {"llm": sum(r["elapsed_ms"] for run in llm["runs"]["real_llm"] for r in run["rows"]),
                                             "jev_adapter": sum(d["elapsed_ms"] for d in jev["decisions"]),
                                             "jev_transport": sum(transport_latencies)},
              "summaries": {"real_llm": llm["summaries"]["real_llm"], "jev_raw": raw_summaries,
                            "jev_post_override": post_summaries, "rule": llm["summaries"]["rule"]}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", type=Path, required=True)
    parser.add_argument("--jev", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    opts = parser.parse_args()
    if not opts.output.resolve().is_relative_to(live.EVIDENCE_ROOT.resolve()):
        raise SystemExit("output outside assigned workspace")
    result = compare(opts.llm, opts.jev)
    live.write_json(opts.output, result)
    print(json.dumps({"snapshots_checked": result["snapshots_checked"], "output": str(opts.output),
                      "joint_match": {k: v["all"]["joint_label_match_rate"] for k, v in result["summaries"].items()}}))


if __name__ == "__main__":
    main()
