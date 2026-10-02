# B07 real LLM closeout — 2026-10-01

## Result

The authorized real baseline completed **100 frozen scenes × 3 complete
repeats**. The selected working service was the existing CC Switch
`api.deepseek.com` configuration, Anthropic-compatible protocol, requested and
service-reported response model `deepseek-v4-flash`. No stub/worker answer is
used as model output. No prompt, model or output cap changed during evaluation.
All 300 cases and failures are retained; independent replay reproduces the
entire LLM summary exactly. Source remained stable through the run.

The first authorized service, `api.gemai.cc` with `[free]gpt-5.6-sol`, failed its
only probe at the transport layer in 547 ms. It produced no response model,
usage or score. That failure remains in `run-first`, not dropped.

## Measured selector quality

**These are scheduling-selection microbenchmarks, NOT multistep task success.**
The synthetic corpus exposes authored operational facts; rule is corpus-aware.

| Selector | Joint labels | Owner-option match | Conservative match | Bad choice count | Invalid scene runs | Resource conflict runs |
|---|---:|---:|---:|---:|---:|---:|
| Real LLM capped configuration | 21/300 = **7.00%** | 44/600 = 7.33% | 0/150 = 0% | 556 | 274 | 0 |
| Jev raw vendor choices | 248/300 = 82.67% | 563/600 = 93.83% | 140/150 = 93.33% | 13 | 0* | 10 |
| Jev post adapter validation/overrides | 206/300 = 68.67% | 517/600 = 86.17% | 147/150 = 98% | 8 | 4 | 0 |
| Deterministic rule | 300/300 = 100% | 600/600 = 100% | 150/150 = 100% | 0 | 0 | 0 |

\* Jev raw is an **unconstrained semantic diagnostic before complete distribution
validation**, not admissible commands. Its four adapter rejects remain disclosed:
three invalid probability distributions, one choice-not-argmax. Post result
includes those rejects and 334 per-owner confidence overrides at threshold 0.6.
No execute admission is inferred from either raw or post scores.

**Dominant LLM failure:** 274/300 HTTP-200 responses ended `max_tokens` at the
explicit 512 output-token limit, with no complete final selection. Provider
default reasoning behavior consumed the cap; hidden thinking was discarded.
They were not retried, repaired, replaced or excluded. Thus this is evidence
about this **capped default configuration**, not a fair ceiling comparison of
uncapped or explicitly nonthinking DeepSeek against Jev. A future configuration
change needs a separately labelled experiment; visible v1 holdout must not be
presented as newly unseen or tuned implicitly.

- Per-repeat LLM joint: **8/100, 5/100, 8/100**; invalid: 91, 94, 89.
- Development: 14/240 joint = 5.83%; owner match 29/480 = 6.04%; 222 invalid.
- Holdout: 7/60 joint = 11.67%; owner match 15/120 = 12.50%; 52 invalid.
- Category joint: normal 11/120; ambiguity 0/60; stale 0/30;
  conflict/cancel 10/45; missing/failure 0/45.
- All LLM timeouts: **0**; truncations are failures, not timeouts.
- LLM bad count includes 548 missing-owner choices from complete-batch rejection
  plus 8 explicit bad-label choices. These are oracle selection counts, not
  commands executed or physical safety violations.

## Calls, latency, usage and estimated cost

| Metric | Real LLM | Jev sibling real run |
|---|---:|---:|
| Scored calls | 300 | 300 |
| All calls including probes | 302 across 2 providers; 301 working provider | 302 |
| Cloud p50 | 3208.18 ms | 438.00 ms |
| Cloud p95 | 3638.32 ms | 625.00 ms |
| Cloud max | 4461.19 ms | 1188.00 ms |
| Evaluation sum of request durations | 966.03 s | 141.03 s adapter / 139.61 s HTTP |
| Estimated evaluation cost | $0.0624288448 | $0.024104934 |
| Estimated working-provider/session cost including probes | $0.0626812648 | $0.024261636 |
| Actual bill | **null** | **null** |

