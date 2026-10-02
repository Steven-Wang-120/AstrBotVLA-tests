"""Deterministic offline repetition of reviewed cases, NOT real B04 integration."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import unittest
from pathlib import Path

PREFIX = "astrbot_plugin_astrbotex_interaction.tests."
CASES = {
    "P01": ["test_task_coordinator.CoordinatorTests.test_P01_three_steps_and_accepted_not_completed",
            "test_planning_tools.PlanningTests.test_P01_fixed_fake_LLM_coordinator_completes_three_steps",
            "test_planning_tools.PlanningTests.test_actual_public_Context_llm_generate_with_fake_provider"],
    "P02": ["test_task_coordinator.CoordinatorTests.test_P02_failure_replans_suffix_without_first_replay"],
    "P03": ["test_feedback_sync.FeedbackTests.test_P03_out_of_order_gap_sync_and_duplicate",
            "test_feedback_sync.FeedbackTests.test_P03_trimmed_history_state_full_restore"],
    "P04": ["test_task_coordinator.CoordinatorTests.test_P04_submit_timeout_same_request_and_one_goal"],
    "P05": ["test_task_store.TaskStoreTests.test_P05_owner_and_creation_idempotency"],
    "P06": ["test_task_coordinator.CoordinatorTests.test_P06_unknown_invalid_and_oversize_never_submit",
            "test_planning_tools.PlanningTests.test_P06_batch_validation_before_side_effect"],
    "P07": ["test_task_coordinator.CoordinatorTests.test_P07_replacement_requires_confirmed_stop",
            "test_task_store.TaskStoreTests.test_CAS_intents_and_P07_generation",
            "test_output_isolation.IsolationTests.test_P07_late_LLM_result_has_no_send_or_submit"],
    "P08": ["test_task_store.TaskStoreTests.test_P08_restart_review_no_replay",
            "test_task_coordinator.CoordinatorTests.test_P08_coordinator_start_does_not_resume"],
    "P09": ["test_planning_tools.PlanningTests.test_P09_raw_text_empty_final_errors_chunks_not_success",
            "test_planning_tools.PlanningTests.test_P09_round_limit"],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path, required=True)
    options = parser.parse_args()
    from validation_support.host_lifecycle import framework_baseline, thread_leaks
    baseline, framework = framework_baseline()
    records = []
    for scenario, cases in CASES.items():
        for iteration in range(1, 21):
            print(f"CAMPAIGN {scenario} iteration={iteration}/20", flush=True)
            suite = unittest.defaultTestLoader.loadTestsFromNames([PREFIX + case for case in cases])
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            record = {"scenario": scenario, "iteration": iteration, "cases": cases,
                      "tests_run": result.testsRun, "failures": len(result.failures),
                      "errors": len(result.errors), "skipped": len(result.skipped),
                      "ok": result.wasSuccessful() and not result.skipped}
            records.append(record)
            print("ROUND_RESULT " + json.dumps(record, sort_keys=True), flush=True)
    summary = {"scope": "real Host container, offline fake provider/EX; NOT real B04 combination",
               "rounds": len(records), "tests_run": sum(r["tests_run"] for r in records),
               "failed_rounds": sum(not r["ok"] for r in records), "records": records}
    summary['framework_baseline'] = framework
    summary['thread_leaks'] = thread_leaks(baseline)
    summary['ok'] = not summary['failed_rounds'] and not summary['thread_leaks']
    options.result.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print("CAMPAIGN_RESULT " + json.dumps(summary, sort_keys=True), flush=True)
    return int(not summary['ok'])


if __name__ == "__main__":
    sys.exit(main())
