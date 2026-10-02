# v2 transparent continuation addendum — 2026-10-01

The first live attempt requested `deepseek-v4-flash` with the frozen nonthinking
Chat Completions configuration but received `deepseek-flash`. The new original
v2 exact-model-name check rejected otherwise valid planner JSON. After 34
presend reservations / 33 observed planner responses / **zero Jev calls**, the
worker stopped this session; the last response was not observed. All original
request/response/task files remain byte-immutable. One in-flight request may
have been sent and billed; it is conservatively reserved and failed, not retried
and not asserted to be a proven HTTP call. Original raw exit file was empty due
to interruption, not presented as success. Original run is not source-stable
across the following interpretation change.

Official original URLs retrieved through Tavily confirm API alias routing:
- https://api-docs.deepseek.com/updates
- https://api-docs.deepseek.com/quick_start/pricing

They say previous V4 Flash is retired and legacy `deepseek-v4-flash` is routed to
V4.1 Flash (`deepseek-flash`). This experiment must therefore report actual
service-returned **deepseek-flash**, not claim pinned original V4 identity. The
requested provider/account/model remain exactly unchanged. Existing CC Switch
.14/.28/.0028 pricing is an **old configured-price estimate**, NOT verified
current V4.1 tariff; invoices remain null. Missing interrupted usage stays
unknown and is not zero billing.

New `scripts/continue_virtual_closed_loop_v2.py` handles this as a transparent
local interpretation, with immutable new artifacts under
`task-evidence/2026-10-01-llm-v2-continuation`. It accepts only the documented
response aliases and requires byte-identical recovered planner input SHA.
Existing real planner replies are revalidated without making another request;
one missing response remains planner failure/no oracle/no retry. Previously
presend reservations count toward the same **300 LLM / $1** maximum, including
the uncertain one; Jev remains **180 / $1**, first key, shadow, .6 unchanged.
No prompt, cap, guide, task, nonthinking parameter, requested model or selector
gate is changed. There is no selection-based tuning. There is a disclosed
source/interpretation correction after initial live, so this is **not an
uninterrupted single-source experiment** and not a newly unseen holdout.

Each arm still receives independent actual planning, shared bound tasks/initial
state and max three selector ticks. Recovered planner roundtrip contributes to
active task timing; interruption/research idle is excluded from per-task active
time and reported separately in whole wall elapsed. The unresolved interrupted
planner latency is null (excluded from percentiles), its task is unsuccessful.
No latency is fabricated. Complete 20×3 arm attempts are reported, with failures.

Reproduction of new runs should first integrate the official alias interpretation
into a newly versioned driver after coordinator review; neither original nor
continuation artifacts may be overwritten or automatically rerun. This
continuation checks original v2 plus v1 evidence hashes before/after, freezes its
own source, and performs independent replay/exact-secret scans. The change is
separate from v1 immutable evidence and never authorizes Jev execution.

Command (one-use, explicit opt-in):
`python scripts/capture_virtual_evidence_v2.py continuation -- python scripts/continue_virtual_closed_loop_v2.py --allow-live-http`
