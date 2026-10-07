# PR remediation — bounded harness migration (2026-10-07)

## Scope and source identity

Six independent validation files moved from EX to this repository; no runtime/dashboard business code moved or changed. EX keeps its functional unit tests and required examples, including `wiring_fixture.py`, `test_decision_service.py`, `test_goal_manager.py`, `test_laya_backend.py`, `test_provider_connection.py` and management HTTP fixtures. Both product Git histories remain. No forwarding stubs remain in EX.

| Original EX path | New path |
| --- | --- |
| `scripts/diagnose_dispatcher_stress.py` | `validation_drivers/diagnose_dispatcher_stress.py` |
| `scripts/verify_decision_management.py` | `validation_drivers/verify_decision_management.py` |
| `scripts/verify_decision_ui.mjs` | `validation_drivers/verify_decision_ui.mjs` |
| `tests/decision_ui_fixture.py` | `validation_support/decision_ui_fixture.py` |
| `tests/decision_ui_real_fixture.py` | `validation_support/decision_ui_real_fixture.py` |
| `tests/test_decision_aeb_replanning.py` | `validation_tests/integration/test_decision_aeb_replanning.py` |

EX migration source: clean commit `b2e8dddb36a6e3bab52607a09db66669d8afd5df`, branch `integration/laya-b08-b09-ros`; remediation base `531b7c9a4feef93599a51f5565fe65e7a98bcc38`. A.E.B reviewed companion: commit `2e77eba61ad37d178f872805cbb7219104bb38fd`, branch `codex/c05-chat-routing-20261006`, **dirty reviewed overlay**, not formal A.E.B main. [sources.json](../results/remediation-20261007/sources.json) records exact reviewed and migration-time dirty file SHA-256. No product code archive is published.

`repositories.lock.json` is **unchanged**: historical development baseline, `release_final: false`. C09 source acceptance below does not certify the older split harness, Host/campaign investigation or the whole kit. The runner's explicit `--remediation-sources` option is restricted to `replanning --allow-dirty`; it records current identities separately rather than changing or pretending to match the historical lock. For this mode tracked sources plus explicitly source-root/suffix-allowlisted untracked functional code are hashed using NUL-delimited Git paths (including Unicode filenames and the reviewed A.E.B new files). Runtime data/cache/credentials and Python bytecode are neither read nor certified. No release-clean claim.

## Portable commands

Run from this validation repository with disjoint explicit EX/A.E.B paths. Python and Chrome must already be installed. Host-dependent checks need an installed real Host SDK/dependencies or explicit `--host-checkout`; no user-directory discovery, package installation, model, hardware or ROS calls are performed here. If using an explicit dependency `PYTHONPATH`, record it with the command; no implicit Host/user Python overlay is injected.

```sh
python -B -m validation_drivers.diagnose_dispatcher_stress --ex-checkout ../ex --aeb-checkout ../aeb --check-import
# Full diagnostic retains the original 1000 sequences/seed/assertions/Future limits:
python -B -m validation_drivers.diagnose_dispatcher_stress --ex-checkout ../ex --aeb-checkout ../aeb
python -B -m validation_drivers.verify_decision_management --ex-checkout ../ex --aeb-checkout ../aeb --output artifacts/remediation-management-unique
node validation_drivers/verify_decision_ui.mjs --mode fixture --ex-checkout ../ex --aeb-checkout ../aeb --python /absolute/python --chrome /absolute/chromium --output artifacts/remediation-fixture-unique
node validation_drivers/verify_decision_ui.mjs --mode actual --ex-checkout ../ex --aeb-checkout ../aeb --host-checkout ../host --python /absolute/host-python --chrome /absolute/chromium --output artifacts/remediation-actual-unique
python -B run_validation.py replanning --ex-checkout ../ex --aeb-checkout ../aeb --host-checkout ../host --allow-dirty --remediation-sources
```

Actual mode **requires** explicit A.E.B CLI input and both real handler/TaskStore ROUTER/DEALER projection checks; it cannot silently become six-of-eight coverage. Management and browser outputs require a new directory outside functional checkouts (in this repo, only ignored `artifacts/`). Product/dashboard hashes resolve from EX; harness hashes are a separate group. Cleanup guards and strict browser assertions remain. The six C09 case IDs have an explicit [old→new map](../results/remediation-20261007/case-id-map.json); class/helper bodies and assertions are AST-identical. `replanning` collects exactly these six as a standalone mode. Historical `ex-unit`/`offline`/`wiring` selections are unchanged; `ex-offline` remains their original union and does not add replanning or warm up Host. The replanning runner overrides inherited `ASTRBOT_ROOT` and child cwd with its isolated `output/host-runtime` before any Host import. No imported fixture duplicates.

## Historical accepted results (sanitized derivatives)

| Evidence | Result and limitation |
| --- | --- |
| [Windows](../results/remediation-20261007/windows.json) | 757 collected/run; 756 passed, 1 Linux TCP TIME_WAIT `/proc` regression skipped |
| [Native Linux](../results/remediation-20261007/linux.json) | 757 collected; 755 run/pass, 2 Host SDK cases skipped; those two passed on Windows |
| [Independent A.E.B](../results/remediation-20261007/aeb.json) | 164 passed, zero failures/errors/skips |
| [Actual browser](../results/remediation-20261007/browser.json) | 8 checks; actual EX HTTP + A.E.B projection, synthetic loopback suppliers |
| [Contract fixture](../results/remediation-20261007/fixture.json) | 17 checks; frozen decision HTTP contract, not production activation proof |
| [Management loop](../results/remediation-20261007/management.json) | 7 checks; real RuntimeController/software Actor, synthetic owned supplier |
| [Native ROS lifecycle](../results/remediation-20261007/ros2.json) | Historical `ros2_echo` only, exit 0, runtime returned idle, HTTP thread stopped; not rerun here |
| [Jev no-action probe](../results/remediation-20261007/jev-no-action.json) | Previous authorized single wait-only request; execution disabled, no Goal/Action; no repeat paid call |

[acceptance.json](../results/remediation-20261007/acceptance.json) preserves source review limits. Platform skips are explicit and **not** a strict runner pass. True physical motion was not exercised; no actual Laya GPU/model semantic quality, real robot stopping, production deployment or universal reply-delivery guarantee is claimed. The supported buffered Host logical dedup path does not cover arbitrary proactive/direct/third-party sends. Port preflight is not reservation. The earlier cleanup fault injection hit a 40-second bound and required termination; normal final cleanup passed, not the fault attempt.

[prior-failures.json](../results/remediation-20261007/prior-failures.json) retains failed `final-native-20261007`, `final-native-reviewed-20261007`, owned-port and Linux/Windows snapshot-stop red regressions with source evidence hashes. Passing reruns do not erase or relabel them. The previously removed 109 EX docs/evidence artifacts are not restored.

## Derivation and migration checks

Public JSON uses allowlisted counts, case IDs/statuses, scoped checks/cleanup facts, limits and relative source SHA references. It excludes credentials, headers, request/task bodies, database contents, absolute workstation paths, raw logs and source archives. Each derivative records its original evidence filename/SHA. These are **sanitized derivatives**, not byte-identical moves or newly executed historical acceptance. Originals remain immutable externally.

The external migration audit `test-repository-migration-20261007/` contains pre/post hashes/status, byte-identical initial-copy verification, scoped deletion list, retained-unit hashes, command captures and original→published derivative hashes; it is not committed. Coordinator review/push owns final publication. Bounded local migration checks and any failed attempts are recorded separately from historical acceptance in [migration-checks.json](../results/remediation-20261007/migration-checks.json).
