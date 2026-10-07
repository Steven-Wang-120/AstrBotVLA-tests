"""Portable boundaries and separate product/harness identities for remediation checks."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from validation_support.bootstrap import ROOT, ex_root
from validation_support.identity import output_path

PRODUCT_FILES = (
    'astrbot_ex/core/api_server.py', 'astrbot_ex/core/decision/management.py',
    'astrbot_ex/core/decision/service.py', 'astrbot_ex/core/decision/controller.py',
    'astrbot_ex/core/decision/config.py', 'astrbot_ex/core/actions/dispatcher.py',
    'astrbot_ex/core/connection_manager.py', 'astrbot_ex/core/decision_transport.py',
    'astrbot_ex/core/decision/backends/jev.py', 'astrbot_ex/core/decision/backends/laya.py',
    'dashboard/app.js', 'dashboard/decision.js', 'dashboard/index.html', 'dashboard/styles.css',
)
HARNESS_FILES = (
    'validation_drivers/diagnose_dispatcher_stress.py',
    'validation_drivers/verify_decision_management.py', 'validation_drivers/verify_decision_ui.mjs',
    'validation_support/decision_ui_fixture.py', 'validation_support/decision_ui_real_fixture.py',
    'validation_support/bootstrap.py', 'validation_support/remediation.py',
    'validation_support/suite.py', 'validation_support/identity.py', 'run_validation.py',
    'validation_tests/integration/test_decision_aeb_replanning.py',
)


def checkout_arguments(parser):
    # bootstrap has already consumed these and propagated them to subprocesses.
    for flag in ('--ex-checkout', '--aeb-checkout', '--host-checkout', '--artifacts-dir'):
        parser.add_argument(flag, metavar='PATH', help='explicit checkout/output path (shared bootstrap)')


def source_hashes():
    return {'product': {name: hashlib.sha256((ex_root() / name).read_bytes()).hexdigest()
                        for name in PRODUCT_FILES},
            'harness': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in HARNESS_FILES}}


def fresh_output(path):
    roots = {'ex': ex_root()}
    for name, env in (('aeb', 'ASTRBOTEX_TEST_AEB_CHECKOUT'), ('host', 'ASTRBOTVLA_TEST_HOST_CHECKOUT')):
        if os.environ.get(env):
            roots[name] = Path(os.environ[env]).resolve()
    target = output_path(path, roots, ROOT)
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    return target
