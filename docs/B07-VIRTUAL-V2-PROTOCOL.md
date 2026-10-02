# B07 virtual closed-loop v2 — frozen protocol (2026-10-01)

This is a **new, public synthetic, independent stdlib virtual state-machine
experiment**, not an unseen holdout, hardware test, Actor test, production run,
or grounds for changing Jev execution admission. It does not reuse/tune v1's
100-scene corpus, prompts or confidence threshold. All v1 source/evidence stays
byte-immutable and is SHA checked before/after. Independent task spec is frozen
before tests and live. No core/B00/B03/B08 or sibling changes.

## Machine and paired arms

20 authored tasks: four each normal, ambiguity, observation failure, conflict,
failure recovery. Explicit bound item identity, raw/ready/processed/completed
phase, observation condition, existing command and stop evidence are actual
virtual state. They contain no expected selection labels. Three complete repeats;
paired arm order alternates by repeat. Identical tasks, initial states, allowed
bound goals, candidates, shared local eligibility/transition gate and maximum
three decision ticks. Each arm/task independently makes **one real LLM planning
request** yielding exactly three bound goal keys (`prepare`, `transform`,
`finish` in model-chosen order); no oracle fallback or plan repair. Plan only
selects supplied goals: it cannot invent target or parameters. The resulting
current goal goes into each decision snapshot. LLM-only uses real LLM every tick;
hybrid uses real Jev shadow Choice every tick. Failures can end before three
steps; all 120 arm/task attempts remain in results, not only successes.

Only English goal, JSON, public operational guide and actual virtual state enter
requests. Metadata category/evaluator predicates/expected choices do not.
Transient stale/ambiguous evidence is repaired by a wait observation tick; this
**observation-only preparation** updates raw to ready without starting work.
Missing evidence stays missing. Healthy existing preparation is completed by
keep without restarting. Foreign workspace-holder/failed preparation require
cancel; only proven stop releases resources and establishes ready. Unprovable
cancel remains terminal unsuccessful. request_replan ends this attempt with
failure (no extra planning). Ordinary prepare/transform/finish starts perform
the corresponding actual phase update, only when current prerequisite phase,
fresh observation and free workspace pass the common gate. Invalid responses,
timeouts, unknown/ineligible options never update phase. Task success requires
completed bound item, no active command, proven stop and no failure. This is not
a multiplication of independent label matches; wait/replan are not automatic
success. The crafted machine semantics intentionally let recoverable conditions
fit the same three-tick budget; they are not robot behaviors.

## Fixed provider/protocol, requests and financial reservations

Official DeepSeek docs retrieved through Tavily before implementation:
- https://api-docs.deepseek.com/guides/thinking_mode
- https://api-docs.deepseek.com/api/create-chat-completion
- https://api-docs.deepseek.com/guides/anthropic_api

Current docs explicitly support `thinking: {"type":"disabled"}` in Chat
Completions; default is thinking enabled. v2 fixes that field from request one,
nonstream Chat Completions at `https://api.deepseek.com/v1/chat/completions`,
512-token output cap, JSON-object format, immutable planner/selector prompts.
Selected existing CC Switch provider `4a5dc05f-ad51-437f-b137-60469993368c`
is read with reviewed v1 `read_provider` (SQLite read-only). The configured
Anthropic base path is replaced **only in the new in-memory v2 Provider** by the
official same-host Chat Completions endpoint; no config/database writes.
Requested model stays `deepseek-v4-flash`; actual response model must match,
else fail closed. No midrun cap/prompt/protocol tuning and no extra probe/retry.

Jev pins unchanged `jev-1.13.0`, shadow, `execution_allowed=False`, reviewed
adapter deadline 1500 ms, confidence override .6 unchanged, no retries. Reviewed
sibling `Ledger`/`RecordingTransport`/`one_decision` are loaded read-only. Only
first nonempty token from the authorized Desktop file is read, in memory; no
rotation/account change/environment persistence. Jev data never enters real
DecisionService or dispatcher. Per-call validated data alone updates local
independent machine.

