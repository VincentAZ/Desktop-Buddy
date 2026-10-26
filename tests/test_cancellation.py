import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import time
import unittest
from desktop_buddy.providers import Provider
from desktop_buddy.state import ProviderConfig
from desktop_buddy.security import Cancelled,StopToken,Redactor

class StalledHandler(BaseHTTPRequestHandler):
    event=threading.Event()
    release=threading.Event()
    def log_message(self,*_):pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers();self.wfile.flush()
        self.event.set();self.release.wait(5)

    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.flush()
        self.event.set();self.release.wait(5)

class CancellationTests(unittest.TestCase):
    def test_emergency_interrupts_stalled_inference_socket(self):
        StalledHandler.event.clear();StalledHandler.release.clear()
        server=ThreadingHTTPServer(('127.0.0.1',0),StalledHandler);threading.Thread(target=server.serve_forever,daemon=True).start()
        token=StopToken();result=[]
        provider=Provider(ProviderConfig('lmstudio',f'http://127.0.0.1:{server.server_port}','chat',True,model='fixture'))
        def run():
            try:provider.complete([{'role':'user','content':'stall'}],[],token,local_only=True)
            except Cancelled:result.append('cancelled')
        worker=threading.Thread(target=run);worker.start()
        try:
            self.assertTrue(StalledHandler.event.wait(3));start=time.monotonic();token.stop();worker.join(2)
            self.assertFalse(worker.is_alive());self.assertLess(time.monotonic()-start,1);self.assertEqual(result,['cancelled'])
        finally:StalledHandler.release.set();server.shutdown();server.server_close();worker.join(2)

    def test_stop_interrupts_stalled_model_discovery(self):
        StalledHandler.event.clear();StalledHandler.release.clear()
        server=ThreadingHTTPServer(('127.0.0.1',0),StalledHandler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        token=StopToken();result=[]
        provider=Provider(ProviderConfig('lmstudio',f'http://127.0.0.1:{server.server_port}','chat',True))
        def run():
            try:provider.models(token,local_only=True)
            except Cancelled:result.append('cancelled')
        worker=threading.Thread(target=run);worker.start()
        try:
            self.assertTrue(StalledHandler.event.wait(3))
            start=time.monotonic();token.stop();worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertLess(time.monotonic()-start,1)
            self.assertEqual(result,['cancelled'])
        finally:
            StalledHandler.release.set();server.shutdown();server.server_close();worker.join(2)
    def test_stream_preview_hides_secret_prefix(self):
        r=Redactor();r.register('custom-secret-value-1234567890')
        self.assertNotIn('custom-secret',r.stream_text('prefix custom-secret'))
        self.assertNotIn('sk-abc',r.stream_text('prefix sk-abc'))

if __name__=='__main__':unittest.main()

class RealSandboxTests(unittest.TestCase):
    def test_local_shell_cannot_reach_host_loopback(self):
        import tempfile
        import socket
        from pathlib import Path
        from desktop_buddy.tools import run_process
        server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1)
        port=server.getsockname()[1]
        try:
            test_root=Path(__file__).resolve().parents[1]/'.test-data';test_root.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(dir=test_root) as folder:
                code=f'import socket; s=socket.socket(); s.settimeout(.2); result=s.connect_ex(("127.0.0.1",{port})); print(result); assert result != 0'
                result=run_process(['/usr/bin/python3','-c',code],Path(folder),StopToken(),local_only=True)
                self.assertEqual(result['exit_code'],0,result['output'])
        finally:server.close()
