#!/usr/bin/env python3
"""Selected spreadsheet → native UI → agent tools → saved conversation, no paid API."""
import json
from pathlib import Path
import sys,tempfile,threading,time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0');gi.require_version('Graphene','1.0')
from gi.repository import Gtk,GLib,Graphene
from openpyxl import Workbook
from desktop_buddy.ui import BuddyApp
from desktop_buddy.security import StopToken

root=Path(__file__).resolve().parents[1]/'.test-data'/'file-ui';root.mkdir(parents=True,exist_ok=True)
profile=tempfile.TemporaryDirectory(dir=root);app=BuddyApp(Path(profile.name)/'state')
app.store.set('window_width',940);app.store.set('window_height',700)
config=app.store.configs()['ollama'];config.model='fixture';app.store.save_config(config);app.store.set('provider','ollama')
app.credentials.get=lambda _:''
source=Path(profile.name)/'Quarterly sales.xlsx';book=Workbook();book.active.title='Sales'
book.active.append(['Region','Revenue']);book.active.append(['West',12000]);book.save(source);book.close()
original=source.read_bytes();result={};phase=0;started=time.monotonic()

class FixtureProvider:
    def __init__(self,config,*_):self.config=config;self.round=0
    def check(self,*_):pass
    def complete(self,messages,*_):
        self.round+=1
        if self.round==1:
            assert file['id'] in messages[-1]['content']
            assert file['path'] not in messages[-1]['content']
            return {'role':'assistant','content':'I’ll read the attached spreadsheet.','tool_calls':[{'id':'read','name':'run_tool','arguments':{'name':'attachments.read','arguments':{'id':file['id'],'sheet':'Sales'}}}]},{}
        data=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))['untrusted_tool_result']
        assert data['rows'][1]['cells']['B2']['value']==12000
        return {'role':'assistant','content':'West revenue is 12,000. Your original spreadsheet is unchanged.'},{}

def walk(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from walk(child);child=child.get_next_sibling()

def visible_text(window):return '\n'.join(w.get_text() for w in walk(window) if isinstance(w,Gtk.Label) and w.get_mapped())

def capture(window,name):
    paintable=Gtk.WidgetPaintable.new(window);snapshot=Gtk.Snapshot.new()
    paintable.snapshot(snapshot,float(window.get_width()),float(window.get_height()));node=snapshot.to_node()
    if node is None:return False
    rect=Graphene.Rect();rect.init(0,0,window.get_width(),window.get_height())
    window.get_renderer().render_texture(node,rect).save_to_png(str(root/name));return True

def tick():
    global phase,file,review_thread,decision
    try:
        if time.monotonic()-started>30:raise TimeoutError(f'phase {phase}')
        if phase==0:
            file=app.attach_file_path(source)
            assert app.pending_files==[file]
            app.input.get_buffer().set_text('What was West revenue?');phase=1
        elif phase==1 and capture(app.window,'attached.png'):
            assert 'Quarterly sales.xlsx' in visible_text(app.window)
            assert app.window.get_width()<=960
            app.send();phase=2
        elif phase==2 and app.worker and not app.worker.is_alive():
            assert app.store.tasks(app.cid)[0]['status']=='complete'
            assert not app.pending_files and Path(file['path']).exists()
            assert source.read_bytes()==original
            app.render_history();phase=3
        elif phase==3 and capture(app.window,'result.png'):
            text=visible_text(app.window)
            assert 'Quarterly sales.xlsx' in text and 'West revenue is 12,000' in text
            result['spreadsheet_reaches_agent_without_changing_original']=True
            result['attachment_survives_history_reload']=True
            extra=app.attach_file_path(source)
            remove=next(w for w in walk(app.attachments) if isinstance(w,Gtk.Button) and w.get_tooltip_text()=='Remove Quarterly sales.xlsx')
            remove.emit('clicked');assert not Path(extra['path']).exists()
            result['remove_unsent_copy_preserves_original']=source.read_bytes()==original
            decision=[]
            detail=json.dumps({'server':'Sales account','name':'update_record','arguments':{'record_id':'Opportunity-0042','amount':1250.75,'currency':'USD','owner':'jane@example.com'}})
            review_thread=threading.Thread(target=lambda:decision.append(app.approve('mcp.call',detail,StopToken())))
            review_thread.start();phase=4
        elif phase==4:
            dialog=next((w for w in app.get_windows() if w.get_title()=='Review action'),None)
            if dialog and capture(dialog,'review.png'):
                text=visible_text(dialog)
                for term in ('Sales account','Opportunity-0042','1250.75','USD','jane@example.com'):assert term in text
                assert '"arguments"' not in text
                next(w for w in walk(dialog) if isinstance(w,Gtk.Button) and w.get_label()=='Skip action').emit('clicked')
                phase=5
        elif phase==5 and not review_thread.is_alive():
            assert decision==[False];result['readable_exact_review_and_deny_work']=True
            app.quit();return False
    except Exception as error:
        result['error']=repr(error);app.emergency('test failure');app.quit();return False
    return True

GLib.timeout_add(150,tick)
try:
    with patch('desktop_buddy.ui.Provider',FixtureProvider):app.run([])
finally:
    if app.worker:app.worker.join(5)
    app.mcp.close();app.store.close();profile.cleanup()
(root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if 'error' in result:raise SystemExit(1)
