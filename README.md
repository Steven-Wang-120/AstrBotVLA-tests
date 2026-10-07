# AstrBotVLA-tests

Published **development test kit**, not a certified release, for [AstrBotEX](https://github.com/Steven-Wang-120/AstrbotEX) and [A.E.B](https://github.com/Steven-Wang-120/A.E.B.). It provides independent system, ROS and evaluation validation. Functional unit tests and their local helpers remain in their respective repositories; runtime code and frozen contracts are not owned here.

## Remediation migration (2026-10-07)

[PR-REMEDIATION-20261007](docs/PR-REMEDIATION-20261007.md) describes the bounded six-file EX harness migration and [sanitized historical acceptance](results/remediation-20261007/acceptance.json). These are separately identified remediation sources, **not** certification of this whole kit. The historical development lock and `release_final: false` remain unchanged. Product units and shared fixtures stay in EX; only six cross-repository replanning cases and independent drivers/helpers move here. See the report for portable commands, source hashes, platform skips and retained failures.

## Evidence status and known issues

- The pinned functional B04–B07 production code previously passed the original Windows (443), ARM (443) and ROS (5) checks. That historical evidence **does not certify this new harness**.
- The split `offline` (76) and `wiring` (9) checks passed individually. Combined collection failed with a lifecycle race and a missing `rejected` phase. The phase-contamination fix now reloads contracts in a fresh process, but the combined suite has **not been rerun**.
- Host/campaign stricter thread/shutdown checks and P07 remain under investigation; some cleanup fixes are unverified. Publication is not a claim of complete acceptance or a green release.

## Checkouts and dependencies

Use three **disjoint** checkouts; never copy validation into either runtime tree:

```text
workspace/
  validation/  # Steven-Wang-120/AstrBotVLA-tests
  ex/          # AstrBotEX at repositories.lock.json repositories.ex.commit
  aeb/         # A.E.B at repositories.lock.json repositories.aeb.commit
```

For portable clones, `.gitattributes` preserves raw frozen fixture bytes (`fixtures/** -text`), preventing Windows `core.autocrlf` from changing their hashes.

Python 3.10 or 3.12 and Git are required. Install only EX's runtime dependencies:

```sh
python -m pip install -r ../ex/requirements.txt
python -B run_validation.py --help
```

Commands below run from `validation/`. Both checkout arguments accept absolute or relative paths; named environment alternatives are `ASTRBOTEX_TEST_CHECKOUT` and `ASTRBOTEX_TEST_AEB_CHECKOUT`. No desktop paths, host discovery or credentials are needed for offline modes.

`repositories.lock.json` pins **exact full functional Git commits**, not branch names. Release acceptance requires a final lock and clean EX/AEB checkouts at those commits. This development publication retains `release_final: false`: append `--allow-dirty` to every runner invocation **even on pinned clean checkouts**. It always records a **noncertified development identity**, including exact tree and per-file hashes; it does not waive strict test, skip, thread-leak or source-stability checks. Do not change the lock merely to bypass a failure.

## Offline and unit checks

```sh
python -B run_validation.py ex-unit --ex-checkout ../ex --aeb-checkout ../aeb --allow-dirty
python -B run_validation.py offline --ex-checkout ../ex --aeb-checkout ../aeb --allow-dirty
python -B run_validation.py wiring --ex-checkout ../ex --aeb-checkout ../aeb --allow-dirty
# Combined collection: retained EX units + evaluation + harness integrity + wiring.
python -B run_validation.py ex-offline --ex-checkout ../ex --aeb-checkout ../aeb --allow-dirty
```

| Mode | Scope |
| --- | --- |
| `ex-unit` | All retained EX `tests/test_*.py` classes defined in their own modules |
| `offline` | Migrated evaluation tests (70) and runner-integrity tests (6, including real Actor/worker leak detection); live-driver tests use fake responses/loopback only |
| `wiring` | Original 9 decision-wiring cases, using EX's retained fixture |
| `replanning` | Six migrated C09 EX/A.E.B cases (four core + two requiring real Host public SDK); strict runner fails missing-SDK skips |
| `ex-offline` | Original union of `ex-unit`, `offline` and `wiring`; standalone `replanning` is not included |
| `host-unit` | Retained A.E.B units in an explicit local AstrBot Host image |
| `host-integration` | Migrated real EX/A.E.B composition (9), ZMQ rejection boundaries and joint-helper cases |
| `campaign` | Original finite P01–P09 campaign: 180 rounds / 340 case executions, fake provider/EX; **not real B04 combination** |
| `ros` | Original five native DDS/custom-interface cases on an isolated sourced ROS host |

Counts describe the recovered baseline, not a substitute for recorded case IDs. Windows/Linux Python 3.10/3.12 **development smoke CI** runs the first three modes only; success is not release certification. Generic runners do not run native ROS, Docker Host integration or live models.

## Existing offline Host image

An already prepared Host 4.26.7 image is required; no implicit pull, build or update occurs:

```sh
docker image inspect local/hzf-aeb-baseline:20260927
python -B run_validation.py host-unit --ex-checkout ../ex --aeb-checkout ../aeb --allow-dirty --host-image local/hzf-aeb-baseline:20260927
python -B run_validation.py host-integration --ex-checkout ../ex --aeb-checkout ../aeb --allow-dirty --host-image local/hzf-aeb-baseline:20260927
python -B run_validation.py campaign --ex-checkout ../ex --aeb-checkout ../aeb --allow-dirty --host-image local/hzf-aeb-baseline:20260927
```

The runner inspects and records the local image digest, uses `--pull never --network none --read-only`, read-only source mounts, temporary Host data and a separate writable evidence mount. Missing Docker/image is a failure, not a passed/skipped acceptance. This image supplies Host dependencies; do not install Host packages into EX.

Host 4.26.7 imports start two process-lifetime `astrbot.core.sp` (SharedPreferences)
services, whose owner has no public shutdown method: `_sync_loop.run_forever`
and `_scheduler._main_loop`. The Host runner warms up **only actual public Host
API**, verifies these exact bound-object owners and scheduler job, and records
their names/ownership in `framework_baseline`. This is not a claim of zero
absolute threads. Every additional fixture/Actor/worker/import thread remains a
strict leak; the negative integrity case starts a real Actor plus worker and
proves both are detected and then actually joined. Joint fixtures unregister
actual mock owners only after real mock-device stop proof; no positive evidence
is fabricated.

## Native ROS (separate authorized host)

Prepare ROS and custom interfaces **before** invoking the runner (and hence before its before-hash). Example on an isolated Linux ROS2/Humble host, using a Python interpreter compatible with sourced ROS:

```sh
source /opt/ros/humble/setup.bash
# External sibling workspace: no build/install/log files in any source root.
ROS_BUILD_ROOT="$(cd .. && pwd)/ros-validation-build"
colcon --log-base "$ROS_BUILD_ROOT/log" build --base-paths ../ex/ros_interfaces --build-base "$ROS_BUILD_ROOT/build" --install-base "$ROS_BUILD_ROOT/install"
source "$ROS_BUILD_ROOT/install/setup.bash"
ROS_DOMAIN_ID=73 ASTRBOTEX_TEST_ROS_DOMAIN_ID=73 python -B run_validation.py ros --ex-checkout ../ex --aeb-checkout ../aeb --allow-dirty
```

The child preserves and deduplicates inherited `PYTHONPATH`, including `rclpy` and the built custom interface paths. Build/install/log outputs are local only and must not be committed. The snapshot deliberately includes any existing ROS build outputs: do not build or edit sources during validation. Unsourced ROS or missing custom interfaces may cause original tests to skip; the strict runner **fails any skip**. DDS lifecycle/simulated stop evidence does not prove mechanical stopping or authorize production/hardware use. Native ARM/ROS release checks are performed separately by the coordinator.

## Evidence and boundaries

Each invocation creates a new unique ignored `artifacts/<mode>-<id>/` directory (or a new `--output` directory outside functional sources). It keeps raw stdout/stderr, their SHA-256, exit status, exact command, lock identity, per-file before/after hashes of all three repositories, case IDs/statuses, duplicate/load errors, skips and live thread leaks. Default whole-run timeout is 600 seconds; `--timeout` changes only the process bound, never a case budget. Any failure/error/skip/expected failure/duplicate/load error/thread leak or source change fails acceptance. Failed attempts are never overwritten. Exit 0 requires both a successful strict suite and byte-stable sources.

Migrated drivers use the `validation_drivers` module namespace, for example:

```sh
python -B -m validation_drivers.evaluate_jev_offline --ex-checkout ../ex --aeb-checkout ../aeb --repeats 3 --output artifacts/manual-offline/report.json
```

Drivers also accept explicit `--artifacts-dir`; live-only helpers require explicit local credential-file arguments and explicit live opt-in. **Do not run paid models, read credentials, contact production or attach hardware as part of these checks.** Historical docs under `docs/` retain their original result numbers, limitations and archive references; old archives remain local, not bundled with this public repository. Jev is shadow-only; real NapCat delivery, TTS playback and mechanical stopping remain untested. Historical reports are not release-clean evidence for the split.

Keep keys, raw logs, ZIPs, databases, caches and build outputs out of Git. Migration evidence (original repository/path → validation path, original and migrated SHA-256, plus original-ID coverage) is generated outside Git for coordinator review. The public repository carries portable code/docs/fixtures and allowlisted sanitized result derivatives only; raw evidence stays external. Publication, final lock updates and release-clean checks belong to the coordinator.
