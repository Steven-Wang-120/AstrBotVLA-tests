# B07 v2 finite metrics correctness closeout — 2026-10-01

**New CLI source correction only, separate from the historical experiment.**
No new vendor HTTP/provider or key reads, no change to success/state/labels,
prompts, tasks, thresholds, gates, deadline/budget or invoice claims. No original
`task-evidence`/v1/core/B00/corpus/source archive/report/supplement writes. Failed
logs from every earlier stage remain untouched.

## Exact code changes

- `evaluate_virtual_closed_loop_v2.jev_cost_estimate(records)` computes only
  `Decimal(input_tokens) * Decimal("0.042") / Decimal(1000000)`, accumulating
  in Decimal before converting the public JSON values. Output tokens are free.
  Missing/invalid input usage produces **null total**, an explicit
  `known_usage_subtotal_usd` and `missing_usage_calls`, not fabricated zero cost.
  Actual zero observed input tokens is valid; no attempted calls has a zero known
  cost. Actual invoices remain null.
- The same helper is now used by arm summaries, individual Jev-call records and
  per-task rows in **both** original and continuation drivers. Each task has
  `jev_token_estimated_cost_usd`, `jev_known_usage_subtotal_usd`, and
  `jev_missing_usage_calls`. There is no remaining input+output charge in these
  two new CLI drivers. Existing old result numbers are not rewritten.
- `summarize()` accepts missing request elapsed values directly, computes
  p50/p95/max from known values only, reports `unobserved_latency_attempts`, sets
  complete `sum_request_ms` to **null if any duration is unknown**, and separately
  records `sum_known_request_ms`. All-unknown quantiles are null, not zero.
- Continuation no longer makes copies with missing elapsed replaced by zero.
  Recovered prior RTT sums known durations only. Per-task active duration is
  null when any owned call duration is unknown, with separately named
  `known_active_elapsed_ms` and a scope that excludes interruption idle gap.
  Task quantiles/sums also preserve null complete totals, explicit unknown task
  count and known-duration subtotal; unknown interrupted calls cannot become
  complete task time. State/outcome itself is unchanged.

## Historical separation and immutable pins

Before modifying the two drivers, this stage archived their current original
bytes/SHA in `review-evidence/2026-10-01-v2-metrics/preimage/` and
`preimage-sha256.json`. It also hashes **1781** pre-existing task-evidence,
review/archive/failed-log and v1 source/doc files. Prior source archives stay
byte-identical; their historical SHA values are not replaced with current SHA.

The original live report's overestimated Jev output charge and incomplete timing
sum are retained as originally measured/disclosed. Its existing
`official-price-supplement.json` remains the official input-only historical cost
reference; this correction does not create another experiment, improve old
successes or retroactively claim complete interrupted latency.

The original historical source map from the preceding stage still verifies all
**818** source identities/SHA (3 correct archived originals plus 815 real
unchanged original paths), same identity-map SHA:
`430d004de0cb3f1364b0a15c2afc2f0ee726dbefa9ab08455630d275bf5893e9`.
Independent historical replay remains 120 state/call-linked task attempts,
LLM288reserved/287responses, Jev177, successesLLM51/hybrid38. It is not recalculated
using current cost/latency formulas, and report/supplement/old audit stay unchanged.

## Finite necessary tests / actual exits

**75/75 tests, no skips, 5.562s, exit 0**:

- 25 existing v2 virtual/continuation/replay tests;
- 4 new `tests/test_virtual_metrics_v2.py` regressions: output>0 billed free,
  stable Decimal accumulation, missing/invalid usage null total + known subtotal,
  actual zero input valid; known-only request quantiles, missing total null and
  explicit subtotal/count; unknown task duration null total; fake Jev per-call
  records use input-only cost and no live HTTP;
- 7 historical source archive/relocation/missing/tampering tests;
- 12 v1 LLM and 27 Jev existing regressions.

Historical source/state audit **exit 0**, retaining original success/count/SHA.
All newly generated stdout/stderr/exit goes only in this stage's review-evidence:
`full-tests-final.*`, `historical-audit.*`, `immutable-seal.*` plus `metrics.diff`
and `verification.json`.

The first test-runner invocation placed `--jev-harness` after argparse REMAINDER
and correctly exited **2 before executing tests**, kept under `full-tests.*`.
Corrected command places that option before `tests`; no failed log was overwritten:

```text
python -B scripts/review_virtual_metrics_v2.py --jev-harness <read-only-reviewed-helper.py> tests
python -B scripts/audit_virtual_closed_loop_v2.py --evidence-dir task-evidence/2026-10-01-llm-v2-continuation --source-archive review-evidence/2026-10-01-v2-source-audit/complete-historical-source-map.json
```

Explicit helper path is necessary only in this pre-integration workspace; code
still defaults to same-repository helper after normal main-repository integration.
Vendor live calls0; the existing backend unit suite uses fake/loopback transport,
not new model requests. No commit/push/global approval/config changes.

Changed files: two new v2 drivers; added finite metrics tests, offline stage
archive/test/capture/seal helper and this doc. Nothing else in old artifacts.
MARINA threadId is coordinator launch-return data, unavailable inside worker;
no experiment session is substituted. Review evidence keeps thread_id:null.
