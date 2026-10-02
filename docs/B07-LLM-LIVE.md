# B07 opt-in real LLM selector microbenchmark

## Scope and invariants

`python -B -m validation_drivers.evaluate_llm_live` is a **separate** live HTTP harness. It
imports only the frozen corpus loader, strict JSON helpers, scorer, deterministic
rule and summary math from `evaluate_jev_offline.py`; it does not call that
module's offline runner or turn its stub into a model baseline. No B00/action/
backend/core registration, corpus or holdout bytes are modified. No dispatcher,
Jev adapter, policy execution, plugin or hardware service is imported or called.

This is a **single-snapshot scheduling-selection microbenchmark**, not a
multistep task-success or physical safety measurement. `task_success=null`,
`actual_bill=null`, `execute_authorized=false`; there is no invoice. Action
side effects are absent by construction, not an integrated execute-gate proof.
The full B07 comparison of LLM-only scheduling versus LLM planning + Jev across
shared multistep tasks is **not completed** by this experiment.

## Protocol and explicit credentials

Read the selected authorized CC Switch provider row through SQLite `mode=ro`.
Only the selected configuration/secret is held in memory; no database mutation,
backup, CLI/global environment/config change, credential auto-discovery, login,
account query, historical request-log access or new purchase occurs. Console
metadata is limited to host, requested model, protocol and redacted status.

The first selected configuration uses `api.gemai.cc`, the Anthropic Messages
format and its configured model `[free]gpt-5.6-sol`. That is a **configuration
label**, not proof of a free tariff or model identity. The exact response `model`
is recorded separately and remains a service assertion, not independently
verified OpenAI/Anthropic/other vendor identity.

Primary API references retrieved using Tavily on 2026-10-01:

- [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages):
  POST `/v1/messages`, model, max_tokens, system/messages, nonstream response
  text blocks, model, stop_reason and usage.
- [DeepSeek Anthropic compatibility](https://api-docs.deepseek.com/guides/anthropic_api):
  configured `/anthropic` prefix, x-api-key, Messages format and model remapping;
  the provider configuration, not branding, determines the endpoint.
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion):
  compatible `/chat/completions`, messages, max_tokens, nonstream final content,
  response model, finish_reason and token usage. Explicit OpenAI-compatible mode
  is available but **not assumed** for an Anthropic-configured provider.

Raw HTTP was explicitly authorized. Python standard-library `http.client` uses
verified TLS and connects directly to the selected BASE_URL host only. No
redirect is followed (including same-host redirects), no proxy auto-discovery,
SDK retry or extra auth handshake is used. Secrets go only on that host's chosen
Authorization or x-api-key header; headers and HTTP error bodies are never saved.
Every result is exact-secret redacted in the child before it crosses the process
boundary and again before writing. The report scans evidence for exact in-memory
selected secret matches without ever printing the secret.

## Experiment and input/output contract

- Opt-in `--allow-live-http` required **before DB reads or HTTP**.
- 100 fixed corpus scenes in frozen order, exactly 3 complete repeats.
- One initial probe of the first frozen snapshot. It is not a scored scene run;
  its call, latency, tokens and possible billing remain in separate evidence.
- One active HTTP process; maximum two request starts per second; **no retries**.
- At most 310 total requests including probes across evidence directories;
  at most 6 probes and 3 configured provider hosts. A valid evaluation does not
  switch providers/model mid-repeat or discard failed cases.
- Every request has a **30-second total deadline**, including spawned-process
  startup and response decoding. A timed-out child is terminated and joined
  before another can start. This cannot abort remote inference or promise a
  refund. The setting is non-real-time, not B07's proposed 1500 ms target.
- Maximum 512 output tokens, 32 KiB actual system+user UTF-8 input and 256 KiB
  response. No truncation; oversize input rejects before HTTP.
- Each request receives only a fresh `DecisionSnapshot.parse(...).to_dict()`
  copy plus an output JSON schema mapping each owner to that owner's eligible
  IDs. Scene wrapper/category/subtype/split/expected labels/rationale are not
  sent. Raw observations, their guide text, current goal/bound parameters,
  owner state and all candidates remain visible in the snapshot. The corpus's
  authored `offline_facts` are transparent synthetic operational data.
- Parser-normalized snapshot hash may differ from the raw fixture snapshot
  hash because B00 materializes optional defaults. LLM and rule both use the
  **same normalized bytes/hash**, checked for every scene. Frozen corpus hash
  remains `fba8d87b04399fd644cddd96cad1ac08181baf695de5d2a03fe17db811ad9936`.
- Prompt version `b07-live-llm-selector-v1` is fixed before the first call. JSON
  constraints are prompted and validated locally, **not** claimed to be
  provider-enforced constrained decoding. No labels or few-shot answers.
- Strict JSON requires exactly the owner set and eligible matching option IDs.
  Duplicate keys/non-finite JSON/unknown owners/options/invented params fail the
  complete selection. No first-candidate/conservative fallback repairs model
  output. Truncated, refusal or nonfinal stop reasons remain failed calls.
