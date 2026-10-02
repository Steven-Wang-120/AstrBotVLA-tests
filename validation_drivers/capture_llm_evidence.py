"""Capture subprocess stdout/stderr/exit without changing global environment."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_support import bootstrap as paths
paths.bootstrap()


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        argparse.ArgumentParser(description="stem -- command; --artifacts-dir and --ex-checkout are explicit bootstrap options").parse_args()
        return 0
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        raise SystemExit("usage: capture_llm_evidence.py workspace_relative_stem -- command args")
    stem = (paths.artifacts_path("2026-10-01-llm") / sys.argv[1]).resolve()
    evidence = (paths.artifacts_path("2026-10-01-llm")).resolve()
    if not stem.is_relative_to(evidence):
        raise SystemExit("evidence path outside assigned workspace")
    stem.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(stem) + ".stdout.txt").open("xb") as stdout, \
         Path(str(stem) + ".stderr.txt").open("xb") as stderr, \
         Path(str(stem) + ".exit.json").open("x", encoding="utf-8") as exit_file:
        process = subprocess.run(sys.argv[3:], cwd=ROOT, stdout=stdout, stderr=stderr, check=False)
        json.dump({"command": sys.argv[3:], "exit_code": process.returncode}, exit_file, indent=2)
    print(json.dumps({"evidence_stem": str(stem), "exit_code": process.returncode}), flush=True)
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
