"""Strict unittest collection, case provenance and unwanted thread detection."""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation_support.bootstrap import bootstrap
bootstrap()

HOST_MODULES = ['test_plugin_import', 'test_plugin_channels', 'test_zmq_transport',
                'test_task_contracts', 'test_task_store', 'test_task_coordinator',
                'test_planning_tools', 'test_feedback_sync', 'test_output_isolation',
                'test_public_output_router', 'test_lease_lifecycle', 'test_task_admission', 'test_task_turn_sync']


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def module_cases(name):
    module = importlib.import_module(name)
    # unittest's default module loader also collects imported TestCase classes.
    # Collect only classes defined here; external fixture classes aren't cases.
    suite = unittest.TestSuite()
    for value in vars(module).values():
        if isinstance(value, type) and issubclass(value, unittest.TestCase) and value.__module__ == name:
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(value))
    return list(flatten(suite))


def modules_for(mode):
    ex = Path(os.environ['ASTRBOTEX_TEST_CHECKOUT'])
    evaluation = ['validation_tests.evaluation.' + p.stem for p in sorted((ROOT / 'validation_tests/evaluation').glob('test_*.py'))]
    if mode == 'ex-unit':
        return ['tests.' + p.stem for p in sorted((ex / 'tests').glob('test_*.py'))]
    if mode == 'offline':
        return evaluation + ['validation_tests.test_harness_integrity']
    if mode == 'wiring':
        return ['validation_tests.integration.test_decision_wiring']
    if mode == 'replanning':
        return ['validation_tests.integration.test_decision_aeb_replanning']
    if mode == 'ex-offline':
        return modules_for('ex-unit') + modules_for('offline') + modules_for('wiring')
    if mode == 'host-unit':
        return ['astrbot_plugin_astrbotex_interaction.tests.' + name for name in HOST_MODULES]
    if mode == 'host-integration':
        return ['validation_tests.host.test_host_decision_composition',
                'validation_tests.host.test_decision_rejection_zmq', 'validation_tests.host.test_joint_fixture_helpers']
    if mode == 'ros':
        return ['validation_tests.ros.test_ros2_integration']
    raise ValueError('unknown suite')


class Results(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({'id': test.id(), 'status': 'passed'})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records.append({'id': test.id(), 'status': 'failed'})

    def addError(self, test, err):
        super().addError(test, err)
        self.records.append({'id': test.id(), 'status': 'error'})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records.append({'id': test.id(), 'status': 'skipped', 'reason': reason})


from validation_support.host_lifecycle import framework_baseline, thread_leaks


def run(mode, output):
    baseline = set(threading.enumerate())
    framework = None
    if mode in ('host-unit', 'host-integration') or (mode == 'replanning' and importlib.util.find_spec('astrbot') is not None):
        baseline, framework = framework_baseline()
    result = None
    cases = []
    load_errors = []
    try:
        for name in modules_for(mode):
            cases.extend(module_cases(name))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        load_errors.append(type(exc).__name__)
    ids = [test.id() for test in cases]
    duplicates = sorted({test_id for test_id in ids if ids.count(test_id) > 1})
    if not load_errors and not duplicates and cases:
        result = unittest.TextTestRunner(verbosity=2, resultclass=Results).run(unittest.TestSuite(cases))
    # Suite fixtures must join their workers; the runner neither hides nor fixes leaks.
    leaked = thread_leaks(baseline)
    report = {'mode': mode, 'framework_baseline': framework, 'collected': len(cases), 'case_ids': ids,
              'duplicate_case_ids': duplicates, 'load_errors': load_errors,
              'tests_run': result.testsRun if result else 0,
              'failures': len(result.failures) if result else 0,
              'errors': len(result.errors) if result else 0,
              'skipped': len(result.skipped) if result else 0,
              'unexpected_successes': len(result.unexpectedSuccesses) if result else 0,
              'expected_failures': len(result.expectedFailures) if result else 0,
              'thread_leaks': leaked, 'cases': result.records if result else []}
    report['ok'] = bool(result and result.wasSuccessful() and not result.skipped and not result.expectedFailures
                        and not result.unexpectedSuccesses and not leaked and not load_errors and not duplicates)
    Path(output).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('SUITE_RESULT ' + json.dumps({k: v for k, v in report.items() if k not in ('case_ids', 'cases')}), flush=True)
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode')
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    return run(args.mode, args.result)


if __name__ == '__main__':
    raise SystemExit(main())
