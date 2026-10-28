"""Concurrent sessions must not share cancellation, transports, or desktop input."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import shutil
from http.server import ThreadingHTTPServer
import threading
import time
import unittest
from unittest.mock import Mock

from desktop_buddy.browser import Browser
from desktop_buddy.mcp_client import MCPConfig, MCPManager
from desktop_buddy.security import Cancelled, StopToken
from desktop_buddy.sessions import DesktopLease
from test_core import Sandbox
from test_mcp import Credentials


class SessionTests(Sandbox):
    def test_desktop_observation_and_input_keep_one_owner_until_task_finishes(self):
        lease=DesktopLease();first=StopToken();second=StopToken()
        waiting=threading.Event();acquired=threading.Event()
        lease.acquire(first,lambda _:None)
        lease.acquire(first,lambda _:None)  # the same task observes, then acts
        def work():
            lease.acquire(second,lambda _:waiting.set());acquired.set()
        with ThreadPoolExecutor() as pool:
            future=pool.submit(work)
            self.assertTrue(waiting.wait(2));self.assertFalse(acquired.is_set())
            lease.release(second);self.assertFalse(acquired.is_set())
            lease.release(first);future.result(2)
            self.assertTrue(acquired.is_set())
            lease.release(second)

    def test_waiting_desktop_task_cancels_without_releasing_owner(self):
        lease=DesktopLease();owner=StopToken();waiting=StopToken();entered=threading.Event()
        lease.acquire(owner,lambda _:None)
        with ThreadPoolExecutor() as pool:
            future=pool.submit(lease.acquire,waiting,lambda _:entered.set())
            self.assertTrue(entered.wait(2));waiting.stop()
            with self.assertRaises(Cancelled):future.result(2)
        self.assertIs(lease.owner,owner)
        lease.release(owner)

    def test_registry_serializes_desktop_but_not_file_work(self):
        lease=DesktopLease();owner=StopToken();token=StopToken();lease.acquire(owner,lambda _:None)
        registry=self.registry(mode='yolo');registry.desktop_lease=lease
        registry.accessibility=Mock();registry.accessibility.inspect.return_value={'windows':[]}
        waiting=threading.Event();registry.progress=lambda _:waiting.set()
        with ThreadPoolExecutor() as pool:
            future=pool.submit(registry.execute,'desktop.accessibility',{},token)
            self.assertTrue(waiting.wait(2));registry.accessibility.inspect.assert_not_called()
            result=registry.execute('fs.write',{'path':'independent.txt','content':'parallel'},StopToken())
            self.assertTrue(result['verified'])
            lease.release(owner);self.assertEqual(future.result(2),{'windows':[]})
            lease.release(token)

    def test_mcp_task_close_is_local_but_user_disconnect_reaches_all_sessions(self):
        manager=MCPManager(self.store,Credentials(self.redactor));a=manager.fork();b=manager.fork()
        one,two=Mock(),Mock();a.connections['fixture']=one;b.connections['fixture']=two
        a.close();one.close.assert_called_once();two.close.assert_not_called()
        one.reset_mock();a.connections['fixture']=one
        manager.disconnect('fixture')
        one.close.assert_called_once();two.close.assert_called_once()
        self.assertFalse(a.connections);self.assertFalse(b.connections)

    def test_real_mcp_cancel_does_not_close_another_tasks_transport(self):
        manager=MCPManager(self.store,Credentials(self.redactor))
        manager.save(MCPConfig('fixture','Fixture',transport='stdio',auth='none',
                              command=[sys.executable,str(Path(__file__).with_name('mcp_fixture.py'))]))
        a,b=manager.fork(),manager.fork();token=StopToken();other=StopToken();connections=[]
        try:
            a.tools('fixture',token);b.tools('fixture',other)
            connections=[a.connections['fixture'],b.connections['fixture']]
            with ThreadPoolExecutor() as pool:
                marker=self.root/'mcp-started'
                future=pool.submit(a.call,'fixture','slow',{'started_path':str(marker)},token)
                deadline=time.monotonic()+5
                while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(marker.exists(),'MCP call must be in flight before cancellation')
                # Stop only the first session; the other transport remains usable.
                token.stop()
                with self.assertRaises(Cancelled):future.result(3)
                result=b.call('fixture','echo',{'message':'other session still works'},other)
                self.assertIn('other session still works',str(result))
                self.assertFalse(connections[1].stopped.is_set())
            manager.disconnect('fixture')
            self.assertTrue(connections[1].stopped.is_set())
        finally:
            manager.close_all()
            for connection in connections:connection.thread.join(5)

    @unittest.skipUnless(shutil.which('google-chrome') or shutil.which('chromium'), 'Chromium unavailable')
    def test_real_background_browsers_keep_pages_and_cookies_separate(self):
        from test_browser_routing import Origin
        server=ThreadingHTTPServer(('127.0.0.1',0),Origin);server.hits=[]
        threading.Thread(target=server.serve_forever,daemon=True).start()
        first=Browser(self.root/'browser-a',headless=True)
        second=Browser(self.root/'browser-b',headless=True)
        token=StopToken();other=StopToken();base=f'http://127.0.0.1:{server.server_port}'
        try:
            with ThreadPoolExecutor() as pool:
                a=pool.submit(first.navigate,base+'/alpha',token)
                b=pool.submit(second.navigate,base+'/beta',other)
                self.assertEqual(a.result(20)['url'],base+'/alpha')
                self.assertEqual(b.result(20)['url'],base+'/beta')
            first.evaluate("document.cookie='session=alpha'",token)
            self.assertEqual(second.evaluate('document.cookie',other),'')
            first.close()
            self.assertEqual(second.inspect(other)['url'],base+'/beta')
        finally:
            first.close();second.close();server.shutdown();server.server_close()
