import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from desktop_buddy.comfyui import ComfyUI, ComfyUIError
from desktop_buddy.security import Denied, Policy, Redactor, StopToken
from desktop_buddy.state import Store
from desktop_buddy.tools import Registry


PNG = b'\x89PNG\r\n\x1a\nverified-test-image'


class Fixture(BaseHTTPRequestHandler):
    prompt_id = None
    workflow = None
    complete = True
    cancelled = False
    prompt_event = threading.Event()

    def log_message(self, *_):pass

    def reply(self, value, content_type='application/json'):
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/system_stats':
            self.reply({'system': {'comfyui_version': 'fixture'}, 'devices': [{'name': 'test GPU', 'type': 'cuda'}]})
        elif self.path == '/queue':
            self.reply({'queue_running': [], 'queue_pending': []})
        elif self.path.startswith('/history/'):
            if self.complete:
                self.reply({self.prompt_id: {'status': {'status_str': 'success', 'completed': True},
                    'outputs': {'2': {'images': [{'filename': 'proof.png', 'subfolder': '', 'type': 'output'}]}}}})
            else:self.reply({})
        elif self.path.startswith('/view?'):
            self.reply(PNG, 'image/png')
        else:self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = json.loads(self.rfile.read(length) or b'{}')
        if self.path == '/prompt':
            self.__class__.prompt_id = body['prompt_id']
            self.__class__.workflow = body['prompt']
            self.__class__.prompt_event.set()
            self.reply({'prompt_id': body['prompt_id'], 'number': 1, 'node_errors': {}})
        elif self.path.endswith('/cancel'):
            self.__class__.cancelled = True
            self.reply({'cancelled': True})
        else:self.send_error(404)


class ComfyUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close()

    def test_status_is_bounded_summary(self):
        status = ComfyUI(self.url).status(StopToken())
        self.assertEqual(status['version'], 'fixture')
        self.assertEqual(status['devices'][0]['name'], 'test GPU')
        self.assertEqual(status['running'], 0)

    def test_run_applies_overrides_waits_and_copies_output_exclusively(self):
        workflow = {'1': {'class_type': 'EmptyImage', 'inputs': {'width': 8}},
                    '2': {'class_type': 'SaveImage', 'inputs': {'images': ['1', 0]}}}
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'outputs'
            result = ComfyUI(self.url).run(json.dumps(workflow), {'1': {'width': 16}}, destination, StopToken(), 10)
            self.assertTrue(result['verified'])
            self.assertEqual(Fixture.workflow['1']['inputs']['width'], 16)
            output = Path(result['outputs'][0]['path'])
            self.assertEqual(output.read_bytes(), PNG)
            self.assertEqual(output.parent, destination)

    def test_rejects_non_loopback_bad_workflow_and_missing_override_node(self):
        with self.assertRaises(Denied):ComfyUI('https://example.com')
        with self.assertRaises(ComfyUIError):ComfyUI.workflow('{}', {})
        workflow = json.dumps({'1': {'class_type': 'EmptyImage', 'inputs': {}}})
        with self.assertRaisesRegex(ComfyUIError, 'missing node'):ComfyUI.workflow(workflow, {'9': {}})

    def test_registry_requires_one_compute_approval_and_workspace_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary);workspace = root / 'workspace';workspace.mkdir()
            (workspace / 'workflow.json').write_text(json.dumps({'1': {'class_type': 'EmptyImage', 'inputs': {}}}))
            store = Store(root / 'state', Redactor());approvals=[]
            try:
                policy = Policy(workspace, 'execute', False, lambda *args: approvals.append(args) or True, protected=[store.root])
                with patch('desktop_buddy.tools.ComfyUI', lambda *_: ComfyUI(self.url)):
                    registry = Registry(policy, store)
                result = registry.execute('comfyui.run', {'workflow_path': 'workflow.json', 'output_directory': 'results'}, StopToken(), 'task')
                self.assertTrue(result['verified'])
                self.assertEqual(len(approvals), 1)
                self.assertEqual(approvals[0][0], 'comfyui.run')
                with self.assertRaises(Denied):
                    registry.execute('comfyui.run', {'workflow_path': 'workflow.json', 'output_directory': str(root / 'outside')}, StopToken(), 'task')
            finally:store.close()

    def test_batch_uses_one_approval_and_reports_every_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);workspace=root/'workspace';workspace.mkdir()
            (workspace/'workflow.json').write_text(json.dumps({'1': {'class_type':'TextNode','inputs':{'text':''}}}))
            store=Store(root/'state',Redactor());approvals=[]
            try:
                policy=Policy(workspace,'execute',False,lambda *args:approvals.append(args[0]) or True,protected=[store.root])
                with patch('desktop_buddy.tools.ComfyUI',lambda *_:ComfyUI(self.url)):
                    registry=Registry(policy,store)
                result=registry.execute('comfyui.run_batch',{'workflow_path':'workflow.json','prompt_node_id':'1',
                    'input_name':'text','prompts':['first','second'],'output_directory':'batch'},StopToken(),'batch-task')
                self.assertTrue(result['verified'])
                self.assertEqual(result['complete'],2)
                self.assertEqual([job['prompt'] for job in result['jobs']],['first','second'])
                self.assertEqual(approvals,['comfyui.run_batch'])
            finally:store.close()

    def test_stop_cancels_specific_external_job(self):
        Fixture.complete = False;Fixture.cancelled = False;Fixture.prompt_event.clear()
        workflow = json.dumps({'1': {'class_type': 'EmptyImage', 'inputs': {}}})
        token = StopToken();result=[]
        with tempfile.TemporaryDirectory() as temporary:
            worker = threading.Thread(target=lambda: self._run_cancel_test(workflow, Path(temporary), token, result))
            worker.start()
            self.assertTrue(Fixture.prompt_event.wait(2))
            token.stop();worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result, ['cancelled'])
            self.assertTrue(Fixture.cancelled)
        Fixture.complete = True

    def _run_cancel_test(self, workflow, destination, token, result):
        from desktop_buddy.security import Cancelled
        try:ComfyUI(self.url).run(workflow, {}, destination, token, 30)
        except Cancelled:result.append('cancelled')


if __name__ == '__main__':unittest.main()
