import asyncio
import http.client
import json,os,subprocess,sys
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import shutil,socket,socketserver,tempfile,threading,time,unittest
from urllib.parse import urlsplit
from desktop_buddy.browser import Browser
from desktop_buddy.browser_proxy import BrowserProxy
from desktop_buddy.security import Denied,StopToken


class Origin(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_GET(self):
        self.server.hits.append(self.path)
        blocked=f'http://localhost:{self.server.server_port}/blocked'
        if self.path=='/redirect':
            self.send_response(302);self.send_header('Location',blocked);self.send_header('Content-Length','0');self.end_headers();return
        if self.path=='/worker.js':
            body=f'fetch("{blocked}").catch(()=>{{}})'.encode();kind='text/javascript'
        else:
            body=f'''<!doctype html><title>Routing fixture</title><h1>Allowed page</h1>
            <button id="popup" onclick="window.open('{blocked}')">Popup</button>
            <button id="fetch" onclick="fetch('{blocked}').catch(()=>{{}})">Fetch</button>
            <button id="worker" onclick="new Worker('/worker.js')">Worker</button>'''.encode();kind='text/html'
        self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_POST(self):
        self.server.hits.append(self.path)
        body=self.rfile.read(int(self.headers['Content-Length']))
        self.server.last_headers=dict(self.headers)
        self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)


class CountingServer(ThreadingHTTPServer):
    def get_request(self):
        result=super().get_request();self.connections+=1;return result


class OriginFixture:
    def setUp(self):
        self.origin=CountingServer(('127.0.0.1',0),Origin);self.origin.hits=[];self.origin.connections=0
        threading.Thread(target=self.origin.serve_forever,daemon=True).start()
        self.blocks=[]
        self.proxy=BrowserProxy(self.guard,lambda *args:self.blocks.append(args))
    def guard(self,url):
        if urlsplit(url).hostname=='localhost':raise Denied('Use the fixture MCP connection.')
    def tearDown(self):
        self.proxy.close();self.origin.shutdown();self.origin.server_close()


class RoutingTests(OriginFixture,unittest.TestCase):
    def test_saved_endpoint_blocks_http_and_tunnels_before_upstream_connections(self):
        from unittest.mock import Mock
        from desktop_buddy.mcp_client import MCPConfig,MCPManager
        from desktop_buddy.state import Store
        from desktop_buddy.security import Redactor
        with tempfile.TemporaryDirectory() as root:
            redactor=Redactor();store=Store(Path(root)/'state',redactor)
            credentials=Mock(redactor=redactor);manager=MCPManager(store,credentials)
            other=CountingServer(('127.0.0.1',0),Origin);other.hits=[];other.connections=0
            threading.Thread(target=other.serve_forever,daemon=True).start()
            try:
                manager.save(MCPConfig('private','Private service',auth='none',
                    url=f'http://127.0.0.1:{self.origin.server_port}/mcp'))
                self.proxy.guard=manager.require_browser_fallback
                for host in ('127.0.0.1','localhost','alternate.localhost','[::1]'):
                    for method,target in [('GET',f'http://{host}:{self.origin.server_port}/anything'),
                                          ('CONNECT',f'{host}:{self.origin.server_port}')]:
                        with self.subTest(host=host,method=method):
                            connection=http.client.HTTPConnection('127.0.0.1',self.proxy.port,timeout=3)
                            try:
                                connection.request(method,target);response=connection.getresponse()
                                self.assertEqual(response.status,403);response.read()
                            finally:connection.close()
                self.assertEqual(self.origin.connections,0);self.assertEqual(self.origin.hits,[])
                connection=http.client.HTTPConnection('127.0.0.1',self.proxy.port,timeout=3)
                try:
                    connection.request('GET',f'http://127.0.0.1:{other.server_port}/allowed')
                    response=connection.getresponse();self.assertEqual(response.status,200);response.read()
                finally:connection.close()
                self.assertEqual(other.hits,['/allowed'])
                credentials.get.assert_not_called()
            finally:
                self.proxy.guard=self.guard
                other.shutdown();other.server_close();manager.close();store.close()

    def test_concurrent_close_waits_for_connection_cleanup_without_stopping_loop_twice(self):
        entered=threading.Event();cleaning=threading.Event();cleaned=threading.Event()
        class SlowCleanup(BrowserProxy):
            async def _client(self,reader,writer):
                task=asyncio.current_task();self.clients.add(task)
                entered.set()
                try:await asyncio.Event().wait()
                finally:
                    cleaning.set();await asyncio.sleep(.1)
                    writer.close();await writer.wait_closed();cleaned.set();self.clients.discard(task)
        proxy=SlowCleanup(lambda _:None,lambda *_:None)
        try:
            with socket.create_connection(('127.0.0.1',proxy.port),timeout=3):
                self.assertTrue(entered.wait(2))
                closer=threading.Thread(target=proxy.close);closer.start()
                self.assertTrue(cleaning.wait(2));proxy.close();closer.join(2)
                self.assertTrue(cleaned.is_set());self.assertFalse(proxy.thread.is_alive())
                self.assertTrue(proxy.loop.is_closed())
        finally:proxy.close()
    def test_allowed_http_body_and_headers_are_forwarded_without_proxy_credentials(self):
        c=http.client.HTTPConnection('127.0.0.1',self.proxy.port,timeout=3)
        try:
            c.request('POST',f'http://127.0.0.1:{self.origin.server_port}/echo',body=b'fixture body',
                      headers={'Proxy-Authorization':'fixture-proxy-secret','Host':'untrusted.example'})
            response=c.getresponse();self.assertEqual(response.status,200);self.assertEqual(response.read(),b'fixture body')
        finally:c.close()
        self.assertEqual(self.origin.hits,['/echo'])
        self.assertNotIn('Proxy-Authorization',self.origin.last_headers)
        self.assertEqual(self.origin.last_headers['Host'],f'127.0.0.1:{self.origin.server_port}')
    def test_http_and_https_tunnel_are_denied_before_any_origin_request(self):
        for method,target in [('GET',f'http://localhost:{self.origin.server_port}/blocked'),
                              ('CONNECT',f'localhost:{self.origin.server_port}')]:
            with self.subTest(method=method):
                c=http.client.HTTPConnection('127.0.0.1',self.proxy.port,timeout=3)
                try:c.request(method,target);response=c.getresponse();self.assertEqual(response.status,403);response.read()
                finally:c.close()
        self.assertEqual(self.origin.hits,[]);self.assertEqual(len(self.blocks),2)
        self.assertEqual(self.origin.connections,0)
    def test_opaque_tunnel_is_closed_when_browser_routing_stops(self):
        with socket.create_connection(('127.0.0.1',self.proxy.port),timeout=3) as c:
            c.sendall(f'CONNECT 127.0.0.1:{self.origin.server_port} HTTP/1.1\r\nHost: ignored\r\n\r\n'.encode())
            self.assertIn(b'200 Connection Established',c.recv(4096))
            c.sendall(b'GET /tunnel HTTP/1.1\r\nHost: localhost\r\n\r\n')
            self.assertIn(b'200 OK',c.recv(4096))
        self.assertEqual(self.origin.hits,['/tunnel'])
        self.proxy.close();self.assertFalse(self.proxy.thread.is_alive())
    def test_new_service_policy_applies_to_an_existing_tunnel(self):
        received=[]
        class Echo(socketserver.BaseRequestHandler):
            def handle(self):
                while data:=self.request.recv(1024):
                    received.append(data);self.request.sendall(data)
        server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Echo)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with socket.create_connection(('127.0.0.1',self.proxy.port),timeout=3) as c:
                c.sendall(f'CONNECT 127.0.0.1:{server.server_address[1]} HTTP/1.1\r\n\r\n'.encode())
                self.assertIn(b'200 Connection Established',c.recv(4096))
                c.sendall(b'allowed');self.assertEqual(c.recv(1024),b'allowed')
                def changed(_):raise Denied('Use the newly configured MCP.')
                self.proxy.guard=changed
                c.sendall(b'must not arrive');self.assertEqual(c.recv(1024),b'')
            self.assertEqual(received,[b'allowed']);self.assertTrue(self.blocks)
        finally:server.shutdown();server.server_close()


