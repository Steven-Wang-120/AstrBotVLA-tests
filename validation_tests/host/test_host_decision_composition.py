"""Real offline Host ADMIN -> ZMQ -> EX composition acceptance, not fake EX."""
from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from validation_tests.host.host_decision_helper import (
    JointHarness, admin_event, dispatch_admin, aeb, OwnerBinding, StopEvidence,
)
from astrbot_plugin_astrbotex_interaction.task_contracts import Feedback
from astrbot_plugin_astrbotex_interaction.planning_tools import PlanningTools
from astrbot_plugin_astrbotex_interaction.task_models import PlanningTurn, TaskAuthority, TaskError
from astrbot_plugin_astrbotex_interaction.task_store import TaskStore


class HostDecisionCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = JointHarness(Path(self.tmp.name))
        self.addCleanup(self.tmp.cleanup)
        self.addAsyncCleanup(self.h.close)
        await self.h.start()

    async def test_joint_three_steps_actual_owner_once_and_verified_journal(self):
        h = self.h
        task_id = await h.create('quiet', 3, 'PRIVATE_MARKER-three-step')
        ids = []
        for index in range(3):
            command = await h.started(task_id, index)
            ids.append(command.command_id)
            self.assertEqual(h.task(task_id)['active_step'], index)
            self.assertEqual(sum(c.goal_id == command.goal_id for c in h.owner.starts), 1)
            await h.complete(task_id, command, index)
        await h.wait(lambda: not h.task(task_id)['active'], 'finish complete privately')
        self.assertEqual(len(h.owner.starts), 3)
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(h.task(task_id)['status'], 'completed')
        self.assertEqual(h.texts, [])
        self.assertEqual(h.audio, [])
        submits = [p for m, p in h.requests if m == 'decision.goal.submit']
        self.assertEqual(len(submits), 3)
        self.assertEqual([p['expected_revision'] for p in submits], [0, 1, 2])
        self.assertEqual({p['schema_version'] for p in submits}, {1})
        for call in h.provider.calls:
            self.assertFalse(call['stream'])
            self.assertEqual(len(call['func_tool'].tools), 4)
        final = h.server.decision_controller.journal.snapshot()['feedback']
        self.assertEqual(final['task_id'], task_id)
        self.assertEqual(final['status'], 'succeeded')
        self.assertTrue(final['details']['completion_evidence']['verified'])
        self.assertEqual(final['details']['completion_evidence']['succeeded_actions'], ['joint_mock.step.v1'])
        self.assertGreater(h.task(task_id)['last_event_seq'], 0)
        self.assertGreater(h.plugin.task_store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 0)
        print('JOINT_THREE_STEP', json.dumps({'starts': 3, 'text': 0, 'tts': 0,
              'goal_revisions': [p['expected_revision'] + 1 for p in submits], 'commands': ids}), flush=True)

    async def test_emit_question_and_finish_queue_actual_public_xor_tts(self):
        h = self.h
        entered, release = threading.Event(), threading.Event()
        class OfflineTTS:
            async def get_audio(self, text):
                entered.set()
                await asyncio.to_thread(release.wait, 2)
                return 'offline-test-audio.wav'
        h.server.interaction_core.tts_provider = OfflineTTS()
        h.plugin.task_coordinator.router.delivery = 'tts'
        task_id = await h.create('emit', 1, 'PRIVATE_MARKER-emit')
        command = await h.started(task_id)
        await h.wait(entered.is_set, 'real public delivery worker entered offline TTS')
        self.assertIsNone(h.task(task_id)['turn_id'])
        release.set()
        await h.wait(lambda: len(h.audio) == 1, 'finished turn did not swallow legal queued audio')
        self.assertEqual(h.texts, [])
        self.assertNotIn('PRIVATE_MARKER', str(h.audio))
        await h.complete(task_id, command, 0)
        await h.wait(lambda: not h.task(task_id)['active'], 'first task finished')
        h.plugin.task_coordinator.router.delivery = 'text'
        before_starts = len(h.owner.starts)
        question = await h.create('question', 1, 'PRIVATE_MARKER-question')
        await h.wait(lambda: h.task(question)['status'] == 'waiting_input' and len(h.texts) == 1, 'question public chain')
        self.assertEqual(len(h.owner.starts), before_starts)
        self.assertEqual(len(h.audio), 1)
        self.assertNotIn('PRIVATE_MARKER', str(h.texts))
        print('JOINT_O02_O03', json.dumps({'O02': {'start': 1, 'text': 0, 'tts': 1},
              'O03': {'start': 0, 'text': 1, 'tts': 0}}), flush=True)

    async def test_100_markers_actual_public_chain_O01_O02_O03(self):
        h = self.h
        stats = {key: {'cases': 0, 'start': 0, 'text': 0, 'tts': 0} for key in ('O01', 'O02', 'O03')}
        begin = time.monotonic()
        for index in range(100):
            mode = ('quiet', 'emit', 'question')[index % 3]
            key = {'quiet': 'O01', 'emit': 'O02', 'question': 'O03'}[mode]
            marker = 'PRIVATE_MARKER-joint-' + str(index)
            before = (len(h.owner.starts), len(h.texts), len(h.audio))
            task_id = await h.create(mode, 1, marker)
            if mode == 'question':
                await h.wait(lambda: h.task(task_id)['status'] == 'waiting_input' and len(h.texts) == before[1] + 1,
                             'question marker ' + str(index))
                result = await dispatch_admin(h.plugin, 'ex_task_cancel', task_id)
                self.assertEqual(result.get_result().get_plain_text(), '')
            else:
                command = await h.started(task_id)
                if mode == 'emit':
                    await h.wait(lambda: len(h.texts) == before[1] + 1, 'emit marker ' + str(index))
                await h.complete(task_id, command, 0)
                await h.wait(lambda: not h.task(task_id)['active'], 'finish marker ' + str(index))
            delta = (len(h.owner.starts) - before[0], len(h.texts) - before[1], len(h.audio) - before[2])
            self.assertEqual(delta, {'quiet': (1, 0, 0), 'emit': (1, 1, 0), 'question': (0, 1, 0)}[mode])
            self.assertNotIn(marker, str(h.texts) + str(h.audio))
            stats[key]['cases'] += 1
            for field, value in zip(('start', 'text', 'tts'), delta):
                stats[key][field] += value
        self.assertNotIn('PRIVATE_MARKER', str(h.texts) + str(h.audio))
        print('JOINT_100_MARKERS', json.dumps({'stats': stats, 'leaks': 0,
              'elapsed_sec': round(time.monotonic() - begin, 3)}), flush=True)

    async def test_true_update_cancel_authority_and_late_provider_result(self):
        h = self.h
        h.provider.latched = True
        task_id = await h.create('emit', 1, 'PRIVATE_MARKER-late')
        await h.wait(h.provider.started.is_set, 'Host offline provider held')
        task = h.task(task_id)
        for command in ('ex_task_update', 'ex_task_cancel', 'ex_task_review'):
            tail = task_id + (' changed' if command == 'ex_task_update' else '')
            foreign = await dispatch_admin(h.plugin, command, tail, session='other-session')
            self.assertIn('owner_mismatch', foreign.get_result().get_plain_text())
            foreign_user = await dispatch_admin(h.plugin, command, tail, user='other-user')
            self.assertIn('owner_mismatch', foreign_user.get_result().get_plain_text())
        route = h.plugin.task_store.route(task['route_ref'])
        with self.assertRaises(TaskError):
            h.plugin.admit_task_route(TaskAuthority('foreign-robot', task['session_id'], task['user_id'], task['route_ref'], True), b'joint-trusted')
        with self.assertRaises(TaskError):
            h.plugin.admit_task_route(TaskAuthority(task['robot_id'], task['session_id'], task['user_id'], task['route_ref'], True), b'foreign-peer')
        canceled = await dispatch_admin(h.plugin, 'ex_task_cancel', task_id)
        self.assertEqual(canceled.get_result().get_plain_text(), '')
        self.assertEqual(h.task(task_id)['status'], 'canceled')
        h.provider.release.set()
        await asyncio.sleep(0.2)
        self.assertEqual(h.owner.starts, [])
        self.assertEqual(h.texts, [])
        self.assertEqual(h.audio, [])
        self.assertEqual(h.plugin.task_store.route(task['route_ref']), route)

    async def test_real_missing_stop_proof_prevents_update_advance(self):
        h = self.h
        task_id = await h.create('quiet', 1, 'PRIVATE_MARKER-stop')
        command = await h.started(task_id)
        h.owner.stop_proof = False
        h.server.action_service.stop_timeout = 0.15
        with self.assertRaisesRegex(ValueError, 'positive structured stop evidence'):
            h.owner.context.actions.report(command.command_id, 'canceled')
        replacement = json.dumps({'mode': 'quiet', 'steps': 1, 'marker': 'PRIVATE_MARKER-replacement'})
        updated = await dispatch_admin(h.plugin, 'ex_task_update', task_id + ' ' + replacement)
        self.assertEqual(updated.get_result().get_plain_text(), '')
        await h.wait(lambda: h.server.decision_service.goals.phase == 'blocked', 'missing stop proof blocks real goal')
        self.assertEqual(h.task(task_id)['active_step'], 0)
        self.assertIsNotNone(h.task(task_id)['current_goal'])
        self.assertFalse(h.task(task_id)['needs_planning'])
        self.assertEqual(len(h.owner.starts), 1)
        self.assertEqual(h.texts, [])
        self.assertIsNone(h.server.decision_controller.journal.snapshot()['feedback'])
        # Assertions above establish the missing-proof failure. Only now park the
        # simulated device, using the bound SDK's late-stop reconciliation path.
        await h.wait(lambda: h.server.action_ledger.get(command.command_id).result(1).status
                     in {'timed_out', 'unknown'}, 'uncertain command awaiting late device stop')
        proof = StopEvidence(command.command_id, True, 'joint_mock', 'cleanup-parked-' + command.command_id)
        row = await h.report(command, 'canceled', stop_evidence=proof)
        self.assertIn(row.status, {'timed_out', 'unknown'})
        self.assertEqual(row.held_resources, ())
        committed = await asyncio.wrap_future(h.server.action_ledger.stop_proof(
            command.command_id, OwnerBinding(row.owner, row.generation)))
        self.assertEqual(committed, proof)
        self.assertEqual(h.server.decision_service.goals.phase, 'blocked')
        await asyncio.wait_for(asyncio.wrap_future(h.server.decision_service.review()), timeout=5)
        await h.wait(lambda: h.server.decision_service.goals.phase == 'idle', 'explicit framework stop review')
        self.assertEqual(len(h.owner.starts), 1)

    async def test_real_disabled_business_rejection_is_durable_not_retry(self):
        h = self.h
        h.server.decision_service.set_mode('disabled')
        await h.wait(lambda: h.server.decision_service.goals.phase == 'idle', 'disabled gate')
        task_id = await h.create('quiet', 1, 'PRIVATE_MARKER-rejected')
        await h.wait(lambda: any(m == 'decision.goal.submit' for m, p in h.requests), 'submit business rejection')
        await h.wait(lambda: h.task(task_id)['current_goal'] is None, 'determinate rejection clears goal')
        submits = [p for m, p in h.requests if m == 'decision.goal.submit']
        self.assertEqual(len(submits), 1)
        record = h.plugin.task_store.request(submits[0]['request_id'])
        self.assertFalse(record['result']['ok'])
        self.assertEqual(record['result']['phase'], 'rejected')
        self.assertEqual(record['result']['error']['code'], 'decision_disabled')
        self.assertNotIn(task_id, h.plugin.task_coordinator._lease_identities)
        self.assertEqual(h.owner.starts, [])
        self.assertEqual(h.texts, [])

    async def test_P02_real_proved_failure_replans_suffix_without_completed_start_replay(self):
        h = self.h
        h.observe('initial', 1)
        task_id = await h.create('repair', 3, 'PRIVATE_MARKER-P02')
        first = await h.started(task_id)
        first_goal = h.task(task_id)['current_goal']
        await h.complete(task_id, first, 0)
        second = await h.started(task_id, 1)
        failed_goal = h.task(task_id)['current_goal']
        old_calls = [json.loads(c['contexts'][0].content) for c in h.provider.calls]
        old_observation = next(o for o in old_calls[-1]['decision_context']['observations']
                               if o['source_id'] == 'joint_mock.status')
        h.observe('failed-step-1', 2)
        proof = StopEvidence(second.command_id, True, 'joint_mock', 'failed-parked-' + second.command_id)
        # Required SDK contract: status and positive proof commit together, never a test-injected goal fact.
        failed = await h.report(second, 'failed', reason_code='joint_step_failed', stop_evidence=proof)
        self.assertEqual(failed.status, 'failed')
        self.assertEqual(failed.held_resources, ())
        binding = OwnerBinding(failed.owner, failed.generation)
        committed = await asyncio.wrap_future(h.server.action_ledger.stop_proof(second.command_id, binding))
        self.assertEqual(committed, proof)
        await h.wait(lambda: any(f['goal_id'] == second.goal_id and f['status'] == 'failed'
            for f in h.server.decision_controller.journal.snapshot()['goal_summaries']), 'P02 real failed goal summary')
        summary = next(f for f in h.server.decision_controller.journal.snapshot()['goal_summaries']
                       if f['goal_id'] == second.goal_id and f['status'] == 'failed')
        self.assertEqual(Feedback.parse(summary).goal_revision, failed_goal['revision'])
        self.assertEqual(summary['task_id'], task_id)
        self.assertEqual(summary['ex_session'], h.task(task_id)['ex_session'])
        await h.wait(lambda: h.task(task_id)['plan_revision'] == 2, 'P02 suffix plan CAS')
        repair = await h.started(task_id, 1, plan_revision=2, after_goal_id=second.goal_id)
        task = h.task(task_id)
        self.assertEqual(task['steps'][0]['step_id'], first_goal['payload']['step_id'])
        self.assertEqual(task['steps'][0]['status'], 'completed')
        self.assertEqual([s['step_id'] for s in task['steps']], ['step-0', 'repair-1', 'repair-2'])
        calls = [json.loads(c['contexts'][0].content) for c in h.provider.calls]
        replanned = next(c for c in calls if c['task']['steps'] and c['task']['steps'][1]['status'] == 'failed')
        fresh = next(o for o in replanned['decision_context']['observations'] if o['source_id'] == 'joint_mock.status')
        self.assertNotEqual(fresh['observation_id'], old_observation['observation_id'])
        self.assertEqual(fresh['data']['stage'], 'failed-step-1')
        self.assertEqual(replanned['task']['steps'][0]['status'], 'completed')
        await h.complete(task_id, repair, 1)
        third = await h.started(task_id, 2)
        await h.complete(task_id, third, 2)
        await h.wait(lambda: not h.task(task_id)['active'], 'P02 replanned third step completed')
        commands = [first, second, repair, third]
        submits = [p for m, p in h.requests if m == 'decision.goal.submit']
        self.assertEqual([p['step_id'] for p in submits], ['step-0', 'step-1', 'repair-1', 'repair-2'])
        self.assertEqual([p['expected_revision'] for p in submits], [0, 1, 2, 3])
        self.assertEqual(len(h.owner.starts), 4)
        for command, payload in zip(commands, submits):
            row = await asyncio.wrap_future(h.server.action_ledger.get(command.command_id))
            events = await asyncio.wrap_future(h.server.action_ledger.events(after_seq=row.event_seq - 1, limit=1))
            event = events[0]
            self.assertEqual((row.command_id, event.command_id, event.event_seq),
                             (command.command_id, command.command_id, row.event_seq))
            self.assertEqual(event.task_id, task_id)
            self.assertEqual((event.ex_session, event.goal_id, event.goal_revision),
                             (command.ex_session, command.goal_id, command.goal_revision))
            self.assertEqual(event.status, row.status)
            self.assertEqual(row.status, 'failed' if command.command_id == second.command_id else 'succeeded')
            self.assertEqual(command.ex_session, payload['ex_session'])
            self.assertEqual(command.goal_id, payload['goal_id'])
            self.assertEqual(command.goal_revision, payload['expected_revision'] + 1)
            self.assertEqual(sum(c.command_id == command.command_id for c in h.owner.starts), 1)
        await asyncio.to_thread(h.owner.join_stops)
        self.assertEqual(h.owner.parked[second.command_id], proof)
        self.assertFalse(any(worker.is_alive() for worker in h.owner.stop_threads))
        self.assertEqual(sum(c.goal_id == first.goal_id for c in h.owner.starts), 1)
        self.assertEqual(h.task(task_id)['plan_revision'], 2)
        self.assertEqual(h.task(task_id)['status'], 'completed')
        self.assertEqual(h.texts, [])
        self.assertEqual(h.audio, [])
        print('JOINT_P02', json.dumps({'starts': 4, 'completed_prefix_starts': 1,
              'failed_summary_seq': summary['event_seq'], 'plan_revision': 2,
              'goal_revisions': [c.goal_revision for c in commands]}), flush=True)

    async def test_P07_real_ADMIN_update_waits_for_stop_and_revokes_late_emit(self):
        h = self.h
        h.observe('initial', 1)
        task_id = await h.create('update-held', 1, 'PRIVATE_MARKER-P07-old')
        await h.wait(h.provider.started.is_set, 'P07 old planning continuation held')
        old_task = h.task(task_id)
        old_turn = PlanningTurn(task_id, old_task['robot_id'], old_task['turn_id'],
                                old_task['generation'], old_task['route_ref'])
        goal = old_task['current_goal']
        await h.wait(lambda: any(c.goal_id == goal['payload']['goal_id'] for c in h.owner.starts), 'P07 old real start')
        command = next(c for c in h.owner.starts if c.goal_id == goal['payload']['goal_id'])
        await h.wait(lambda: h.server.action_ledger.get(command.command_id).result(1).status == 'accepted', 'P07 durable admission')
        h.owner.cancel_release.clear()
        replacement = json.dumps({'mode': 'replacement', 'steps': 1, 'marker': 'PRIVATE_MARKER-P07-new'})
        coordinator = h.plugin.task_coordinator
        original = coordinator.request
        replies = []
        async def capture(*args):
            if args[2] == 'decision.goal.cancel':
                self.assertGreater(h.task(task_id)['generation'], old_turn.generation)
                self.assertIsNone(h.task(task_id)['turn_id'])
            result = await original(*args)
            if args[2] == 'decision.goal.cancel':
                replies.append(result)
            return result
        coordinator.request = capture
        update = asyncio.create_task(dispatch_admin(h.plugin, 'ex_task_update', task_id + ' ' + replacement))
        try:
            await h.wait(lambda: bool(replies) and h.owner.cancel_entered.is_set(), 'P07 cancel accepted before proof')
            self.assertTrue(replies[0]['accepted'])
            self.assertFalse(replies[0]['stopped'])
            current = h.task(task_id)
            self.assertEqual(current['active_step'], 0)
            self.assertEqual(current['current_goal']['payload']['goal_id'], command.goal_id)
            self.assertFalse(current['needs_planning'])
            self.assertEqual(len(h.owner.starts), 1)
            self.assertFalse(any(f['goal_id'] == command.goal_id and f['status'] == 'canceled'
                for f in h.server.decision_controller.journal.snapshot()['goal_summaries']))
            tools = PlanningTools(coordinator, coordinator.router, old_turn,
                                  json.loads(h.provider.calls[0]['contexts'][0].content)['decision_context'])
            with self.assertRaisesRegex(TaskError, 'stale_generation'):
                await tools.execute('emit_user_message', {'text': 'OLD_COMPLETED_OUTPUT',
                                    'claim': 'completed', 'evidence_seq': 1}, 'stale-output')
            self.assertEqual(h.texts, [])
            self.assertEqual(h.audio, [])
            h.observe('replacement-target', 2)
        finally:
            h.owner.cancel_release.set()
        try:
            updated = await update
            self.assertEqual(updated.get_result().get_plain_text(), '', json.dumps(h.request_errors, default=str))
            await h.wait(lambda: any(f['goal_id'] == command.goal_id and f['status'] == 'canceled'
                for f in h.server.decision_controller.journal.snapshot()['goal_summaries']), 'P07 real canceled goal summary')
            summary = next(f for f in h.server.decision_controller.journal.snapshot()['goal_summaries']
                           if f['goal_id'] == command.goal_id and f['status'] == 'canceled')
            parsed = Feedback.parse(summary)
            self.assertEqual((parsed.task_id, parsed.goal_revision), (task_id, goal['revision']))
            self.assertEqual(summary['details']['stop_evidence']['stopped'], True)
            row = await asyncio.wrap_future(h.server.action_ledger.get(command.command_id))
            self.assertEqual(row.status, 'canceled')
            self.assertEqual(row.held_resources, ())
            proof = await asyncio.wrap_future(h.server.action_ledger.stop_proof(command.command_id, OwnerBinding(row.owner, row.generation)))
            self.assertEqual(proof, StopEvidence(command.command_id, True, 'joint_mock', 'parked-' + command.command_id))
            self.assertEqual(row.details['stop_evidence']['reference'], proof.reference)
        finally:
            h.provider.release.set()
        newer = await h.started(task_id)
        self.assertNotEqual(newer.goal_id, command.goal_id)
        self.assertEqual(newer.goal_revision, command.goal_revision + 1)
        self.assertEqual(h.task(task_id)['plan_revision'], 2)
        self.assertEqual(h.task(task_id)['steps'][0]['step_id'], 'replacement-0')
        await h.complete(task_id, newer, 0)
        await h.wait(lambda: not h.task(task_id)['active'], 'P07 replacement completed')
        submits = [p for m, p in h.requests if m == 'decision.goal.submit']
        self.assertEqual([p['step_id'] for p in submits], ['step-0', 'replacement-0'])
        self.assertEqual([p['expected_revision'] for p in submits], [0, 1])
        self.assertEqual(len(h.owner.starts), 2)
        for started, payload in zip((command, newer), submits):
            row = await asyncio.wrap_future(h.server.action_ledger.get(started.command_id))
            events = await asyncio.wrap_future(h.server.action_ledger.events(after_seq=row.event_seq - 1, limit=1))
            event = events[0]
            self.assertEqual((row.command_id, event.command_id, event.event_seq),
                             (started.command_id, started.command_id, row.event_seq))
            self.assertEqual(event.task_id, task_id)
            self.assertEqual((event.ex_session, event.goal_id, event.goal_revision),
                             (started.ex_session, started.goal_id, started.goal_revision))
            self.assertEqual(event.status, row.status)
            self.assertEqual(started.ex_session, payload['ex_session'])
            self.assertEqual(started.goal_id, payload['goal_id'])
            self.assertEqual(started.goal_revision, payload['expected_revision'] + 1)
            self.assertEqual(sum(c.command_id == started.command_id for c in h.owner.starts), 1)
        self.assertEqual(h.texts, [])
        self.assertEqual(h.audio, [])
        self.assertNotIn('OLD_COMPLETED_OUTPUT', str(h.texts) + str(h.audio))
        print('JOINT_P07', json.dumps({'starts': 2, 'old_generation': old_turn.generation,
              'generation': h.task(task_id)['generation'], 'canceled_summary_seq': summary['event_seq'],
              'cancel_accepted_before_stop': True, 'stale_public': 0}), flush=True)

    async def test_AEB_restart_and_fresh_EX_manual_review_no_replay(self):
        h = self.h
        task_id = await h.create('quiet', 1, 'PRIVATE_MARKER-restart')
        command = await h.started(task_id)
        old_session = h.task(task_id)['ex_session']
        await h.plugin.terminate()
        await h.close_server()
        h.server = h.new_server()
        h.attach_sinks()
        self.assertNotEqual(h.server.decision_service.goals.ex_session, old_session)
        await h.start()  # initialize recovers resume_review; runtime configured only in temporary fixture
        self.assertEqual(h.task(task_id)['status'], 'resume_review')
        await asyncio.sleep(0.15)
        self.assertEqual(h.owner.starts, [])
        self.assertFalse(h.task(task_id)['needs_planning'])
        reviewed = await dispatch_admin(h.plugin, 'ex_task_review', task_id)
        print('JOINT_RESTART_REVIEW', json.dumps({'entry_text': reviewed.get_result().get_plain_text(),
              'old_session': old_session, 'fresh_session': h.server.decision_service.goals.ex_session,
              'aeb_status': h.task(task_id)['status'], 'starts_after_restart': len(h.owner.starts)}), flush=True)
        # Required restoration must reconcile fresh session and real stop proof, not manual fake facts.
        self.assertEqual(reviewed.get_result().get_plain_text(), '', 'explicit review failed against fresh EX; report real recovery defect')
        self.assertEqual(h.task(task_id)['ex_session'], h.server.decision_service.goals.ex_session)
        self.assertEqual(len(h.owner.starts), 0, 'restart replayed physical start')
        recovered = h.task(task_id)
        retired = recovered['reviewed_goal']
        self.assertEqual(retired['payload']['goal_id'], command.goal_id)
        self.assertEqual(retired['payload']['ex_session'], old_session)
        self.assertEqual(retired['feedback_status'], 'unknown')
        self.assertTrue(retired['review_required'])
        self.assertEqual(recovered['status'], 'waiting_input')
        self.assertFalse(recovered['needs_planning'])
        self.assertEqual(recovered['reviewed_session'], {'task_id': task_id,
            'robot_id': recovered['robot_id'], 'ex_session': recovered['ex_session']})
        self.assertTrue(h.server.decision_controller.journal.snapshot()['previous_session_manual_review'])
        h.observe('explicit-new-target', 1)
        replacement = json.dumps({'mode': 'replacement', 'steps': 1, 'marker': 'PRIVATE_MARKER-restart-new'})
        updated = await dispatch_admin(h.plugin, 'ex_task_update', task_id + ' ' + replacement)
        self.assertEqual(updated.get_result().get_plain_text(), '')
        newer = await h.started(task_id)
        self.assertNotEqual(newer.goal_id, command.goal_id)
        self.assertEqual(newer.ex_session, h.server.decision_service.goals.ex_session)
        self.assertEqual(h.task(task_id)['plan_revision'], recovered['plan_revision'] + 1)
        self.assertEqual(h.task(task_id)['steps'][0]['step_id'], 'replacement-0')
        generation = h.task(task_id)['generation']
        await h.report(newer, 'running')
        await h.wait(lambda: h.task(task_id)['current_goal'].get('last_feedback', {}).get('status') == 'running',
                     'new session real journal running fact applied')
        await h.plugin.task_coordinator.sync(task_id)
        self.assertEqual(h.task(task_id)['status'], 'executing')
        self.assertEqual(h.task(task_id)['generation'], generation)
        self.assertEqual(h.task(task_id)['reviewed_goal'], retired)
        self.assertEqual(h.task(task_id)['active_step'], 0)
        await h.complete(task_id, newer, 0)
        await h.wait(lambda: not h.task(task_id)['active'], 'explicit new target completed after recovery')
        self.assertEqual(h.task(task_id)['status'], 'completed')
        self.assertEqual(h.task(task_id)['reviewed_goal'], retired)
        self.assertEqual(len(h.owner.starts), 1)
        self.assertFalse(any(c.goal_id == command.goal_id for c in h.owner.starts))
        submits = [p for m, p in h.requests if m == 'decision.goal.submit']
        self.assertEqual(len(submits), 2)
        self.assertEqual([p['ex_session'] for p in submits], [old_session, recovered['ex_session']])
        self.assertEqual([p['step_id'] for p in submits], ['step-0', 'replacement-0'])
        self.assertEqual(h.texts, [])
        self.assertEqual(h.audio, [])
        print('JOINT_RESTART_CONTINUED', json.dumps({'new_starts': 1, 'old_replays': 0,
              'manual_review_flag_remains': True, 'plan_revision': h.task(task_id)['plan_revision']}), flush=True)
