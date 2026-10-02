"""Exclusive raw exit capture for the v2 experiment; v1 capture remains frozen."""
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
CHECKS = paths.artifacts_path("2026-10-01-llm-v2-checks")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stem")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    if "--" not in sys.argv or not options.command:
        parser.error("command must follow --")
    command = options.command[1:] if options.command[0] == "--" else options.command
    if not options.stem.replace("-", "").isalnum():
        parser.error("invalid evidence stem")
    CHECKS.mkdir(parents=True, exist_ok=True)
    stem = CHECKS / options.stem
    with Path(str(stem) + ".stdout.txt").open("xb") as out, \
         Path(str(stem) + ".stderr.txt").open("xb") as err, \
         Path(str(stem) + ".exit.json").open("x", encoding="utf-8") as status:
        result = subprocess.run(command, cwd=ROOT, stdout=out, stderr=err, check=False)
        json.dump({"command": command, "exit_code": result.returncode}, status, indent=2)
    print(json.dumps({"raw_evidence_stem": str(stem), "exit_code": result.returncode}), flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
