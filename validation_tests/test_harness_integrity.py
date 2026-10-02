"""Behavioral integrity checks for the new lock, output and collection boundary."""
from __future__ import annotations

import json
import threading
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from validation_support.identity import output_path, snapshot, verify_lock
from validation_support.suite import module_cases


class HarnessIntegrityTests(unittest.TestCase):
    def lock(self, root, final=True):
        path = root / 'lock.json'
        path.write_text(json.dumps({'schema_version': 1, 'release_final': final, 'repositories': {
            'ex': {'url': 'https://github.com/example/ex', 'commit': 'a' * 40}}}), encoding='utf-8')
        return path

    def test_exact_commit_and_clean_release_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkout = root / 'checkout'
            checkout.mkdir()
            (checkout / 'source.py').write_bytes(b'original')
            lock = self.lock(root)
            with patch('validation_support.identity.git', side_effect=['b' * 40, '']):
                with self.assertRaisesRegex(ValueError, 'locked_commit_mismatch'):
                    verify_lock(lock, {'ex': checkout})
            with patch('validation_support.identity.git', side_effect=['a' * 40, ' M source.py']):
                with self.assertRaisesRegex(ValueError, 'clean_checkout'):
                    verify_lock(lock, {'ex': checkout})
            with patch('validation_support.identity.git', side_effect=['a' * 40, '']):
                self.assertTrue(verify_lock(lock, {'ex': checkout})['certified_locked_clean_release'])

    def test_dirty_mode_records_overlay_not_only_base(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkout = root / 'checkout'
            checkout.mkdir()
            source = checkout / 'source.py'
            source.write_bytes(b'overlay one')
            lock = self.lock(root, False)
            with patch('validation_support.identity.git', side_effect=['a' * 40, ' M source.py']):
                first = verify_lock(lock, {'ex': checkout}, True)
            source.write_bytes(b'overlay two')
            with patch('validation_support.identity.git', side_effect=['a' * 40, ' M source.py']):
                second = verify_lock(lock, {'ex': checkout}, True)
            self.assertFalse(first['certified_locked_clean_release'])
            self.assertNotEqual(first['repositories']['ex']['tree_sha256'], second['repositories']['ex']['tree_sha256'])
            with patch('validation_support.identity.git', side_effect=['a' * 40, '']):
                with self.assertRaisesRegex(ValueError, 'release_requires_final_lock'):
                    verify_lock(lock, {'ex': checkout})

    def test_bad_lock_full_sha_required_even_dirty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = self.lock(root)
            content = json.loads(lock.read_text())
            content['repositories']['ex']['commit'] = 'main'
            lock.write_text(json.dumps(content))
            with self.assertRaisesRegex(ValueError, 'full_commit'):
                verify_lock(lock, {'ex': root}, True)

    def test_outputs_cannot_overlap_sources_and_hashes_detect_new_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkout = root / 'ex'
            checkout.mkdir()
            with self.assertRaisesRegex(ValueError, 'overlap'):
                output_path(checkout / 'results', {'ex': checkout}, root / 'validation')
            with self.assertRaisesRegex(ValueError, 'overlap'):
                output_path(root, {'ex': checkout}, root / 'validation')
            first = snapshot(checkout)
            (checkout / 'new.py').write_bytes(b'new')
            self.assertNotEqual(first, snapshot(checkout))

    def test_live_actor_and_worker_are_not_hidden_by_framework_baseline(self):
        from astrbot_ex.core.plugin_actor import PluginActor
        from validation_support.host_lifecycle import thread_leaks
        baseline = set(threading.enumerate())
        actor = PluginActor(types.SimpleNamespace(id='integrity-negative-actor'))
        release = threading.Event()
        worker = threading.Thread(target=release.wait, name='integrity-negative-worker', daemon=True)
        try:
            actor.start()
            worker.start()
            names = {entry['name'] for entry in thread_leaks(baseline)}
            self.assertIn(actor.thread_name, names)
            self.assertIn(worker.name, names)
        finally:
            release.set()
            worker.join(2)
            actor.stop(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(actor.alive)
        self.assertEqual(thread_leaks(baseline), [])

    def test_imported_fixture_cases_are_not_collected_twice(self):
        module = types.ModuleType('authored_validation_test')
        class ImportedFixture(unittest.TestCase):
            def test_external(self):
                pass
        module.ImportedFixture = ImportedFixture
        exec('import unittest\nclass LocalCase(unittest.TestCase):\n    def test_owned(self): pass\n', module.__dict__)
        with patch('validation_support.suite.importlib.import_module', return_value=module):
            cases = module_cases(module.__name__)
        self.assertEqual([c.id() for c in cases], ['authored_validation_test.LocalCase.test_owned'])