Maximum LLM calls: 120 plans + 180 LLM selections = **300**. Maximum Jev calls:
60 hybrid tasks × 3 = **180**. Single active request, global maximum 2 starts/s;
LLM 30-second total subprocess deadline. Parent quarantines any Jev canceled
worker before another call; timeout wait is not accepted inference latency.
Reservation precedes network and failures are never refunded. No fake call is
created if Jev rejects before HTTP. Durable reservation may conservatively mark
a crashed not-proven-sent attempt; normal completed reports count actual recorded
transport calls and disclose this distinction.

LLM configured estimate basis input $0.14/M, output $0.28/M, cache read $0.0028/M,
creation $0/M × existing multiplier. Conservative fixed input bound 12000 UTF8
bytes + 4096 overhead tokens, 512 output, max input/cache rate per request:
$0.0023968 × 300 = **$0.71904**, ≤ $1. Exact selected pricing is checked at
preflight; unknown price or full-run reserve >$1 stops before network.
Jev official https://docs.typesafe.ai/models total tokens $0.042/M:
65536 × $0.042/M × 180 = **$0.49545216**, ≤ $1. Reservations are conservative
estimates, not server-side billing enforcement or verified actual invoices.

## Evidence and reproduction

Migrated driver: `validation_drivers/evaluate_virtual_closed_loop_v2.py`; task
machine: `validation_drivers/b07_virtual_tasks_v2.py`; frozen spec:
`fixtures/decision/virtual-v2.tasks.json`; offline tests:
`validation_tests/evaluation/test_virtual_closed_loop_v2.py`; exclusive raw
wrapper: `validation_drivers/capture_virtual_evidence_v2.py`.

From the independent AstrBotVLA-tests checkout:

```text
python -B run_validation.py offline --ex-checkout ../ex --aeb-checkout ../aeb
python -B -m validation_drivers.evaluate_virtual_closed_loop_v2 --ex-checkout ../ex --aeb-checkout ../aeb --help
```

The spec is already frozen: do not recreate/retune it. Historical live/audit/scan
runs below retain their original numbers and local archive references. A new
live run requires separate authorization, explicit credential-file options
(`--cc-switch-db`, `--jev-key-file`), `--allow-live-http` and a unique
`--artifacts-dir`. Wrappers take checkout/artifacts options before `--` and a
`python -B -m validation_drivers.<driver>` child command after it. Those live or
credential-scanning commands are not part of this split's offline acceptance.

Live is explicit opt-in, whole-run exclusive directory; existing v2 evidence
refuses rerun/overwrite. Freeze is exclusive creation, not changing spec after
live. Every actual call keeps public request, response final text or Jev public
projection, reported model, usage, duration, estimated cost/null actual invoice,
session/repeat/task/arm, errors, request SHA and reservation. HTTP error bodies
and hidden reasoning are suppressed by the reviewed safety projection; transport
failure status/reason are retained, not hidden. Unknown Jev response IDs are
redacted to avoid hostile credentials echo; this is a documented public
projection, not byte-identical vendor response. Raw stdout/stderr/exit stay
under `task-evidence/2026-10-01-llm-v2-checks`; live artifacts under
`task-evidence/2026-10-01-llm-v2`. Every task retains its model plan, actual
before/after states, choices, failure reason, success, steps, modelcalls, elapsed,
token-cost estimate and null invoice. Arm summaries retain p50/p95/max request
and task end-to-end times, sum end-to-end time, usage, calls, failures and all
repeats. Separate session wall-clock includes serialization/rate wait/cleanup;
request latency scopes differ and are not real-time claims.

Independent no-network replay verifies actual state transitions, bindings,
terminal success and per-task real-call counts. SHA manifests include frozen v1
evidence before/after and readonly core/adapter/corpus. Exact in-memory scan of
both selected credentials runs at completion and again over final evidence.
No keys, complete config, headers or hidden reasoning are output. Actual invoice
is always null absent a verified invoice. Coordinator review remains required.
MARINA threadId is from the coordinator's worker-launch return, not exposed by
this worker SDK; a local experiment session is not substituted for threadId.
