#!/usr/bin/env python3
"""Native UI + real provider transport: overlapping conversations and scoped Stop."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0')
from gi.repository import GLib, Gtk
from desktop_buddy.ui import BuddyApp, content

entered={key:threading.Event() for key in ('alpha','beta','gamma','delta')}
release={key:threading.Event() for key in entered}
requests=[]

class Model(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        users=[m['content'] for m in body['messages'] if m['role']=='user']
        name=users[0];requests.append(users);entered[name].set()
        release[name].wait(20)
        event={'choices':[{'delta':{'content':name+' finished.'},'finish_reason':'stop'}]}
        data=('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode()
        try:
            self.send_response(200);self.send_header('Content-Type','text/event-stream')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass

root=Path(__file__).resolve().parents[1]/'.test-data'
root.mkdir(exist_ok=True)
profile=tempfile.TemporaryDirectory(dir=root)
server=ThreadingHTTPServer(('127.0.0.1',0),Model)
threading.Thread(target=server.serve_forever,daemon=True).start()
app=BuddyApp(Path(profile.name)/'state')
c=app.store.configs()['lmstudio'];c.base_url=f'http://127.0.0.1:{server.server_port}'
c.model='concurrency-fixture';app.store.save_config(c)
app.store.set('provider','lmstudio');app.store.set('mode','plan');app.credentials.cache['lmstudio']=''
phase=0;started=time.monotonic();result={};ids={}
cleanup_entered=threading.Event();cleanup_release=threading.Event()

def select(cid):
    row=app.history.get_first_child()
    while row and row.cid!=cid:row=row.get_next_sibling()
    assert row,'Conversation missing from history'
    app.history.select_row(row)
    assert app.cid==cid

def start(name):
    app.new_conversation();ids[name]=app.cid;app.send(name)

def tick():
    global phase
    try:
        if time.monotonic()-started>30:raise TimeoutError(f'phase {phase}')
        if phase==0:
            start('alpha');phase=1
        elif phase==1 and entered['alpha'].is_set():
            app.input.get_buffer().set_text('alpha draft')
            start('beta');phase=2
        elif phase==2 and entered['beta'].is_set():
            a,b=(app.sessions[ids[k]] for k in ('alpha','beta'))
            assert a.running and b.running
            assert a.agent is not b.agent and a.start_token is not b.start_token
            assert a.browser.root!=b.browser.root
            assert a.agent.registry.mcp is not b.agent.registry.mcp
            result['two_live_inference_requests']=True
            assert app.history.get_sensitive()
            select(ids['alpha']);assert content(app.input)=='alpha draft'
            app.send('alpha follow-up')
            assert a.agent.inbox and not b.agent.inbox
            app.cancel()
            assert a.start_token.event.is_set() and not b.start_token.event.is_set()
            select(ids['beta']);phase=3
        elif phase==3 and app.sessions[ids['alpha']].done:
            assert app.sessions[ids['beta']].running
            assert app.cancel_button.get_visible() and app.spinner.get_spinning()
            assert app.store.tasks(ids['alpha'])[0]['status']=='cancelled'
            assert app.store.tasks(ids['beta'])[0]['status']=='running'
            assert 'Stopped' not in app.status.get_text()
            result['stop_and_followup_are_conversation_scoped']=True
            release['beta'].set();phase=4
        elif phase==4 and app.sessions[ids['beta']].done:
            assert app.store.tasks(ids['beta'])[0]['status']=='complete'
            assert app.last_reply=='beta finished.'
            # Replaying a queued answer after history already rendered it is idempotent.
            before=0;child=app.transcript.get_first_child()
            while child:before+=1;child=child.get_next_sibling()
            app.session_event(app.cid,app.current_session.generation,'message',{'content':'beta finished.'})
            after=0;child=app.transcript.get_first_child()
            while child:after+=1;child=child.get_next_sibling()
            assert before==after
            assert not any(m.get('content')=='beta finished.' for m in app.store.messages(ids['alpha']))
            assert not app.cancel_button.get_visible()
            result['reply_and_completion_routing']=True
            start('gamma');phase=5
        elif phase==5 and entered['gamma'].is_set():
            start('delta');phase=6
        elif phase==6 and entered['delta'].is_set():
            # Complete a background conversation while foreground work keeps going.
            release['gamma'].set();phase=7
        elif phase==7 and app.sessions[ids['gamma']].done:
            assert app.sessions[ids['delta']].running and app.cancel_button.get_visible()
            assert app.last_reply!='gamma finished.'
            select(ids['gamma']);assert not app.cancel_button.get_visible()
            assert app.last_reply=='gamma finished.'
            select(ids['delta']);assert app.cancel_button.get_visible()
            # A stale completion cannot overwrite a later run.
            app.session_event(ids['delta'],0,'done',{'status':'complete'})
            assert app.sessions[ids['delta']].done is None
            app.new_conversation();app.send('alpha');phase=8
        elif phase==8 and app.current_session.running:
            background=app.sessions[ids['delta']]
            approval=threading.Thread(target=lambda:app.approve('Concurrent approval fixture','No external action.',background.start_token))
            approval.start();result['approval_thread']=approval;phase=10
        elif phase==10 and app.sessions[ids['delta']].attention:
            assert not app.pending  # background requests do not steal focus
            row=app.history.get_first_child()
            while row.cid!=ids['delta']:row=row.get_next_sibling()
            assert 'Needs you' in row.get_tooltip_text()
            select(ids['delta']);phase=11
        elif phase==11 and app.pending:
            dialog=app.pending[0][1]
            assert 'delta' in dialog.get_title()
            assert not dialog.get_modal()
            app.new_conversation();assert not dialog.get_visible()
            app.cancel();assert not app.sessions[ids['delta']].start_token.event.is_set()
            select(ids['delta']);assert dialog.get_visible()
            def widgets(w):
                yield w
                child=w.get_first_child()
                while child:
                    yield from widgets(child);child=child.get_next_sibling()
            next(w for w in widgets(dialog) if isinstance(w,Gtk.Button) and w.get_label()=='Skip action').emit('clicked')
            phase=12
        elif phase==12 and not result['approval_thread'].is_alive():
            result.pop('approval_thread');result['background_approval_is_scoped']=True
            browser=app.sessions[ids['alpha']].browser
            original_close=browser.close
            def slow_close():
                # An old conversation's browser must never delay stopping delta.
                assert all(s.start_token.event.is_set() for s in app.sessions.values())
                cleanup_entered.set();cleanup_release.wait(10);original_close()
            browser.close=slow_close
            before=time.monotonic();app.stop_button.emit('clicked')
            assert time.monotonic()-before<.5
            assert app.stop_button.get_label()=='Stopped'
            assert all(s.start_token.event.is_set() for s in app.sessions.values())
            app.stop_button.emit('clicked')  # repeated clicks remain responsive
            phase=9
        elif phase==9 and not app.any_running():
            assert all(s.start_token.event.is_set() for s in app.sessions.values())
            assert cleanup_entered.is_set() and app.stop_cleanup_pending
            # Simulate a progress callback queued immediately before Stop.
            session=app.current_session;before=app.status.get_text()
            app.session_event(app.cid,session.generation,'status','Still working')
            assert app.status.get_text()==before
            cleanup_release.set();phase=13
        elif phase==13 and not app.stop_cleanup_pending:
            assert app.status.get_text()=='Stopped · computer control and app connections are closed'
            result['background_completion_and_stop_everything']=True
            result['stop_button_stays_responsive_during_browser_cleanup']=True
            app.quit();return False
    except Exception as error:
        import traceback
        traceback.print_exc();result['error']=str(error);app.quit();return False
    return True

GLib.timeout_add(50,tick)
try:app.run([])
finally:
    cleanup_release.set()
    app.emergency()
    approval=result.pop('approval_thread',None)
    if approval:approval.join(3)
    for event in release.values():event.set()
    for session in app.sessions.values():
        if session.worker:session.worker.join(5)
    server.shutdown();server.server_close();app.store.close();profile.cleanup()
print(json.dumps(result,indent=2))
if 'error' in result or getattr(app,'startup_error',None) or len(result)<4:raise SystemExit(1)
