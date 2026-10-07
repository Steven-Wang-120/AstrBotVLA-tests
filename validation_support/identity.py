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


UNTRACKED_SOURCE_ROOTS = {'astrbot_ex', 'astrbot_plugin_astrbotex_interaction', 'astrbot',
                          'tests', 'scripts', 'dashboard'}
UNTRACKED_SOURCE_SUFFIXES = {'.py', '.js', '.mjs', '.css', '.html', '.md'}
PRIVATE_SOURCE_PARTS = {'data', 'cache', 'secrets', 'credentials', 'runtime', 'logs'}


def git_paths(root, *args):
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', *args],
                            capture_output=True, check=False)
    if result.returncode:
        raise ValueError('checkout_git_identity_unavailable')
    return [name.decode('utf-8', errors='surrogateescape')
            for name in result.stdout.split(b'\0') if name]


def tracked_snapshot(root):
    """Tracked source plus allowlisted untracked functional code; no private data."""
    root = Path(root).resolve()
    names = set(git_paths(root, '--cached'))
    for name in git_paths(root, '--others', '--exclude-standard'):
        path = Path(name)
        if path.parts[0] in UNTRACKED_SOURCE_ROOTS and path.suffix in UNTRACKED_SOURCE_SUFFIXES:
            names.add(name)
    hashes = {}
    for name in sorted(names):
        path = Path(name)
        parts = {part.casefold() for part in path.parts}
        if (EXCLUDES.intersection(parts) or PRIVATE_SOURCE_PARTS.intersection(parts)
                or path.name.casefold().startswith('.env')
                or any(marker in path.stem.casefold() for marker in ('credential', 'api_key', 'access_token'))
                or path.suffix.casefold() in {'.pyc', '.pyo', '.db', '.sqlite', '.sqlite3', '.log', '.pem', '.key'}):
            continue
        source = root / path
        if source.is_file() and source.resolve().is_relative_to(root):
            hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    return hashes


def remediation_identity(lock_path, roots):
    """Record explicit current trees, without altering/claiming the historical lock."""
    lock = json.loads(Path(lock_path).read_text(encoding='utf-8'))
    identities = {}
    for name, root in roots.items():
        hashes = tracked_snapshot(root)
        status = git(root, 'status', '--porcelain', '--untracked-files=all')
        identities[name] = {'commit': git(root, 'rev-parse', 'HEAD'), 'dirty': bool(status),
                            'status': status, 'files': hashes,
                            'tree_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}
    return {'release_final': False, 'allow_dirty': True, 'certified_locked_clean_release': False,
            'source_kind': 'explicit remediation development trees; not historical pinned acceptance',
            'historical_lock': lock, 'repositories': identities}


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