- Keep raw **final selection text** (secret redacted) plus parsed result;
  discard provider thinking/reasoning/signatures and all headers. Scoring uses
  the actual parsed selection, never worker-generated model answers.

## Budget and reporting

The desired cap is $5. If exact configured model pricing plus provider
cost_multiplier exists, preflight reserves the **entire planned run** against
$5 using a deliberately loose UTF-8-byte-plus-4096-overhead planning bound,
max input/cache rate, and 512 output tokens. This is **not actual tokenizer
counting or verified billing enforcement**; reservation is not refunded for
failures/timeouts. Actual usage-based cost is computed only when usage and
response model support that exact pricing. Cache read/write tokens are separate.

If reliable pricing is unavailable, only the specifically authorized existing
configuration **labelled `[free]`** is allowed. Its unknown price and bill are
explicitly reported, with `financial_cap_verified=false`; no claim of verified
free service or measured zero cost. Paid/unknown non-free configs fail preflight.
No global account budget is changed.

Reports contain source/prompt/request/input/corpus/split SHA-256, requested and
response models, every failure/timeout, local rule timings, cloud p50/p95/max,
per-repeat/category/development/holdout owner-option/joint/conservative/bad and
conflict results, and actual response usage (missing usage is not zero).
Success latencies do not replace failed latencies in summaries. Source and
frozen corpus are checked again after the run; changes invalidate acceptance.

Sibling real Jev evaluation writes elsewhere. This worker marks comparison
`pending_sibling_real_report`; coordinator must combine public reports only
after source/corpus/snapshot/model/timeout/scoring comparability review.

## Run / tests

Current commands run from the independent AstrBotVLA-tests checkout, not EX:

```powershell
# No network without the explicit flag (expected rejection):
python -B -m validation_drivers.evaluate_llm_live --ex-checkout ../ex --aeb-checkout ../aeb --evidence artifacts/new-run

# Strict offline tests include both migrated live-driver and corpus tests:
python -B run_validation.py offline --ex-checkout ../ex --aeb-checkout ../aeb
python -B fixtures/decision/jev/build_offline_corpus.py --check
```

The historical authorized live command is not an authorization to rerun. A new
live run needs separate approval, explicit `--allow-live-http`, an explicit
local `--cc-switch-db` (or driver `--db`) and a new artifacts path; no desktop
credential default remains. Capture wrappers use `python -B -m
validation_drivers.capture_llm_evidence`, a relative unique stem, explicit
checkout/artifacts options **before** `--`, and a child module command after it.
No live calls or credential reads are part of migration/offline CI.

Do not rerun if this would exceed the existing evidence request count/budget.
Evidence files use exclusive creation, never overwrite prior runs. Safety tests
use fabricated credentials, a loopback redirect and a stalled child, not live
model quality scores. The initial test failure is retained: SQLite connection
context did not close on Windows, and a test incorrectly compared normalized
snapshot hash to raw fixture hash. Explicit connection closing and same-parser
hash comparison fixed those without modifying the corpus or frozen parser.
The initial provider probe failed with a fixed redacted transport diagnostic and
produced no quality score. The second authorized configuration is
`api.deepseek.com`, requested/service-reported `deepseek-v4-flash`, using its
configured Anthropic endpoint prefix. Its exact CC Switch rates are input
$0.14/M, output $0.28/M, cache read $0.0028/M, cache creation $0/M and multiplier
1; the 301-call preflight reservation is $0.38147004. Those are configured
estimation inputs, **not a verified current vendor tariff or invoice**. The
first failed unknown-price probe is still potentially billable and makes an
all-provider actual-cost total unknown.

The complete live run is not tuned after its probe: the provider's default
reasoning behavior may consume the 512-token output budget before any final
JSON. Such `max_tokens` responses are recorded as invalid selections, not
retried with thinking disabled, a larger output cap or a new prompt. This
experiment characterizes the **specific capped configuration**, not a
ceiling on the model's uncapped/nonthinking quality.

`python scripts/summarize_llm_evidence.py <run-directory> --output <new-evidence-file>`
can independently re-score retained request results without reading credentials
or sending HTTP. It verifies frozen order and input hashes, preserves partial
runs as partial, and uses null (not invented zero measurements) for rule latency
in quality-only replay. Safety tests also check this independent replay.

Python 3.12.10 Windows was tested. Actual Python 3.10/ARM64 execution and the full
project suite are not implied. Final 12/12 live-harness safety tests, 10/10
offline-evaluator tests, 8/8 decision contracts, 4/4 backend contracts, 27/27
Jev adapter tests and 9/9 snapshot contracts pass. A mistyped initial snapshot
test filename matched zero tests and exited 5; the correct suite was subsequently
run, with both outputs retained. All four new Python files pass Python 3.10
syntax grammar checking, which is not an actual Python 3.10 runtime test.
