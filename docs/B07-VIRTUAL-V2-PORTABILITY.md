# B07 v2 portability review fix — 2026-10-01

This is an **offline delivery-path correction only**. It does not rerun v1/v2,
change any historical result/cost, retune selection, read real credentials or
send live model requests. Original DeepSeek alias/interruption disclosures and
current official-price supplement are preserved. No core/B00/D-library/global
configuration/permission changes, commits, pushes, or sibling-cache cleanup.

## Exact code change

- `scripts/evaluate_virtual_closed_loop_v2.py`: replace worktree-name-dependent
  `ROOT.parent / "b07-live/scripts/evaluate_jev_live.py"` with
  `DEFAULT_JEV_HARNESS = ROOT / "scripts/evaluate_jev_live.py"`.
  `load_jev_harness(path=None)` accepts a read-only explicit path, resolving it
  before compile/exec (no helper pycache writes). Missing chosen helper fails
  without searching/falling back to another worktree.
- `source_manifest(jev_harness=None)` hashes the **same selected path** used for
  loading. `Session(..., jev_harness=None)` forwards that path. Original driver's
  `--jev-harness PATH` threads it through preflight, Session and after-run SHA.
- `scripts/continue_virtual_closed_loop_v2.py`: explicit argparse
  `--jev-harness PATH` forwards the selected helper through `hashes()` and
  inherited Session, before/after. Live still requires the original opt-in,
  existing evidence still refuses overwrite. No live run occurred for this fix.
- `tests/test_virtual_closed_loop_v2.py`: extend an existing test (no test count
  change) with same-repository default, explicit path, selected-helper SHA,
  no pycache, and missing-path failure checks.
- New `scripts/review_virtual_portability_v2.py`: stdlib, offline archive,
  complete-64 runner, isolated-layout proof and immutable-evidence/diff seal.
  It never invokes a live driver, reads providers/keys or changes credentials.

The pre-integration LLM workspace does **not** contain the other worker's
`scripts/evaluate_jev_live.py`; copying/merging that existing reviewed helper
belongs to coordinator integration. Therefore this workspace's offline test
runner is given an **explicit original-helper path**, with no code hardcoded to
its worktree name. A merged main repository with the helper in `scripts/` needs
no override. The temporary-layout test supplies it at that ordinary repository
location and calls the default loader unpatched. No helper is copied into the
actual worktree or modified as part of this correction.

## Archive / unchanged evidence

Before changing old v2 files, 15 script/test/doc/spec files were copied as
**original bytes**, with SHA validation, into
`review-evidence/2026-10-01-v2-portability/source-before/`. Manifest
`archive-sha256.json` also hashes **1699 original evidence + v1 source/doc files**.
The original result report, request/result records, raw exits, all original
secret scans, official-price supplement and closeout SHA files are untouched.

Historical source SHA belongs to the archived source, not the current portability
revision. Do not rewrite historical source manifests or call the legacy
current-source audit to falsely certify the corrected delivery as the old source.
The new review seal independently checks every original artifact SHA and the
archived original source bytes, then records the concrete portability diff and
new source SHAs separately. The archived original report still truthfully records
its run-time before/after stability.

The old driver's Jev output-token cost overestimate remains exactly as measured;
use the existing `official-price-supplement.json` for input-only official tariff.
No fee formula, prompt, cap, guide, task, transition, model, deadline, budget,
confidence threshold or execution-gate logic was changed.

## Real checks / exits

- **25 v2 + 12 LLM + 27 Jev = 64 tests**, no skips: `Ran 64 tests in 6.095s`,
  `OK`, **exit 0**. Fake transports/test credentials only; existing loopback
  transport unit tests are not vendor live calls.
- Isolated temporary repository layout: default `scripts/evaluate_jev_live.py`
  loaded successfully, no sibling directory, explicit old-helper path works,
  missing explicit file raises `FileNotFoundError`, helper pycache absent,
  zero real credential reads/live HTTP: **exit 0**.
- Raw outputs under this **new review-evidence**, never old `task-evidence`:
  `tests.stdout.txt`, `tests.stderr.txt`, `tests.exit.json`,
  `portable.stdout.txt`, `portable.stderr.txt`, `portable.exit.json`, plus the
  stricter final `portable-final.*` run.
- First review seal exited 1 after successfully checking unchanged evidence:
  Windows default GBK could not decode the UTF-8 new documentation while making
  its diff. The review helper now reads UTF-8 explicitly; failed raw exit is
  retained under `seal.*`, corrected completion under `seal-final.*`.
- `portability.diff` and `verification.json` record source change and original
  evidence/v1 immutable SHA verification. Original pycache left alone as requested.

Commands used (explicit path supplied by coordinator/local command, not inferred):

```text
python -B scripts/review_virtual_portability_v2.py archive
python -B scripts/review_virtual_portability_v2.py tests --jev-harness <read-only-helper.py> --capture
python -B scripts/review_virtual_portability_v2.py portable --jev-harness <read-only-helper.py> --capture
python -B scripts/review_virtual_portability_v2.py seal --capture
```

Normal integrated-repository tests remain:
`python -B -m unittest discover -s tests -p 'test_virtual*.py' -v`, and the two
existing LLM/Jev suites. The explicit helper is trusted reviewed Python code,
not arbitrary input from a remote response.

**threadId:** not exposed inside worker SDK. The coordinator must use its original
MARINA worker-launch return; experiment session `b07-v2-2aa261d79118` is not
substituted. `verification.json` has `thread_id:null` with that reason.
