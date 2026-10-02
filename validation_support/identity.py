"""Read-only source identities and strict lock verification; no checkout mutations."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

EXCLUDES = {'.git', '__pycache__', '.pytest_cache', '.venv', 'venv', 'artifacts', 'task-evidence'}


def snapshot(root):
    root = Path(root).resolve()
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*'))
            if p.is_file() and not EXCLUDES.intersection(p.relative_to(root).parts)
            and p.suffix not in {'.pyc', '.pyo'}}


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError('checkout_git_identity_unavailable')
    return result.stdout.decode('utf-8').strip()


def verify_lock(lock_path, roots, allow_dirty=False):
    lock = json.loads(Path(lock_path).read_text(encoding='utf-8'))
    if lock.get('schema_version') != 1 or not isinstance(lock.get('release_final'), bool):
        raise ValueError('invalid_repository_lock_schema')
    identities = {}
    for name, root in roots.items():
        record = lock.get('repositories', {}).get(name, {})
        expected = record.get('commit', '')
        if not re.fullmatch('[0-9a-f]{40}', expected) or not record.get('url', '').startswith('https://github.com/'):
            raise ValueError('lock_requires_url_and_full_commit:' + name)
        head = git(root, 'rev-parse', 'HEAD')
        status = git(root, 'status', '--porcelain', '--untracked-files=all')
        if head != expected:
            raise ValueError('locked_commit_mismatch:' + name)
        if not allow_dirty and (not lock['release_final'] or status):
            raise ValueError('release_requires_final_lock_and_clean_checkout:' + name)
        hashes = snapshot(root)
        identities[name] = {'commit': head, 'locked_commit': expected, 'dirty': bool(status),
                            'status': status, 'files': hashes,
                            'tree_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}
    return {'release_final': lock['release_final'], 'allow_dirty': allow_dirty,
            'certified_locked_clean_release': lock['release_final'] and not allow_dirty,
            'repositories': identities}


def output_path(path, roots, validation):
    target = Path(path).resolve()
    for root in roots.values():
        root = Path(root).resolve()
        if target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError('output_must_not_overlap_functional_checkout')
    validation = Path(validation).resolve()
    if target.is_relative_to(validation) and not target.is_relative_to(validation / 'artifacts'):
        raise ValueError('validation_outputs_must_use_ignored_artifacts')
    return target
