"""Strict historical source archives, relocation and actual CLI failure tests; offline only."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from validation_drivers import audit_virtual_closed_loop_v2 as audit


class HistoricalSourceTests(unittest.TestCase):
    def fixture(self, root):
        identity = "C:\\historical-host\\repository\\scripts\\driver.py"
        data = b"# ORIGINAL historical source\n"
        expected = hashlib.sha256(data).hexdigest()
        archive = root / "source-before/validation_drivers/driver.py"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(data)
        mapping = {"schema_version": 1, "files": {identity: {"path": "source-before/validation_drivers/driver.py", "sha256": expected}}}
        path = root / "historical-source-map.json"
        path.write_text(json.dumps(mapping), encoding="utf-8")
        report = {"source_before": {identity: expected}, "source_after": {identity: expected}}
        return identity, archive, path, mapping, report

    def test_relative_archive_verifies_exact_old_absolute_identity_and_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            identity, archive, path, mapping, report = self.fixture(Path(temp))
            for selected in (path, path.parent):
                result = audit.verify_sources(report, selected)
                self.assertEqual(result["files_verified"], 1)
                self.assertEqual(result["origins"], {"archive": 1})
                self.assertTrue(result["source_before_equals_source_after"])

    def test_missing_archive_file_or_mapping_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            identity, archive, path, mapping, report = self.fixture(Path(temp))
            with self.assertRaises(FileNotFoundError):
                audit.verify_sources(report, path.parent / "missing-map.json")
            empty = copy.deepcopy(mapping)
            empty["files"] = {}
            path.write_text(json.dumps(empty), encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                audit.verify_sources(report, path)
            path.write_text(json.dumps(mapping), encoding="utf-8")
            archive.unlink()  # Test-created tempfile only, never actual evidence.
            with self.assertRaises(FileNotFoundError):
                audit.verify_sources(report, path)

    def test_tampered_archived_bytes_rejected_no_current_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            identity, archive, path, mapping, report = self.fixture(Path(temp))
            archive.write_bytes(b"# tampered source\n")
            with self.assertRaisesRegex(ValueError, "historical_source_bytes_sha_mismatch"):
                audit.verify_sources(report, path)

    def test_changed_archive_sha_identity_or_report_after_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            identity, archive, path, mapping, report = self.fixture(Path(temp))
            wrong = copy.deepcopy(mapping)
            wrong["files"][identity]["sha256"] = "0" * 64
            path.write_text(json.dumps(wrong), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source_archive_identity_sha_mismatch"):
                audit.verify_sources(report, path)
            wrong = copy.deepcopy(mapping)
            wrong["files"][identity + ".wrong"] = wrong["files"].pop(identity)
            path.write_text(json.dumps(wrong), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid_source_archive_identity_map"):
                audit.verify_sources(report, path)
            report["source_after"][identity] = "0" * 64
            with self.assertRaisesRegex(ValueError, "historical_source_before_after_mismatch"):
                audit.verify_sources(report)

    def test_all_sources_required_and_explicit_root_relocation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            identity, archive, path, mapping, report = self.fixture(root)
            unchanged = root / "relocated/docs/protocol.md"
            unchanged.parent.mkdir(parents=True)
            unchanged.write_bytes(b"# unchanged real source\n")
            old = "C:\\historical-host\\repository\\docs\\protocol.md"
            expected = hashlib.sha256(unchanged.read_bytes()).hexdigest()
            report["source_before"][old] = expected
            report["source_after"][old] = expected
            with self.assertRaises(FileNotFoundError):
                audit.verify_sources(report, path)
            result = audit.verify_sources(report, path, [("C:\\historical-host\\repository", root / "relocated")])
            self.assertEqual(result["files_verified"], 2)
            self.assertEqual(result["origins"], {"archive": 1, "source_root": 1})
            unchanged.write_bytes(b"# current file changed\n")
            with self.assertRaisesRegex(ValueError, "historical_source_bytes_sha_mismatch"):
                audit.verify_sources(report, path, [("C:\\historical-host\\repository", root / "relocated")])

    def test_unchanged_original_path_verified_without_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "original.py"
            path.write_bytes(b"# original unchanged\n")
            source = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()}
            result = audit.verify_sources({"source_before": source, "source_after": source.copy()})
            self.assertEqual(result["origins"], {"original_path": 1})

    def test_actual_cli_missing_and_tampered_archive_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            identity, archive, manifest, mapping, report = self.fixture(temp)
            evidence = temp / "evidence"
            evidence.mkdir()
            (evidence / "report.json").write_text(json.dumps(report), encoding="utf-8")
            # Source validation precedes task replay. This small test-authored
            # report is sufficient to prove the exact source rejection path;
            # it is not presented as a complete 120-task replay fixture.
            self.assertEqual(audit.verify_sources(report, manifest)["files_verified"], 1)
            command = [sys.executable, "-B", str(Path(audit.__file__)), "--evidence-dir", str(evidence), "--source-archive"]
            missing_map = temp / "missing.json"
            with self.assertRaises(FileNotFoundError) as missing_error:
                audit.audit(evidence, missing_map)
            self.assertEqual(Path(missing_error.exception.filename), missing_map)
            missing = subprocess.run(command + [str(missing_map)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(missing.returncode, 1)
            self.assertEqual(missing.stdout, "")
            expected_error = {"status": "rejected", "code": "evidence_or_historical_source_verification_failed", "network_calls": 0}
            self.assertEqual(json.loads(missing.stderr), expected_error)
            archive.write_bytes(b"tampered historical bytes")
            # The map and report still contain the original matching identity/SHA.
            # Require the actual byte-SHA failure, not missing report or a later
            # task-shape assertion that could also make the CLI return exit 1.
            self.assertEqual(mapping["files"][identity]["sha256"], report["source_before"][identity])
            with self.assertRaisesRegex(ValueError, "^historical_source_bytes_sha_mismatch$"):
                audit.audit(evidence, manifest)
            bad = subprocess.run(command + [str(manifest)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(bad.returncode, 1)
            self.assertEqual(bad.stdout, "")
            self.assertEqual(json.loads(bad.stderr), expected_error)


if __name__ == "__main__":
    unittest.main()
