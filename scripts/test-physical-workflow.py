#!/usr/bin/env python3
"""Test only dedicated data: browser form -> verified file -> terminal view.

Uses the real browser/terminal and app tools; no AI model. A temporary HTTP server
binds only numeric loopback and serves an inline test page. Closes its own apps.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_buddy.browser import Browser
from desktop_buddy.security import Policy,Redactor,StopToken
from desktop_buddy.state import Store
from desktop_buddy.tools import Registry

class Page(BaseHTTPRequestHandler):
 def log_message(self,*_):pass
 def do_GET(self):
  body=b'''<!doctype html><title>Desktop Buddy safe test</title><h1>Local acceptance form</h1><input id="note"><button id="save" onclick="document.getElementById('result').textContent=document.getElementById('note').value">Preview</button><p id="result"></p>'''
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)

root=Path(__file__).resolve().parents[1]/'.test-data'/'physical-workflow';root.mkdir(parents=True,exist_ok=True)
store=Store(root/'state',Redactor());policy=Policy(root,'execute',False,lambda *_:True,protected=[store.root])
# The test's fixed operations above/below are explicitly authorized safe test data.
browser=Browser(root/'chrome');registry=Registry(policy,store,browser=browser);token=StopToken()
server=ThreadingHTTPServer(('127.0.0.1',0),Page);threading.Thread(target=server.serve_forever,daemon=True).start();terminal=None
try:
 page=registry.execute('browser.navigate',{'url':f'http://127.0.0.1:{server.server_port}'},token,'physical')
 assert page['verified'] and page['title']=='Desktop Buddy safe test'
 failed=False
 try:registry.execute('browser.action',{'selector':'#missing','action':'click'},token,'physical')
 except RuntimeError:failed=True
 assert failed
 registry.execute('browser.action',{'selector':'#note','action':'fill','text':'Desktop Buddy two-application workflow verified'},token,'physical')
 result=registry.execute('browser.action',{'selector':'#save','action':'click'},token,'physical')
 assert 'Desktop Buddy two-application workflow verified' in result['observation']['text']
 written=registry.execute('fs.write',{'path':'workflow-report.txt','content':result['observation']['text']+'\n'},token,'physical')
 assert written['verified']
 assert registry.execute('fs.read',{'path':'workflow-report.txt'},token,'physical')['content'].endswith('verified\n')
 terminal=subprocess.Popen(['/usr/bin/ptyxis','--standalone','--title','Desktop Buddy workflow test','--working-directory',str(root),'--','/bin/sh','-c','cat workflow-report.txt | tee terminal-readback.txt; sleep 15'],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 deadline=time.monotonic()+8
 while time.monotonic()<deadline and not (root/'terminal-readback.txt').exists():time.sleep(.1)
 assert (root/'terminal-readback.txt').read_text()==(root/'workflow-report.txt').read_text()
 evidence={'browser_title':page['title'],'browser_form_verified':True,'missing_selector_recovered':True,'file_sha256':written['sha256'],'terminal_readback_matches':True,'model_driven':False}
 (root/'result.json').write_text(json.dumps(evidence,indent=2));print(json.dumps(evidence,indent=2))
finally:
 if terminal:
  try:os.killpg(terminal.pid,signal.SIGTERM)
  except ProcessLookupError:pass
  terminal.wait(timeout=5)
 browser.close();server.shutdown();server.server_close();store.close()
