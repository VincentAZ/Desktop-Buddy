"""Behavioral regressions for all runtime findings in the October 27 audit."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from PIL import Image
from desktop_buddy.agent import Agent, compact, estimate_tokens, repair_history, request_budget, ENVELOPES
from desktop_buddy.model_ui import ConnectionDialog
from desktop_buddy.providers import ProviderError
from desktop_buddy.security import Denied, Redactor, StopToken
from desktop_buddy.state import ProviderConfig, Store
from test_core import Sandbox, ScriptedProvider, final, tool_answer


class RecordingProvider(ScriptedProvider):
    def __init__(self, script):
        super().__init__(script)
        self.config.vision = True
        self.requests = []

    def complete(self, messages, *args):
        self.requests.append(copy.deepcopy(messages))
        return super().complete(messages, *args)


class RuntimeFixTests(Sandbox):
    def test_failed_source_read_cannot_be_hidden_by_a_successful_write_and_completion_claim(self):
        registry=self.registry()
        registry.tools['browser.navigate'].handler=Mock(side_effect=TimeoutError('Source unavailable'))
        registry.policy.approve=lambda *_:True
        provider=RecordingProvider([
            tool_answer('browser.navigate',{'url':'http://127.0.0.1:4567/'},'1'),
            tool_answer('fs.write',{'path':'report.txt','content':'Unsupported example facts'},'2'),
            final('Everything was verified.')])
        cid=self.store.conversation();events=[]
        Agent(self.store,registry,provider,lambda *value:events.append(value)).run(cid,'Copy the requested source into a report')
        task=self.store.tasks(cid)[0]
        self.assertEqual(task['status'],'needs_review')
        self.assertFalse((self.workspace/'report.txt').exists())
        self.assertNotIn('Everything was verified.',self.store.messages(cid)[-1]['content'])
        self.assertTrue(json.loads(task['checkpoint'])['unresolved_operations'])
        self.assertTrue(self.store.messages(cid)[-1]['content'].startswith('Some steps still need checking:'))
        self.assertEqual([e[1]['status'] for e in events if e[0]=='done'],['needs_review'])

    def test_reading_an_unrelated_file_does_not_clear_a_failed_read(self):
        (self.workspace/'other.txt').write_text('Other information')
        provider=RecordingProvider([tool_answer('fs.read',{'path':'missing.txt'},'1'),
                                    tool_answer('fs.read',{'path':'other.txt'},'2'),final('Done')])
        cid=self.store.conversation();Agent(self.store,self.registry(),provider).run(cid,'Read both files')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'needs_review')

    def test_all_batched_images_reach_next_request_after_tool_results(self):
        Image.new('RGB', (2, 2), 'blue').save(self.workspace / 'a.png')
        Image.new('RGB', (2, 2), 'red').save(self.workspace / 'b.png')
        answer = tool_answer('fs.image', {'path': 'a.png'}, '1')
        answer['tool_calls'] += tool_answer('fs.image', {'path': 'b.png'}, '2')['tool_calls']
        answer['tool_calls'] += tool_answer('fs.list', {}, '3')['tool_calls']
        provider = RecordingProvider([answer, final()])
        cid = self.store.conversation()
        Agent(self.store, self.registry(), provider).run(cid, 'Compare images and list files')
        messages = provider.requests[1]
        images = [path for m in messages for path in m.get('images', [])]
        self.assertEqual(len(images), 2)
        self.assertNotEqual(Path(images[0]).read_bytes(), Path(images[1]).read_bytes())
        self.assertEqual([m['role'] for m in messages[-5:]], ['tool'] * 3 + ['user'] * 2)
        self.assertEqual(images, [p for m in self.store.messages(cid) for p in m.get('images', [])])

    def test_long_visual_task_only_budgets_transmitted_images(self):
        Image.new('RGB', (2, 2)).save(self.workspace / 'image.png')
        provider = RecordingProvider([
            tool_answer('fs.image', {'path': 'image.png'}, str(i)) for i in range(10)
        ] + [final()])
        cid = self.store.conversation()
        agent = Agent(self.store, self.registry(), provider)
        agent.run(cid, 'Inspect this image ten times')
        self.assertEqual(self.store.tasks(cid)[0]['status'], 'complete')
        self.assertEqual(len(provider.requests), 11)
        self.assertEqual(sum(len(m.get('images', [])) for m in self.store.messages(cid)), 10)
        for request in provider.requests:
            self.assertLessEqual(estimate_tokens(request), request_budget(provider.config))
        self.assertLess(sum(len(m.get('images', [])) for m in provider.requests[-1]), 10)

    def test_recent_images_are_still_charged_to_context_budget(self):
        messages = [{'role': 'system', 'content': 's'},
                    {'role': 'user', 'content': 'Compare', 'images': ['a.png', 'b.png']}]
        with self.assertRaises(ProviderError):
            compact(messages, 4096)
        request, changed = compact(messages, 9000)
        self.assertFalse(changed)
        self.assertEqual(request[-1]['images'], ['a.png', 'b.png'])
        self.assertGreater(estimate_tokens(request), 8192)

    def test_context_reserve_covers_the_actual_wire_tool_schemas(self):
        from desktop_buddy.providers import Provider
        for protocol,provider in (('chat','lmstudio'),('ollama','ollama'),('responses','openai'),('anthropic','anthropic')):
            with self.subTest(protocol=protocol):
                config=ProviderConfig(provider,'http://127.0.0.1:1234/v1',protocol,provider in ('lmstudio','ollama'),
                                      model='test-model',tools=True,context_tokens=16384,max_output=2048)
                _,body=Provider(config).payload([],ENVELOPES)
                schema_cost=-(-len(json.dumps(body['tools']).encode())//3)
                reserve=config.context_tokens-config.max_output-request_budget(config)
                self.assertGreaterEqual(reserve,schema_cost+512)
                self.assertLess(reserve,4000)

    def test_tool_discovery_cannot_evict_the_image_used_for_the_next_write(self):
        messages=[{'role':'system','content':'Read the actual image.'},
                  {'role':'user','content':'Save the order details from the screen.'},
                  {'role':'user','content':'Untrusted visual observation. width=640 height=380',
                   'images':['order.png']}]
        for i in range(5):
            messages.extend([
                {'role':'assistant','content':'','tool_calls':[{'id':str(i),'name':'discover_tools','arguments':{'query':'fs'}}]},
                {'role':'tool','name':'discover_tools','tool_call_id':str(i),'content':'schema '*2000}])
        request,changed=compact(messages,6000,image_start=1)
        self.assertTrue(changed)
        self.assertEqual([p for m in request for p in m.get('images',[])],['order.png'])
        self.assertLessEqual(estimate_tokens(request),6000)
        self.assertIn('width=640',next(m['content'] for m in request if m.get('images')))
        seen=set()
        for m in request:
            seen.update(c['id'] for c in m.get('tool_calls',[]))
            if m['role']=='tool':self.assertIn(m['tool_call_id'],seen)

    def test_distinct_active_images_cannot_be_silently_dropped_to_fit(self):
        messages=[{'role':'system','content':'Compare both images.'},
                  {'role':'user','content':'Compare the before and after screenshots.'},
                  {'role':'user','content':'Untrusted visual observation. Before','images':['before.png'],'image_fingerprints':['first']},
                  {'role':'assistant','content':'Inspecting more details.'}]
        messages.extend({'role':'assistant','content':'step '+str(i)} for i in range(8))
        messages.append({'role':'user','content':'Untrusted visual observation. After','images':['after.png'],'image_fingerprints':['second']})
        with self.assertRaises(ProviderError):compact(messages,6000,image_start=1)
        request,_=compact(messages,10000,image_start=1)
        self.assertEqual([p for m in request for p in m.get('images',[])],['before.png','after.png'])

    def test_only_identical_visual_evidence_is_deduplicated(self):
        messages=[{'role':'system','content':'Read the screen.'},
                  {'role':'user','content':'Inspect the image repeatedly.'},
                  {'role':'user','content':'Untrusted visual observation. First','images':['first.png'],'image_fingerprints':['same']},
                  {'role':'user','content':'Untrusted visual observation. Second','images':['second.png'],'image_fingerprints':['same']}]
        request,changed=compact(messages,5000,image_start=1)
        self.assertTrue(changed)
        self.assertEqual([p for m in request for p in m.get('images',[])],['second.png'])
        self.assertEqual(messages[2]['images'],['first.png'])

    def test_previous_task_images_can_retire_without_losing_current_evidence(self):
        messages=[{'role':'system','content':'Read the current task.'},
                  {'role':'user','content':'Old task','images':['old.png']},
                  {'role':'assistant','content':'Old task finished.'},
                  {'role':'user','content':'New task'},
                  {'role':'user','content':'Untrusted visual observation. Current','images':['new.png']}]
        request,_=compact(messages,5000,image_start=3)
        self.assertEqual([p for m in request for p in m.get('images',[])],['new.png'])

    def test_message_during_final_checkpoint_is_saved_once_for_next_task(self):
        cid = self.store.conversation()
        agent = Agent(self.store, self.registry(), ScriptedProvider([final()]))
        entered, release = threading.Event(), threading.Event()
        original = self.store.checkpoint

        def checkpoint(tid, conversation, status, data):
            if status == 'complete':
                entered.set()
                if not release.wait(5):raise TimeoutError('Checkpoint was not released')
            return original(tid, conversation, status, data)

        self.store.checkpoint = checkpoint
        worker = threading.Thread(target=agent.run, args=(cid, 'First'))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertTrue(worker.is_alive())
            self.assertFalse(agent.say('Late follow-up', ['attachment.png']))
            self.assertEqual(self.store.messages(cid)[-1]['images'], ['attachment.png'])
        finally:
            release.set()
            worker.join(5)
            self.store.checkpoint = original
        self.assertFalse(worker.is_alive())
        self.assertEqual(agent.inbox, [])
        provider = RecordingProvider([final()])
        Agent(self.store, self.registry(), provider).run(cid, 'Continue')
        users = [m['content'] for m in provider.requests[0] if m['role'] == 'user']
        self.assertEqual(users, ['First', 'Late follow-up', 'Continue'])
        self.assertEqual(sum(m['content'] == 'Late follow-up' for m in self.store.messages(cid)), 1)

    def test_running_followups_are_durable_and_do_not_split_tool_exchange(self):
        cid = self.store.conversation()
        provider = RecordingProvider([tool_answer('fs.list', {}, '1'), final()])
        agent = Agent(self.store, self.registry(), provider)
        complete = provider.complete

        def inference(messages, *args):
            if not provider.requests:
                self.assertTrue(agent.say('First follow-up'))
                self.assertTrue(agent.say('Second follow-up', ['attachment.png']))
                rows = self.store.db.execute('SELECT body FROM queued_messages ORDER BY id').fetchall()
                self.assertEqual([json.loads(r[0])['content'] for r in rows], ['First follow-up', 'Second follow-up'])
            return complete(messages, *args)

        provider.complete = inference
        agent.run(cid, 'List files')
        self.assertEqual(self.store.tasks(cid)[0]['status'], 'complete')
        self.assertEqual([m['role'] for m in self.store.messages(cid)],
                         ['user', 'assistant', 'tool', 'user', 'user', 'assistant'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM queued_messages').fetchone()[0], 0)

    def test_restart_recovers_acknowledged_queue_once_with_redaction_and_images(self):
        cid = self.store.conversation()
        self.store.add_message(cid, tool_answer('fs.list', {}, 'unknown'))
        secret = 'synthetic-private-test-value'
        self.store.redactor.register(secret)
        self.store.queue_message(cid, {'role': 'user', 'content': secret, 'images': ['attachment.png']})
        for _ in range(2):
            self.store.close()
            self.store = Store(self.root / 'state', self.redactor)
            messages = self.store.messages(cid)
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[-1]['content'], '[REDACTED]')
            self.assertEqual(messages[-1]['images'], ['attachment.png'])
            self.assertEqual([m['role'] for m in repair_history(messages)], ['assistant', 'tool', 'user'])

    def test_delete_cleans_queued_messages_and_exclusive_images(self):
        cid = self.store.conversation()
        image = self.store.root / 'images' / 'queued.png'
        image.parent.mkdir()
        Image.new('RGB', (2, 2)).save(image)
        self.store.queue_message(cid, {'role': 'user', 'content': 'Queued', 'images': [str(image)]})
        self.store.delete_conversation(cid)
        self.assertFalse(image.exists())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM queued_messages').fetchone()[0], 0)

    def test_setup_normalizes_every_anthropic_thinking_level(self):
        config = self.store.configs()['anthropic']
        fields = {name: Mock(get_text=lambda name=name: str(getattr(config, name)))
                  for name in ('base_url', 'context_tokens', 'max_output', 'input_cost', 'output_cost', 'api_version', 'workspace_id')}
        dialog = SimpleNamespace(app=SimpleNamespace(store=self.store), selected_id=lambda: 'anthropic',
                                 fields=fields, model=Mock(get_text=lambda: 'fixture'),
                                 protocol=Mock(get_selected=lambda: 2), tools=Mock(get_active=lambda: True),
                                 vision=Mock(get_active=lambda: True))
        for wire in ('', 'adaptive', '1024', '4096', '8192', '16384', '32768'):
            with self.subTest(level=wire):
                dialog.thinking = SimpleNamespace(values=[wire], get_selected=lambda: 0)
                saved = ConnectionDialog.config(dialog)
                self.assertEqual(saved.reasoning, wire)
                if wire.isdigit():self.assertGreater(saved.max_output, int(wire))
                fields['max_output'].set_text.assert_called_with(str(saved.max_output))


class OfflineProbeTests(unittest.TestCase):
    def setUp(self):
        self.paths = []
        self.metadata = {}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                fixture.paths.append(self.path)
                if self.path == '/api/show':payload = fixture.metadata
                elif self.path == '/api/chat':
                    payload = {'message': {'content': '', 'tool_calls': [{'function': {
                        'name': 'connection_probe', 'arguments': {'value': 'buddy-test'}}}]}, 'done': True}
                else:self.send_error(404);return
                data = (json.dumps(payload) + '\n').encode()
                self.send_response(200);self.send_header('Content-Length', str(len(data)))
                self.end_headers();self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5)

    def probe(self, model):
        config = ProviderConfig('ollama', f'http://127.0.0.1:{self.server.server_port}', 'ollama', True, model=model)
        dialog = SimpleNamespace(config=lambda: config, key=Mock(get_text=lambda: ''),
            privacy=Mock(get_active=lambda: True), status=Mock(), tools=Mock(), ready=lambda: None,
            app=SimpleNamespace(credentials=Mock(get=lambda _: ''), redactor=Redactor()))
        dialog.run = lambda work, done: done(work(StopToken()))
        ConnectionDialog.verify(dialog)
        return dialog

    def test_offline_probe_rejects_cloud_name_before_any_request(self):
        with self.assertRaises(Denied):self.probe('fixture-cloud')
        self.assertEqual(self.paths, [])

    def test_offline_probe_rejects_remote_metadata_before_inference(self):
        for metadata in ({'remote_host': 'remote'}, {'remote_model': 'remote'}, {'details': {'remote_host': 'remote'}}):
            with self.subTest(metadata=metadata):
                self.metadata = metadata;self.paths.clear()
                with self.assertRaises(Denied):self.probe('ordinary-name')
                self.assertEqual(self.paths, ['/api/show'])

    def test_offline_probe_allows_verified_local_model(self):
        dialog = self.probe('local-fixture')
        self.assertEqual(self.paths, ['/api/show', '/api/chat'])
        self.assertIn('Tool calling verified', dialog.status.set_text.call_args.args[0])