@unittest.skipUnless(shutil.which('google-chrome') or shutil.which('chromium'),'Chrome is required for real routing checks')
class ChromiumRoutingTests(OriginFixture,unittest.TestCase):
    @unittest.skipUnless(shutil.which('xvfb-run'),'Xvfb is required for display authentication check')
    def test_visible_browser_keeps_the_x_display_authorization(self):
        env=dict(os.environ);env.pop('WAYLAND_DISPLAY',None);env['XDG_SESSION_TYPE']='x11'
        with tempfile.TemporaryDirectory() as root:
            program='''import json,sys
from desktop_buddy.browser import Browser
from desktop_buddy.security import StopToken
b=Browser(sys.argv[1])
try: print(json.dumps(b.navigate(sys.argv[2],StopToken())))
finally: b.close()
'''
            result=subprocess.run(['xvfb-run','-a',sys.executable,'-c',program,root,
                                   f'http://127.0.0.1:{self.origin.server_port}/page'],
                                  env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['ready'],'complete')
            self.assertNotIn('Event loop stopped before Future completed',result.stderr)
    def setUp(self):
        super().setUp();self.temp=tempfile.TemporaryDirectory()
        self.browser=Browser(Path(self.temp.name)/'chrome',headless=True)
        self.browser.set_url_guard(self.guard);self.token=StopToken()
    def tearDown(self):
        self.browser.close();self.temp.cleanup();super().tearDown()
    def test_browser_exit_stops_its_proxy_without_an_app_close_call(self):
        self.browser.start(self.token);proxy=self.browser.proxy
        try:self.browser.call('Browser.close',{},self.token,session=False)
        except RuntimeError as error:self.assertIn('Browser pipe closed',str(error))
        self.browser.proc.wait(timeout=5);proxy.thread.join(4)
        self.assertFalse(proxy.thread.is_alive())
        with self.assertRaises(OSError):socket.create_connection(('127.0.0.1',proxy.port),timeout=.3)
    def test_real_redirect_popup_fetch_and_worker_cannot_reach_mcp_service(self):
        base=f'http://127.0.0.1:{self.origin.server_port}'
        for case in ('redirect','popup','fetch','worker'):
            with self.subTest(case=case):
                blocked=False
                try:
                    page=self.browser.navigate(base+('/redirect' if case=='redirect' else '/'),self.token)
                    self.assertEqual(page['title'],'Routing fixture')
                    self.browser.action({'action':'click','selector':'#'+case},self.token)
                    deadline=time.monotonic()+3
                    while time.monotonic()<deadline:
                        self.browser.inspect(self.token);time.sleep(.05)
                except Denied as error:
                    self.assertIn('fixture MCP connection',str(error));blocked=True
                self.assertTrue(blocked,f'{case} did not reach the routing guard')
                self.assertNotIn('/blocked',self.origin.hits)
