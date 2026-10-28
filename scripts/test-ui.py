#!/usr/bin/env python3
"""Exercise the actual native UI and HTTP/agent loop with a deterministic model fixture.

No paid/cloud requests. Does not claim real-model acceptance. All state/test files stay
inside .test-data. Renders only this app's widgets, never a locked desktop screenshot.
"""
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import os
import sys
import subprocess
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0');gi.require_version('Gsk','4.0');gi.require_version('Graphene','1.0')
from gi.repository import Gtk,GLib,Graphene
from desktop_buddy.ui import BuddyApp

class Model(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*_):pass
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  if self.path.endswith('/api/show'):
   data=json.dumps({'capabilities':['completion','tools']}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
  tool_results=[m for m in body['messages'] if m['role']=='tool']
  n=len(tool_results)
  stop_test=any('Stop test' in str(m.get('content','')) for m in body['messages'] if m['role']=='user')
  if stop_test:
   args={'name':'shell.run','arguments':{'argv':['sleep','20']},'purpose':'Wait briefly so the Stop button can be tested.'} if n==0 else {'name':'fs.write','arguments':{'path':'must-not-exist','content':'bad'}}
   delta={'tool_calls':[{'index':0,'id':'stop_'+str(n),'function':{'name':'run_tool','arguments':json.dumps(args)}}]};finish='tool_calls'
  elif n<2:
   args={'name':'fs.write','arguments':{'path':'ui-proof.txt','content':'Native UI agent workflow verified'}} if n==0 else {'name':'fs.read','arguments':{'path':'ui-proof.txt'}}
   delta={'tool_calls':[{'index':0,'id':'ui_'+str(n),'function':{'name':'run_tool','arguments':json.dumps(args)}}]};finish='tool_calls'
  else:delta={'content':'I created ui-proof.txt and read it back. The content is Native UI agent workflow verified.'};finish='stop'
  event={'choices':[{'index':0,'delta':delta,'finish_reason':finish}],'usage':{'prompt_tokens':12,'completion_tokens':8}}
  if self.path.endswith('/api/chat'):
   message={'role':'assistant','content':delta.get('content','')}
   if delta.get('tool_calls'):message['tool_calls']=[{'id':tc['id'],'function':{'name':tc['function']['name'],'arguments':json.loads(tc['function']['arguments'])}} for tc in delta['tool_calls']]
   data=(json.dumps({'message':message,'done':True,'prompt_eval_count':12,'eval_count':8})+'\n').encode()
  else:data=('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode()
  self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

root=Path(__file__).resolve().parents[1]/'.test-data'/'ui-integration';root.mkdir(parents=True,exist_ok=True)
server=ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=server.serve_forever,daemon=True).start()
app=BuddyApp(root/'state');c=app.store.configs()['lmstudio'];c.base_url=f'http://127.0.0.1:{server.server_port}';c.model='deterministic-test-fixture';c.tools=True;app.store.save_config(c)
app.store.set('provider','lmstudio');app.store.set('workspace',str(root));app.store.set('mode','execute');app.store.set('local_only',True)
app.credentials.cache['lmstudio']=''
started=time.monotonic();result={};phase=[0];thread_test=[]
activation_process=[]
def capture(window,path):
 paintable=Gtk.WidgetPaintable.new(window);snapshot=Gtk.Snapshot.new();paintable.snapshot(snapshot,float(window.get_width()),float(window.get_height()))
 rect=Graphene.Rect();rect.init(0,0,window.get_width(),window.get_height());texture=window.get_renderer().render_texture(snapshot.to_node(),rect);texture.save_to_png(str(path))
def tick():
 try:
  if time.monotonic()-started>20:raise TimeoutError('UI integration deadline exceeded')
  if phase[0]==0:
   activation_process.append(subprocess.Popen([sys.executable,'-m','desktop_buddy','--data-dir',str(root/'state')],stdout=subprocess.PIPE,stderr=subprocess.PIPE))
   phase[0]=11
  elif phase[0]==11 and activation_process[0].poll() is not None:
   stdout,stderr=activation_process[0].communicate()
   assert activation_process[0].returncode==0,stderr.decode()
   result['second_launch_activates_existing_window']=True
   app.new_conversation();app.send('Create ui-proof.txt with the safe test phrase and read it back.');phase[0]=1
  elif phase[0]==1 and app.worker and not app.worker.is_alive() and time.monotonic()-started>1.5:
   tasks=app.store.tasks(app.cid);assert tasks[0]['status']=='complete',tasks
   assert (root/'ui-proof.txt').read_text()=='Native UI agent workflow verified'
   result['native_ui_agent_workflow']=True
   try:capture(app.window,root/'ui.png')
   except Exception as e:result['render_note']=str(e)
   result['rss_kib']=int(Path('/proc/self/status').read_text().split('VmRSS:')[1].split()[0])
   alternate=app.store.configs()['ollama'];alternate.base_url=f'http://127.0.0.1:{server.server_port}';alternate.model='second-protocol-fixture';alternate.tools=True;app.store.save_config(alternate);app.credentials.cache['ollama']=''
   app.selector.set_selected(app.provider_ids.index('ollama'));app.new_conversation();app.send('Create ui-proof.txt with the safe test phrase and read it back.');phase[0]=10
  elif phase[0]==10 and app.worker and not app.worker.is_alive():
   assert app.agent.provider.config.id=='ollama'
   assert app.store.tasks(app.cid)[0]['status']=='complete'
   result['ui_provider_switch_fixture_verified']=True
   app.selector.set_selected(app.provider_ids.index('lmstudio'))
   # Exercise dialog construction and denial; no effect is approved or executed.
   from desktop_buddy.security import StopToken
   token=StopToken();thread=threading.Thread(target=lambda:thread_test.append(app.approve('UI permission test','This test must be denied.',token)));thread.start();phase[0]=2
  elif phase[0]==2 and app.pending:
   dialog=app.pending[0][1]
   try:capture(dialog,root/'approval.png')
   except Exception as e:result['approval_render_note']=str(e)
   def children(w):
    yield w
    c=w.get_first_child()
    while c:
     yield from children(c);c=c.get_next_sibling()
   deny=next(w for w in children(dialog) if isinstance(w,Gtk.Button) and w.get_label()=='Skip action');deny.emit('clicked');phase[0]=3
  elif phase[0]==3 and thread_test:
   assert thread_test==[False];result['permission_dialog_denied']=True
   app.new_conversation();app.send('Stop test: run a harmless sleep, then create a marker.');phase[0]=4
  elif phase[0]==4 and app.pending:
   dialog=app.pending[0][1]
   def widgets(w):
    yield w
    child=w.get_first_child()
    while child:
     yield from widgets(child);child=child.get_next_sibling()
   allow=next(w for w in widgets(dialog) if isinstance(w,Gtk.Button) and w.get_label()=='Allow once');allow.emit('clicked');phase[0]=5
  elif phase[0]==5 and app.agent and app.agent.token.processes:
   def widgets(w):
    yield w
    child=w.get_first_child()
    while child:
     yield from widgets(child);child=child.get_next_sibling()
   stop=app.stop_button
   result['stop_started']=time.monotonic();stop.emit('clicked');phase[0]=6
  elif phase[0]==6 and not app.worker.is_alive():
   assert app.agent.token.event.is_set() and app.emergency_latched
   assert app.store.tasks(app.cid)[0]['status']=='cancelled'
   assert not (root/'must-not-exist').exists()
   result['emergency_button_callback']=True;result['stop_seconds']=round(time.monotonic()-result.pop('stop_started'),3)
   result['elapsed_seconds']=round(time.monotonic()-started,3);result['model_driven']=False
   result['conversation_count_before_restart']=len(app.store.conversations())
   (root/'result.json').write_text(json.dumps(result,indent=2));app.quit();return False
 except Exception as e:
  result['error']=str(e);print(json.dumps(result),flush=True);app.quit();return False
 return True
GLib.timeout_add(200,tick)
try:app.run([])
finally:server.shutdown();server.server_close();app.store.close()
if 'error' in result:raise SystemExit(1)
subprocess.run([sys.executable,'-m','desktop_buddy','--smoke-test','--data-dir',str(root/'state')],check=True,cwd=root.parents[1])
from desktop_buddy.state import Store
from desktop_buddy.security import Redactor
restored=Store(root/'state',Redactor())
try:
 assert len(restored.conversations())==result['conversation_count_before_restart']
 assert restored.configs()['lmstudio'].model=='deterministic-test-fixture'
 assert restored.get('local_only') is True
 assert restored.get('workspace')==str(root)
 result['separate_process_restart_persistence']=True
finally:restored.close()
(root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2),flush=True)
