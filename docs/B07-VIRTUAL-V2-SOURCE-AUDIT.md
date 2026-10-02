# B07 v2 historical-source offline audit correction — 2026-10-01

This is **offline replay/path verification only**, not another real experiment.
No provider/key reads, live HTTP, task/candidate/transition/cost changes, historical
report rewrites, core/B00/corpus edits, commits, pushes or pycache cleanup.
Original result, official-price supplement, original audit outputs and all failed
seal/audit logs remain immutable.

## CLI and verification contract

`scripts/audit_virtual_closed_loop_v2.py` now supports:

- `--evidence-dir DIR`, default unchanged original continuation OUT.
- `--source-archive MAP.json` or a directory containing
  `historical-source-map.json`, optional. Entries map **exact original absolute
  report source identity strings** to archive bytes; no basename guessing.
- Repeatable `--source-root HISTORICAL_ROOT CURRENT_ROOT` explicitly relocates
  unarchived files. Sources not in the archive or a root mapping are checked at
  their actual original path. An explicitly selected missing or wrong archive
  never falls back to current source. Ambiguous root mappings reject.

Mapping JSON (UTF-8, strict duplicate-key parsing):

```json
{
  "schema_version": 1,
  "files": {
    "C:\\old\\repository\\scripts\\driver.py": {
      "path": "source-before/scripts/driver.py",
      "sha256": "<the original report SHA256>"
    }
  }
}
```

A relative archive path resolves from the mapping file's directory; an explicit
absolute archive path is also supported. Every mapping key must exist exactly
in report.source_before. Report source_before/source_after must be identical
nonempty maps, including full key sets and SHAs. Every historical identity must
be absolute without `..`, and every SHA exactly lowercase 64 hex. Entry SHA must
match report SHA and the actual archived bytes. **All 818 historical sources are
verified**, not just the corrected two drivers. No current hash is rewritten
into the old report. Root relocation retains original identity and derives only
the relative source path under an explicit old root; all actual byte hashes are
still checked. Default no-archive audit truthfully rejects revised historical
sources; it never ignores drift.

Existing state/call audit is retained: strict public request/output links, real
before/after state transitions, bound identity/stopped proof, terminal success,
120 arm/task records, per-task and per-arm counts, usage budgets and summaries.
UTF-8 is explicit for audit and replay JSON reads and new fixture writes.

## Archives and evidence

Before editing audit source, original bytes + SHA were retained at
`review-evidence/2026-10-01-v2-source-audit/preimage/scripts/audit_virtual_closed_loop_v2.py`
(and the original replay test preimage). `preimage-sha256.json` records these
plus 1699 immutable original evidence/v1 files, validated against the preceding
portability archive. Correct old driver/test bytes come from existing
`review-evidence/2026-10-01-v2-portability/source-before/`; they were not recopied
from corrected current source.

`historical-source-map.json` initially contained two drivers only, so the first
new audit **exit 1** correctly caught the additionally changed original test
file. That mapping and failure logs remain, not overwritten. The separate
`complete-historical-source-map.json` contains all three changed historical
sources with exact old identities and SHA. Success verifies **3 archives + 815
real original paths = 818** sources, same historical identity-map digest
`430d004de0cb3f1364b0a15c2afc2f0ee726dbefa9ab08455630d275bf5893e9`.

```text
python -B scripts/audit_virtual_closed_loop_v2.py --evidence-dir task-evidence/2026-10-01-llm-v2-continuation --source-archive review-evidence/2026-10-01-v2-source-audit/complete-historical-source-map.json
```

On an integrated machine, supply root mappings for unarchived paths or an
explicit full identity map. `scripts/prove_historical_audit_portability_v2.py`
copies evidence and all **818 validated historical byte files** into a temporary
layout under this worktree, creates a relative archive map and runs the audit as
a separate subprocess: **zero original absolute path source reads during audit**,
all 818 resolved from the temporary archive, report/evidence original SHA stays
unchanged. Temp copies are test-owned and removed on normal exit, not original
sibling/cache/evidence files.

## Tests / real exits

- Full 120-state/call/terminal/count/SHA historical audit: **exit 0**, success
  remains LLM-only51/hybrid38, LLM288reservations/287responses, Jev177.
- Fully relocated evidence/all-source independent subprocess: **exit 0**,
  818 archive-only verifications with same historical identity digest.
- Seven new strict-source tests: **exit 0**, actual missing-map and tampered-byte
  CLI each require **exit 1**, plus missing archived file/entry, wrong entry SHA,
  wrong historical identity, changed after-map, unchanged original file and
  explicit source-root relocation/tamper paths.
- Three original replay/tamper tests: **exit 0**.
- Separate `missing-map-cli` raw exit is **1**, deliberately rejected, not a
  failed acceptance test concealed as success.
- New captures/diff/verification live only under this new review-evidence.
  Existing portability failed `seal.*` and raw task-evidence were not overwritten.

One attempted small cleanup deleting the second identical strict source check
was denied by MARINA because approval output was truncated (`finish_reason=length`).
No retry/bypass/approval-setting change occurred. The approved final code performs
strict source verification before **and** after state replay. This is redundant
but fail-closed (and checks source bytes did not change while replaying).

Changed/new files: audit driver, replay helper's two UTF-8 reads, replay test's
UTF-8 writes; new source tests, offline archive/capture helper, fully-relocated
proof script and this documentation. No original measured outcome or price
values changed. Coordinator review required; MARINA threadId remains unavailable
inside worker SDK (coordinator must use original launch return, not experiment
session).
