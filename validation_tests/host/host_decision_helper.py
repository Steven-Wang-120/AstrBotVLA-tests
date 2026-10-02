"""Offline real composition fixture; EX checkout must be supplied explicitly."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

checkout = os.environ.get("ASTRBOTEX_TEST_CHECKOUT")
if not checkout:
    raise RuntimeError("ASTRBOTEX_TEST_CHECKOUT is required for real Host/EX integration; no implicit sibling or skip")
EX_ROOT = Path(checkout).resolve()
if not (EX_ROOT / "astrbot_ex/core/api_server.py").is_file():
    raise RuntimeError("ASTRBOTEX_TEST_CHECKOUT must name a complete EX checkout")
sys.path.insert(0, str(EX_ROOT))

from astrbot.api.star import Context
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.provider import Provider
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.permission import PermissionTypeFilter, PermissionType
from astrbot_ex.core.api_server import build_server
from astrbot_ex.core.decision.backends import MockBackend
from astrbot_ex.core.plugin_actor import PluginActor
from astrbot_ex.core.actions.ledger import OwnerBinding, StopEvidence
from astrbot_plugin_astrbotex_interaction import main as aeb


MOCK_SOURCE = '''
import threading
import time
from astrbot_ex.core.actions.ledger import StopEvidence
class Plugin:
    def __init__(self, context):
        self.context = context
        self.starts = []
        self.cancels = []
        self.stop_proof = True
        self.cancel_entered = threading.Event()
        self.cancel_release = threading.Event()
        self.cancel_release.set()
        self.stop_threads = []
        self.stop_errors = []
        self.parked = {}
        self.stop_pending = set()
        self.stop_lock = threading.Lock()
    def device_stopped(self, evidence):
        if evidence.stopped is not True:
            raise ValueError('device is not stopped')
        with self.stop_lock:
            old = self.parked.get(evidence.command_id)
            if old is not None and old != evidence:
                raise ValueError('mock device stop identity changed')
            self.parked[evidence.command_id] = evidence
    def on_action_command(self, command):
        self.starts.append(command)
        return 'accepted'
    def on_action_cancel(self, command_id, reason):
        self.cancels.append(command_id)
        self.cancel_entered.set()
        if self.stop_proof:
            with self.stop_lock:
                if command_id in self.parked or command_id in self.stop_pending:
                    return 'requested'
                self.stop_pending.add(command_id)
            def stopped():
                try:
                    if not self.cancel_release.wait(2):
                        raise TimeoutError('test cancel proof was not released')
                    evidence = StopEvidence(command_id, True, 'joint_mock', 'parked-' + command_id)
                    self.device_stopped(evidence)
                    self.context.actions.report(command_id, 'canceled', stop_evidence=evidence).result(2)
                except Exception as exc:
                    self.stop_errors.append(exc)
            worker = threading.Thread(target=stopped, name='joint-device-stop-' + command_id)
            self.stop_threads.append(worker)
            worker.start()
        return 'requested'
    def join_stops(self):
        self.cancel_release.set()
        deadline = time.monotonic() + 3
        for worker in self.stop_threads:
            worker.join(max(0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in self.stop_threads):
            raise TimeoutError('joint device stop worker leaked')
        if self.stop_errors:
            raise RuntimeError('joint device stop report failed: ' + repr(self.stop_errors))
'''


def install_mock(root):
    target = root / 'plugins/control/joint_mock'
    target.mkdir(parents=True)
    (target / 'main.py').write_text(MOCK_SOURCE, encoding='utf-8')
    (target / 'guide.md').write_text('Offline mock only; bounded integer units, stop must be proven.', encoding='utf-8')
    (target / 'plugin.json').write_text(json.dumps({
        'id': 'joint_mock', 'name': 'Joint offline mock', 'version': '1.0.0',
        'entry': 'main.py', 'provides': ['action_owner'], 'enabled_default': True,
        'action_api_version': 2, 'observation_guide': 'guide.md',
        'observation_sources': {'joint_mock.status': {'topic': 'joint_mock.status',
            'max_age_ms': 60000, 'required_fields': ['stage']}},
        'actions': [{'action_id': 'joint_mock.step.v1', 'description': 'Perform one offline bounded step',
            'schema': {'type': 'object', 'properties': {'units': {'type': 'integer', 'minimum': 1, 'maximum': 3}},
                       'required': ['units'], 'additionalProperties': False},
            'resources': ['joint_mock_resource'], 'operations': ['start', 'cancel'],
            'cancel_timeout_ms': 500, 'max_duration_ms': 60000}]}), encoding='utf-8')


def hashes():
    paths = ['astrbot_ex/core/api_server.py', 'astrbot_ex/core/connection_manager.py',
             'astrbot_ex/core/interaction_core.py', 'astrbot_ex/core/plugin_actor.py',
             'astrbot_ex/core/local_plugins.py', 'astrbot_ex/core/actions/dispatcher.py',
             'astrbot_ex/core/actions/ledger.py', 'astrbot_ex/core/actions/plugin_api.py',
             'astrbot_ex/core/decision/service.py', 'astrbot_ex/core/decision/goal_manager.py',
             'astrbot_ex/core/decision/controller.py', 'astrbot_ex/core/decision/feedback_journal.py',
             'astrbot_ex/core/decision/public_delivery.py']
    return {name: hashlib.sha256((EX_ROOT / name).read_bytes()).hexdigest() for name in paths}


class OfflineProvider(Provider):
    def get_current_key(self):
        return 'offline-no-credentials'
    def set_key(self, key):
        raise RuntimeError('offline provider does not accept credentials')
    async def get_models(self):
        return ['offline-joint']
    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.latched:
            self.started.set()
            await self.release.wait()
        task = json.loads(kwargs['contexts'][0].content)['task']
        spec = json.loads(task['text'])
        mode, marker = spec['mode'], spec['marker']
        if mode == 'update-held' and len(kwargs['contexts']) > 1:
            self.started.set()
            await self.release.wait()
            return LLMResponse(role='assistant', completion_text=marker + ' stale private result',
                tools_call_name=['emit_user_message', 'finish_planning_turn'],
                tools_call_args=[{'text': 'OLD_COMPLETED_OUTPUT', 'claim': 'completed', 'evidence_seq': 1},
                                 {'outcome': 'waiting_input'}], tools_call_ids=['late-emit', 'late-finish'])
        names, args = [], []
        if mode == 'question':
            names = ['emit_user_message', 'finish_planning_turn']
            args = [{'text': 'Which offline object?', 'claim': 'question'}, {'outcome': 'waiting_input'}]
        elif task['active_step'] >= spec['steps']:
            names, args = ['finish_planning_turn'], [{'outcome': 'completed'}]
        else:
            index = task['active_step']
            if not task['steps']:
                names.append('save_plan')
                args.append({'steps': [{'step_id': f'step-{i}', 'intent': marker + f' intent {i}',
                              'completion_condition': marker + ' observed success'} for i in range(spec['steps'])],
                             'expected_revision': task['plan_revision']})
            # Consume the actual received catalog schema/guide; never substitute FakeDecision.
            context = json.loads(kwargs['contexts'][0].content)['decision_context']
            completed = [s for s in task['steps'] if s['status'] == 'completed']
            replacing = bool(task['steps'] and (mode == 'replacement' or
                (mode == 'repair' and task['steps'][index]['status'] == 'failed')))
            if replacing:
                assert len(completed) == index and task['current_goal'] is None
                observation = next(o for o in context['observations'] if o['source_id'] == 'joint_mock.status')
                assert observation['health']['status'] == 'ok'
                if mode == 'repair':
                    assert observation['data']['stage'] == 'failed-step-1'
                names.append('save_plan')
                args.append({'steps': [{'step_id': f'{mode}-{i}',
                    'intent': marker + f' fresh intent {i} observation ' + observation['observation_id'],
                    'completion_condition': marker + ' fresh observed success'}
                    for i in range(index, spec['steps'])], 'expected_revision': task['plan_revision']})
            step_id = f'{mode}-{index}' if replacing else (task['steps'][index]['step_id'] if task['steps'] else f'step-{index}')
            action = next(a for a in context['actions'] if a['action_id'] == 'joint_mock.step.v1')
            assert action['schema']['required'] == ['units']
            assert any('Offline mock only' in g['text'] for g in context['guides'])
            names.append('submit_current_goal')
            args.append({'step_id': step_id, 'goal_text_en': 'Perform one bounded offline mock step.',
                         'allowed_actions': [action['action_id']], 'parameters': {action['action_id']: {'units': index + 1}},
                         'completion': {'required_success_actions': [action['action_id']]}, 'lease_ms': 10000})
            if mode == 'emit' and index == 0:
                names.append('emit_user_message')
                args.append({'text': 'Trying the offline step.'})
            if mode != 'update-held':
                names.append('finish_planning_turn')
                args.append({'outcome': 'waiting_feedback'})
        return LLMResponse(role='assistant', completion_text=marker + ' private raw text/tool notes',
                           tools_call_name=names, tools_call_args=args,
                           tools_call_ids=[f'call-{i}' for i in range(len(names))])


def make_host():
    provider = object.__new__(OfflineProvider)
    provider.calls = []
    provider.latched = False
    provider.started, provider.release = asyncio.Event(), asyncio.Event()
    class Manager:
        async def get_provider_by_id(self, provider_id):
            assert provider_id == 'offline-joint'
            return provider
    host = object.__new__(Context)
    host.provider_manager = Manager()
    host.platform_manager = SimpleNamespace(platform_insts=[])
    # Plugin registration has no running Host message manager; only this sink is offline.
    # llm_generate itself and all ToolSet/Message/permission APIs are real Host classes.
    host.add_llm_tools = lambda *tools: None
    return host, provider


def admin_event(text, *, session='joint-session', user='joint-admin', admin=True):
    message = aeb.AstrBotMessage()
    message.type = aeb.MessageType.FRIEND_MESSAGE
    message.session_id = session
    message.sender = aeb.MessageMember(user_id=user, nickname='Offline admin')
    message.message_id = hashlib.sha256((text + session + user + str(time.monotonic_ns())).encode()).hexdigest()
    message.message_str = text
    message.message = [aeb.Plain(text=text)]
    event = aeb.AstrMessageEvent(text, message, aeb.PlatformMetadata(name='offline', id='offline-joint', description='offline'), session)
    event.role = 'admin' if admin else 'member'
    event.is_at_or_wake_command = True
    return event


async def dispatch_admin(plugin, command, tail='', **identity):
    event = admin_event(command + (' ' + tail if tail else ''), **identity)
    md = star_handlers_registry.get_handler_by_full_name(aeb.__name__ + '_' + command)
    if md is None:
        raise AssertionError('Real Host command metadata missing: ' + command)
    permission = next(f for f in md.event_filters if isinstance(f, PermissionTypeFilter))
    assert permission.permission_type == PermissionType.ADMIN
    if not permission.filter(event, {}):
        return event
    command_filter = next(f for f in md.event_filters if isinstance(f, CommandFilter))
    assert command_filter.filter(event, {})
    await getattr(plugin, command)(event, **event.get_extra('parsed_params'))
    return event


class JointHarness:
    def __init__(self, root):
        self.root = root
        self.before = hashes()
        install_mock(root / 'ex')
        self.server = self.new_server()
        self.host, self.provider = make_host()
        self.plugin = aeb.AstrBotEXInteractionPlugin(self.host)
        self._request_text = self.plugin.request_text
        self.plugin.zmq_bind_host = '127.0.0.1'
        self.plugin.text_port = self.plugin.audio_port = self.plugin.vision_port = 0
        self.plugin.task_planning_enabled = True
        self.plugin.task_provider_id = 'offline-joint'
        self.plugin.task_robot_id = 'joint-robot'
        self.plugin.task_peer_id = 'joint-trusted'
        self.texts, self.audio, self.requests = [], [], []
        self.request_errors = []
        self.attach_sinks()

    def new_server(self):
        with patch.dict(os.environ, {'ASTRBOTEX_DATA_DIR': str(self.root / 'ex'),
                                     'ASTRBOTEX_STT_ENABLED': '', 'ASTRBOTEX_TTS_ENABLED': ''}):
            server = build_server('127.0.0.1', 0, 20)
        return server

    def attach_sinks(self):
        bus = self.server.interaction_core.topic_bus
        bus.subscribe('interaction_core.message.outgoing', lambda m: self.texts.append(m.payload))
        bus.subscribe('interaction_core.audio.play', lambda m: self.audio.append(m.payload))

    @property
    def owner(self):
        return self.server.local_plugins.records['joint_mock'].plugin

    @property
    def record(self):
        return self.server.local_plugins.records['joint_mock']

    async def start(self):
        with patch.dict(os.environ, {'ASTRBOTEX_TASK_DB_PATH': str(self.root / 'aeb.sqlite3')}):
            await self.plugin.initialize()
        self.plugin.task_coordinator.heartbeat_interval = 0.1
        self.connect_ex()
        await self.wait(lambda: self.plugin.text_channel.online_peers(), 'real EX hello')
        await asyncio.to_thread(self.server.controller.change_mode, 'decision')
        self.server.decision_service.backend = MockBackend(kind='start')
        self.server.decision_service.set_mode('execute')
        await asyncio.to_thread(self.server.controller.start)
        await self.wait(lambda: self.server.controller.runtime.state.value == 'running'
                        and self.server.decision_service.goals.phase == 'idle', 'real runtime execute ready')
        slot = self.server.controller.runtime.registry.get('joint_mock')
        assert isinstance(slot.actor, PluginActor)
        original = self._request_text
        async def trace(method, payload, **kwargs):
            self.requests.append((method, json.loads(json.dumps(payload))))
            try:
                result = await original(method, payload, **kwargs)
            except Exception as exc:
                error = {'method': method, 'payload': payload, 'type': type(exc).__name__, 'error': str(exc)}
                self.request_errors.append(error)
                print('JOINT_REQUEST_ERROR', json.dumps(error, default=str), flush=True)
                raise
            if isinstance(result, dict) and result.get('ok') is False:
                error = {'method': method, 'payload': payload, 'response': result}
                self.request_errors.append(error)
                print('JOINT_REQUEST_REJECTION', json.dumps(error, default=str), flush=True)
            return result
        self.plugin.request_text = trace

    def connect_ex(self):
        import zmq
        endpoint = self.plugin.text_channel.socket.getsockopt(zmq.LAST_ENDPOINT).decode()
        manager = self.server.connections
        if 'joint-trusted' in manager._records:
            manager.update('joint-trusted', {'config': {'protocol_profile': 'astrbotex', 'channel': 'text',
                           'endpoint': endpoint, 'identity': 'joint-trusted'}, 'enabled': True})
        else:
            manager.create({'id': 'joint-trusted', 'name': 'Temporary offline joint', 'type': 'zmq_client', 'enabled': True,
                            'config': {'protocol_profile': 'astrbotex', 'channel': 'text',
                                       'endpoint': endpoint, 'identity': 'joint-trusted'}})

    async def wait(self, predicate, label, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            await asyncio.sleep(0.01)
        state = {'label': label, 'goals': self.server.decision_service.goals.status(),
                 'aeb': self.plugin.task_store.active_tasks() if self.plugin.task_store else [],
                 'starts': len(self.owner.starts), 'journal': self.server.decision_controller.journal.snapshot(),
                 'request_errors': self.request_errors}
        raise AssertionError('Joint timeout: ' + json.dumps(state, ensure_ascii=False, default=str))

    async def create(self, mode='quiet', steps=1, marker='PRIVATE_MARKER'):
        spec = json.dumps({'mode': mode, 'steps': steps, 'marker': marker})
        event = await dispatch_admin(self.plugin, 'ex_task', spec)
        assert event.get_result().get_plain_text() == '', event.get_result().get_plain_text()
        assert event.is_stopped() and not event.call_llm
        tasks = self.plugin.task_store.active_tasks()
        assert len(tasks) == 1
        return tasks[0]['task_id']

    def task(self, task_id):
        return self.plugin.task_store.get(task_id)

    async def started(self, task_id, index=0, *, plan_revision=None, after_goal_id=None):
        command = None
        def accepted_current():
            nonlocal command
            task = self.task(task_id)
            goal = task['current_goal']
            if (task['active_step'] != index or task['turn_id'] is not None or not goal
                    or goal['revision'] is None
                    or (plan_revision is not None and task['plan_revision'] != plan_revision)
                    or goal['payload']['goal_id'] == after_goal_id
                    or goal.get('feedback_status') in {'succeeded', 'failed', 'rejected', 'canceled', 'timed_out', 'unknown'}):
                return False
            matches = [c for c in self.owner.starts if c.ex_session == task['ex_session']
                       and c.goal_id == goal['payload']['goal_id'] and c.goal_revision == goal['revision']]
            for candidate in matches:
                row = self.server.action_ledger.get(candidate.command_id).result(1)
                if row is None or row.status != 'accepted' or row.command_id != candidate.command_id:
                    continue
                events = self.server.action_ledger.events(after_seq=row.event_seq - 1, limit=1).result(1)
                if not events:
                    continue
                event = events[0]
                current = self.task(task_id)
                if (event.event_seq == row.event_seq and event.command_id == candidate.command_id
                        and event.task_id == task_id and event.ex_session == candidate.ex_session
                        and event.goal_id == candidate.goal_id and event.goal_revision == candidate.goal_revision
                        and event.status == 'accepted' and current['current_goal'] == goal
                        and current['active_step'] == index and current['turn_id'] is None
                        and current['ex_session'] == task['ex_session']
                        and current['plan_revision'] == task['plan_revision']):
                    command = candidate
                    return True
            return False
        await self.wait(accepted_current, 'current step goal and real ledger accepted')
        return command

    async def report(self, command, status, **kwargs):
        evidence = kwargs.get('stop_evidence')
        if status == 'failed' and evidence is not None:
            assert evidence.command_id == command.command_id
            self.owner.device_stopped(evidence)
        return await asyncio.wrap_future(self.owner.context.actions.report(command.command_id, status, **kwargs))

    async def complete(self, task_id, command, index):
        assert self.task(task_id)['active_step'] == index
        await self.report(command, 'running')
        await asyncio.sleep(0.08)
        assert self.task(task_id)['active_step'] == index, 'running advanced a step'
        assert self.task(task_id)['current_goal'] is not None
        await self.report(command, 'succeeded')
        await self.wait(lambda: self.task(task_id)['active_step'] == index + 1, 'EX verified summary advances AEB')

    def observe(self, stage, seq):
        self.server.interaction_core.topic_bus.publish_payload('joint_mock.status',
            timestamp=time.time(), source='joint_mock', seq=seq,
            payload={'stage': stage, 'source_epoch': 'joint-observations'})

    async def close_server(self):
        owner = self.owner
        registry = self.server.controller.runtime.registry
        actors = tuple(slot.actor for slot in registry.list())
        owner.cancel_release.set()
        await asyncio.to_thread(owner.join_stops)
        try:
            # The mock must provide its actual device proof before unregister.
            await asyncio.to_thread(self.server.controller.stop, 'joint fixture shutdown')
            await asyncio.to_thread(owner.join_stops)
            assert await asyncio.to_thread(self.server.action_service.await_stop_proof, 'joint fixture shutdown')
            for slot in registry.list():
                await asyncio.to_thread(registry.unregister, slot.id)
            assert not any(actor.alive for actor in actors), 'joint fixture Actor did not join'
            await asyncio.to_thread(self.server.server_close)
        finally:
            await asyncio.to_thread(owner.join_stops)

    async def close(self):
        self.provider.release.set()
        self.owner.cancel_release.set()
        if self.plugin.task_coordinator:
            await self.plugin.terminate()
        self.owner.stop_proof = True
        await self.close_server()
        print('EX_SOURCE_HASHES', json.dumps({'before': self.before, 'after': hashes(),
              'changed_during_test': self.before != hashes()}, sort_keys=True), flush=True)