LLM includes process startup/cleanup; Jev is adapter decide excluding rate waits
and cleanup. Protocols, prompts, deadlines and token constraints differ:
LLM **30 s total** versus Jev **1500 ms total**, one request at a time and no
retries for each. These numbers cannot be attributed purely to model speed or
extrapolated to robot real-time behavior. Sum of per-call durations is not a
fabricated end-to-end multistep task time.

Working LLM service returned usage on every one of 301 calls including its probe:
**141011 uncached input tokens, 178816 cache-read input tokens,
0 cache-creation input tokens, 151568 output tokens**. Output usage includes
provider thinking consumption, not just final JSON. Each per-call usage remains
in the evidence. Cache fields are distinct, not double-counted.

LLM price basis: exact CC Switch configured model rates × multiplier 1;
input $0.14/M, output $0.28/M, cache read $0.0028/M, creation $0/M. Entire-run
preflight reservation **$0.38147004**, below desired $5. Those rates are
configuration-derived estimates, not a verified current vendor tariff or
invoice. The initial failed provider has unknown billing/price; **all-provider
actual cost remains unknown**. Its `[free]` label is not proof of free service.
No purchase, login, account modification or global spending/config change.

## Comparison validation and limitations

Read sibling public report only:
`../b07-live/task-evidence/2026-10-01-live/evaluate-report.json`.
`comparison.json` pins its exact file hash, checks common frozen loader/scorer
and B00 source hashes, corpus/split hashes, and all **300 exact normalized
snapshot hashes including the snapshot in Jev's actual request**. Independent
common-scorer re-evaluation reproduces raw/post semantic metrics, and preserves
separate effects of Jev confidence overrides and admission checks.

The source LLM report still marks Jev pending because it was immutable when
written. The later `comparison.json` supersedes that pending comparison without
overwriting either source report or writing into the sibling workspace.

**Full B07 requirement remains incomplete:** LLM-only versus LLM planning + Jev
across shared three-step virtual tasks, integrated task success, hardware safety
and invoices were **not measured**. No new three-step runner is supplied in this
selector-focused delivery. No Jev execute-mode or B07 final acceptance is claimed.
Coordinator must review source stability, safety, pricing and test evidence
before integration; future authorized integrated-host replay is separate.

## Files and evidence

New implementation files only:

- `scripts/evaluate_llm_live.py` — explicit opt-in credential selection and real bounded HTTP.
- `scripts/capture_llm_evidence.py` — raw stdout/stderr/exit capture, exclusive output.
- `scripts/summarize_llm_evidence.py` — independent no-network evidence replay audit.
- `scripts/compare_llm_jev_evidence.py` — common-snapshot comparison of public reports.
- `tests/test_llm_live_evaluation.py` — 12 safety and replay tests using fabricated inputs.
- `docs/B07-LLM-LIVE.md` — protocol, budget and reproduction contract.
- `docs/B07-LLM-RESULTS.md` — this measured closeout.
- `WORKER-PROGRESS.md` — continuous implementation/progress record.

`task-evidence/2026-10-01-llm/` retains both probes, all request plans/results,
preflights, complete LLM report, independent audit, comparison, exact-secret
scans, and raw stdout/stderr/exit for tests and live commands. No headers,
account information, raw settings configuration, hidden reasoning or secrets.

Focused checks: **70 tests** total pass (12 new harness, 10 frozen offline,
8 decision contracts, 4 backend contracts, 27 Jev adapter, 9 snapshot contracts).
Initial failed tests and a mistyped filename matching zero tests are preserved,
with corrected final passing outputs. Frozen corpus exact rebuild check passes.
Actual runtime Windows x64 Python 3.12.10; Python 3.10 syntax grammar check only,
not actual 3.10/ARM64 execution. No full-project suite, service/hardware operation,
commit, push, core/corpus modifications or shared Git metadata changes.

MARINA threadId is managed by the coordinator's original worker tool return; it
is not exposed to this implementation process. Local harness session identifier
is `a0ed7aa4-c940-468d-b2c2-60da344f6aa3` (not a fabricated MARINA threadId).
