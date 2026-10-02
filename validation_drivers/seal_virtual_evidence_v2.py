"""Seal v2 deliverable with final file hashes and exact in-memory secret scan."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_support import bootstrap as paths
paths.bootstrap()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validation_drivers import audit_virtual_closed_loop_v2 as audit
from validation_drivers import evaluate_virtual_closed_loop_v2 as v
from validation_drivers import evaluate_llm_live as llm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-secret-read", action="store_true", help="explicit opt-in for in-memory exact secret scan")
    options = parser.parse_args()
    if not options.allow_secret_read or llm.DB_DEFAULT is None or v.KEY_FILE is None:
        parser.error("explicit --allow-secret-read, --cc-switch-db and --jev-key-file required")
    result = audit.audit()
    provider = llm.read_provider(llm.DB_DEFAULT, v.PROVIDER_ID, "openai")
    key = v.read_first_key()
    directories = [v.EVIDENCE, audit.OUT, paths.artifacts_path("2026-10-01-llm-v2-checks")]
    sources = [ROOT / "README.md"]
    sources += list((ROOT / "validation_drivers").glob("*virtual*v2.py"))
    sources += list((ROOT / "validation_drivers").glob("b07_virtual_tasks_v2.py"))
    sources += list((ROOT / "validation_tests/evaluation").glob("test_virtual*.py"))
    sources += list((ROOT / "docs").glob("B07-VIRTUAL-V2-*.md"))
    sources += [v.SPEC]
    paths = set(sources + [p for d in directories for p in d.rglob("*") if p.is_file()])
    manifest = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    source_hits = sum(provider.secret.encode() in p.read_bytes() or key.encode() in p.read_bytes() for p in sources)
    scans = {str(d.resolve()): llm.scan_secrets(d, [provider.secret, key]) for d in directories}
    if source_hits or any(s["exact_in_memory_secret_matches"] for s in scans.values()):
        raise ValueError("secret_match")
    llm.write_json(audit.OUT / "closeout-sha256.json", {
        "hashes": manifest, "scope": "all v2 final source/docs/task evidence/raw results before this seal; manifest and its stdout are excluded to avoid self-reference",
        "independent_final_audit": result, "exact_scans": scans, "source_secret_matches": source_hits,
        "thread_id": None, "thread_id_basis": "MARINA coordinator owns launch-tool threadId; worker cannot observe it",
        "execution_allowed": False, "actual_invoice": None})
    print(json.dumps({"status": "sealed", "hashes": len(manifest), "source_files_scanned": len(sources),
                      "exact_secret_matches": 0, "independent_audit": result, "thread_id": None}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({"status": "failed", "code": "seal_error_redacted"}), file=sys.stderr)
        raise SystemExit(2)
