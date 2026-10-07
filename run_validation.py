"""Pinned, read-only multi-repository acceptance with original raw evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from validation_support.identity import output_path, snapshot, tracked_snapshot, verify_lock, remediation_identity

MODES = ('ex-unit', 'offline', 'wiring', 'replanning', 'ex-offline', 'host-unit', 'host-integration', 'campaign', 'ros')


def command_for(args, roots, output):
    env = os.environ.copy()
    paths = [str(ROOT), str(roots['ex']), str(roots['aeb'])]
    if 'host' in roots:
        paths.append(str(roots['host']))
        env['ASTRBOTVLA_TEST_HOST_CHECKOUT'] = str(roots['host'])
    paths.extend(path for path in env.get('PYTHONPATH', '').split(os.pathsep) if path)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
               ASTRBOTEX_TEST_CHECKOUT=str(roots['ex']), ASTRBOTEX_TEST_AEB_CHECKOUT=str(roots['aeb']),
               ASTRBOTVLA_ARTIFACTS=str(output / 'evaluation-artifacts'),
               PYTHONPATH=os.pathsep.join(dict.fromkeys(paths)))
    cwd = roots['ex']
    if args.mode == 'replanning':
        # Set both before the child can warm up/import any real Host modules.
        cwd = (output / 'host-runtime').resolve()
        cwd.mkdir(parents=True, exist_ok=False)
        env['ASTRBOT_ROOT'] = str(cwd)
    if args.mode not in ('host-unit', 'host-integration', 'campaign'):
        return [sys.executable, '-B', str(ROOT / 'validation_support/suite.py'), args.mode,
                '--result', str(output / 'suite.json')], env, cwd, None
    # Inspect only an existing explicit image. Never pull/build/update a Host.
    inspect = subprocess.run(['docker', 'image', 'inspect', args.host_image, '--format', '{{.Id}}'], capture_output=True)
    if inspect.returncode:
        raise RuntimeError('host_image_or_docker_unavailable:' + inspect.stderr.decode('utf-8', errors='replace'))
    image = inspect.stdout.decode().strip()
    command = ['docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
               '--tmpfs', '/tmp', '--tmpfs', '/work/data', '--tmpfs', '/AstrBot/data',
               '--mount', f'type=bind,source={ROOT},target=/validation,readonly',
               '--mount', f'type=bind,source={roots["ex"]},target=/ex-src,readonly',
               '--mount', f'type=bind,source={roots["aeb"]},target=/aeb-src,readonly',
               '--mount', f'type=bind,source={output},target=/evidence',
               '--workdir', '/tmp', '-e', 'PYTHONPATH=/validation:/aeb-src:/ex-src:/AstrBot',
               '-e', 'ASTRBOTEX_TEST_CHECKOUT=/ex-src', '-e', 'ASTRBOTEX_TEST_AEB_CHECKOUT=/aeb-src',
               '-e', 'ASTRBOTVLA_ARTIFACTS=/evidence/evaluation-artifacts',
               '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONUNBUFFERED=1', '--entrypoint', 'python', image, '-B']
    if args.mode == 'campaign':
        command += ['-m', 'validation_tests.host.finite_campaign', '--result', '/evidence/suite.json']
    else:
        command += ['/validation/validation_support/suite.py', args.mode, '--result', '/evidence/suite.json']
    return command, env, ROOT, image


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=MODES)
    parser.add_argument('--ex-checkout', type=Path, default=os.environ.get('ASTRBOTEX_TEST_CHECKOUT'))
    parser.add_argument('--aeb-checkout', type=Path, default=os.environ.get('ASTRBOTEX_TEST_AEB_CHECKOUT'))
    parser.add_argument('--host-checkout', type=Path, default=os.environ.get('ASTRBOTVLA_TEST_HOST_CHECKOUT'),
                        help='optional explicit Host SDK source; otherwise use already installed SDK')
    parser.add_argument('--remediation-sources', action='store_true',
                        help='replanning only: record current remediation commits separately from historical lock; requires --allow-dirty')
    parser.add_argument('--lock', type=Path, default=ROOT / 'repositories.lock.json')
    parser.add_argument('--allow-dirty', action='store_true', help='development only: record exact dirty trees; never release-certified')
    parser.add_argument('--host-image', default='local/hzf-aeb-baseline:20260927')
    parser.add_argument('--output', type=Path, help='new unique directory outside functional sources (default ignored artifacts/)')
    parser.add_argument('--timeout', type=int, default=600, help='whole-run bound, not per-case budget')
    args = parser.parse_args(argv)
    if args.ex_checkout is None or args.aeb_checkout is None:
        parser.error('explicit EX and AEB checkouts required (arguments or named environment variables)')
    roots = {'ex': Path(args.ex_checkout).resolve(), 'aeb': Path(args.aeb_checkout).resolve()}
    for name, marker in (('ex', 'astrbot_ex/core/api_server.py'), ('aeb', 'astrbot_plugin_astrbotex_interaction/main.py')):
        if not (roots[name] / marker).is_file():
            parser.error('invalid functional checkout:' + name)
    if roots['ex'] == roots['aeb'] or any(ROOT.is_relative_to(r) or r.is_relative_to(ROOT) for r in roots.values()):
        parser.error('functional and validation checkouts must be disjoint')
    if args.remediation_sources and (args.mode != 'replanning' or not args.allow_dirty):
        parser.error('--remediation-sources is noncertified replanning only and requires --allow-dirty')
    if args.host_checkout:
        if args.mode != 'replanning':
            parser.error('--host-checkout is for replanning; other Host modes use the explicit offline image')
        roots['host'] = Path(args.host_checkout).resolve()
        if not (roots['host'] / 'astrbot/core/agent/message.py').is_file():
            parser.error('invalid explicit Host checkout')
    if any(ROOT.is_relative_to(r) or r.is_relative_to(ROOT) for r in roots.values()):
        parser.error('functional and validation checkouts must be disjoint')
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for i, a in enumerate(roots.values()) for b in list(roots.values())[i + 1:]):
        parser.error('functional checkouts must be disjoint')
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    output = output_path(args.output or ROOT / 'artifacts' / (args.mode + '-' + uuid.uuid4().hex), roots, ROOT)
    output.mkdir(parents=True, exist_ok=False)
    all_roots = {**roots, 'validation': ROOT}
    source_snapshot = lambda name, root: (tracked_snapshot(root) if args.remediation_sources and name != 'validation' else snapshot(root))
    before = {name: source_snapshot(name, root) for name, root in all_roots.items()}
    (output / 'source-before.json').write_text(json.dumps(before, indent=2) + '\n', encoding='utf-8')
    command, image, identity, execution_context = [], None, None, None
    started = time.monotonic()
    error = None
    code = 1
    stdout = stderr = b''
    try:
        identity = (remediation_identity(args.lock, roots) if args.remediation_sources
                    else verify_lock(args.lock, {k: v for k, v in roots.items() if k != 'host'}, args.allow_dirty))
        identity['validation'] = {'files': before['validation'], 'lock_sha256': hashlib.sha256(args.lock.read_bytes()).hexdigest(),
                                  'git_status': subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=all'], capture_output=True).stdout.decode()}
        command, env, cwd, image = command_for(args, roots, output)
        execution_context = {'cwd': str(cwd), 'host_runtime_root': env.get('ASTRBOT_ROOT')}
        try:
            result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=args.timeout)
            stdout, stderr, code = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code = exc.stdout or b'', (exc.stderr or b'') + b'\nRUNNER_TIMEOUT\n', 124
    except (ValueError, OSError, RuntimeError) as exc:
        error = str(exc)
        stderr += (error + '\n').encode('utf-8')
    (output / 'stdout.raw.log').write_bytes(stdout)
    (output / 'stderr.raw.log').write_bytes(stderr)
    after = {name: source_snapshot(name, root) for name, root in all_roots.items()}
    (output / 'source-after.json').write_text(json.dumps(after, indent=2) + '\n', encoding='utf-8')
    suite_file = output / 'suite.json'
    suite = json.loads(suite_file.read_text(encoding='utf-8')) if suite_file.exists() else None
    ok = code == 0 and before == after and suite is not None and suite.get('ok') is True
    record = {'mode': args.mode, 'command': command, 'process_exit_code': code, 'exit_code': 0 if ok else (code or 1),
              'ok': ok, 'error': error, 'elapsed_sec': time.monotonic() - started, 'identity': identity,
              'execution_context': execution_context,
              'source_unchanged': {name: before[name] == after[name] for name in before}, 'host_image_id': image,
              'raw_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in
                             (('stdout.raw.log', stdout), ('stderr.raw.log', stderr))},
              'suite': suite, 'scope': 'offline only; no paid HTTP, production network or hardware acceptance'}
    (output / 'result.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    print('EVIDENCE ' + str(output) + ' EXIT ' + str(record['exit_code']) + ' SOURCE_UNCHANGED ' + str(before == after), flush=True)
    return record['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
