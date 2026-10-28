#!/usr/bin/env python3
"""Print evidence for the October 27 audit; no paid calls or personal state.

Uses temporary files, scripted model replies, a loopback HTTP fixture, and
lightweight widget doubles. This is a diagnostic, not part of the passing suite.
It prints observed behavior so the same cases can be checked after repairs.
"""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from desktop_buddy.agent import Agent, compact, estimate_tokens
from desktop_buddy.model_ui import ConnectionDialog
from desktop_buddy.providers import Provider
from desktop_buddy.security import Policy, Redactor, StopToken
from desktop_buddy.state import ProviderConfig, Store
from desktop_buddy.tools import Registry


class ScriptedProvider:
    def __init__(self, script):
        self.config = ProviderConfig('lmstudio', 'http://127.0.0.1:1234/v1',
                                     'chat', True, model='fixture', vision=True)
        self.script = iter(script)
        self.seen = []

    def check(self, *_): pass
    def verify_local_model(self, *_): pass

    def complete(self, messages, *_):
        self.seen.append(copy.deepcopy(messages))
        return next(self.script), {}


def call(name, arguments, ident):
    return {'id': ident, 'name': 'run_tool',
            'arguments': {'name': name, 'arguments': arguments}}


def answer(*calls):
    return {'role': 'assistant', 'content': '', 'tool_calls': list(calls)}


def final():
    return {'role': 'assistant', 'content': 'done', 'tool_calls': []}


def agent_evidence(store, registry):
    evidence = {}
    provider = ScriptedProvider([
        answer(call('fs.image', {'path': 'image.png'}, '1'), call('fs.list', {}, '2')),
        final(),
    ])
    cid = store.conversation()
    Agent(store, registry, provider).run(cid, 'Inspect image and list files')
    evidence['batched_image'] = {
        'expected_image_count': 1,
        'next_request_image_count': sum(len(m.get('images', [])) for m in provider.seen[1]),
        'saved_image_count': sum(len(m.get('images', [])) for m in store.messages(cid)),
    }

    provider = ScriptedProvider([
        answer(call('fs.image', {'path': 'image.png'}, str(i))) for i in range(8)
    ] + [final()])
    cid = store.conversation()
    agent = Agent(store, registry, provider)
    agent.run(cid, 'Inspect this small image eight times')
    budget = provider.config.context_tokens - provider.config.max_output - 4000
    retained, _ = compact([{'role': 'system', 'content': 's'}] + store.messages(cid), budget)
    evidence['historical_image_budget'] = {
        'expected_status': 'complete',
        'actual_status': store.tasks(cid)[0]['status'],
        'inference_requests': len(provider.seen),
        'errors': [json.loads(r['body']) for r in store.audits(agent.task_id)
                   if r['event'] == 'task_error'],
        'images_after_compaction': sum(len(m.get('images', [])) for m in retained),
        'estimated_total_tokens_after_compaction_with_short_system': estimate_tokens(retained),
    }

    cid = store.conversation()
    agent = Agent(store, registry, ScriptedProvider([final()]))
    entered, release = threading.Event(), threading.Event()
    original = store.checkpoint

    def delayed_checkpoint(tid, conversation, status, data):
        if status == 'complete':
            entered.set()
            if not release.wait(5):
                raise TimeoutError('Diagnostic did not release checkpoint')
        return original(tid, conversation, status, data)

    store.checkpoint = delayed_checkpoint
    worker = threading.Thread(target=agent.run, args=(cid, 'First request'))
    worker.start()
    try:
        if not entered.wait(5):
            raise TimeoutError('Task did not reach final checkpoint')
        assert worker.is_alive()
        agent.say('Message accepted during the final checkpoint')
    finally:
        release.set()
        worker.join(5)
        store.checkpoint = original
    assert not worker.is_alive()
    evidence['late_message'] = {
        'expected_persisted': True,
        'actual_persisted': any(m['content'] == 'Message accepted during the final checkpoint'
                                for m in store.messages(cid)),
        'stranded_inbox_messages': len(agent.inbox),
    }
    return evidence


def thinking_evidence(store):
    config = store.configs()['anthropic']
    fields = {name: Mock(get_text=lambda name=name: str(getattr(config, name)))
              for name in ('base_url', 'context_tokens', 'max_output', 'input_cost',
                           'output_cost', 'api_version', 'workspace_id')}
    fake = SimpleNamespace(
        app=SimpleNamespace(store=store), selected_id=lambda: 'anthropic',
        model=Mock(get_text=lambda: 'fixture-claude'), fields=fields,
        protocol=Mock(get_selected=lambda: 2), tools=Mock(get_active=lambda: True),
        vision=Mock(get_active=lambda: True),
    )
    results = {}
    for level in ('1024', '4096', '8192', '16384', '32768'):
        fake.thinking = SimpleNamespace(values=[level], get_selected=lambda: 0)
        try:
            saved = ConnectionDialog.config(fake)
            results[level] = {'accepted': True, 'max_output': saved.max_output}
        except ValueError as error:
            results[level] = {'accepted': False, 'error': str(error)}
    return results


def offline_probe_evidence():
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            paths.append(self.path)
            if self.path == '/api/show':
                payload = {'remote_host': 'https://example.invalid'}
            else:
                payload = {'message': {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'function': {'name': 'connection_probe', 'arguments': {'value': 'buddy-test'}}}
                ]}, 'done': True}
            data = (json.dumps(payload) + '\n').encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = ProviderConfig('ollama', f'http://127.0.0.1:{server.server_port}',
                                'ollama', True, model='fixture-cloud')
        status = Mock()
        fake = SimpleNamespace(
            config=lambda: config, key=Mock(get_text=lambda: ''),
            privacy=Mock(get_active=lambda: True), status=status, tools=Mock(),
            ready=lambda: None, app=SimpleNamespace(
                credentials=Mock(get=lambda _: ''), redactor=Redactor()),
        )
        fake.run = lambda work, done: done(work(StopToken()))
        probe_error = None
        try:
            ConnectionDialog.verify(fake)
        except Exception as error:
            probe_error = str(error)
        normal_check = None
        try:
            Provider(config).verify_local_model(StopToken())
        except Exception as error:
            normal_check = str(error)
        return {'expected_inference_requests': 0, 'actual_request_paths': paths,
                'probe_error': probe_error, 'dialog_status': status.set_text.call_args.args[0],
                'normal_task_local_check_error': normal_check}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def main():
    with tempfile.TemporaryDirectory(prefix='buddy-audit-') as folder:
        root = Path(folder)
        workspace = root / 'workspace'
        workspace.mkdir()
        Image.new('RGB', (2, 2)).save(workspace / 'image.png')
        store = Store(root / 'state', Redactor())
        try:
            registry = Registry(Policy(workspace, 'execute', True), store)
            evidence = agent_evidence(store, registry)
            evidence['setup_thinking'] = thinking_evidence(store)
            evidence['offline_connection_probe'] = offline_probe_evidence()
            print(json.dumps(evidence, indent=2))
        finally:
            store.close()


if __name__ == '__main__':
    main()
