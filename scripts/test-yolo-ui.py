#!/usr/bin/env python3
"""Exercise a real approval dialog -> YOLO -> subsequent task without prompts."""
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import gi
gi.require_version('Gtk','4.0');gi.require_version('Graphene','1.0')
from gi.repository import Gtk,GLib,Graphene
from desktop_buddy.ui import BuddyApp
from desktop_buddy.state import Store
from desktop_buddy.security import Redactor
from test_core import ScriptedProvider,tool_answer,final

root=Path(__file__).resolve().parents[1]/'.test-data'/'yolo-ui';root.mkdir(parents=True,exist_ok=True)
profile=tempfile.TemporaryDirectory(dir=root)
app=BuddyApp(Path(profile.name)/'state')
work=Path(profile.name)/'work';work.mkdir()
app.store.set('workspace',str(work));app.store.set('provider','openai');app.store.set('local_only',False)
app.store.set('mode','execute');app.store.set('window_width',760);app.store.set('window_height',640)
c=app.store.configs()['openai'];c.model='fixture';app.store.save_config(c)
app.credentials.get=Mock(return_value='synthetic-key')
providers=[]
def provider(config,*_):
    instance=ScriptedProvider([tool_answer('app.launch',{'desktop_id':'fixture.desktop'},'1'),
        tool_answer('fs.write',{'path':'proof','content':'YOLO worked'},'2'),final('Done.')])
    instance.config=config
    original=instance.complete
    instance.complete=Mock(side_effect=original)
    providers.append(instance)
    return instance

def widgets(w):
    yield w
    child=w.get_first_child()
    while child:
        yield from widgets(child);child=child.get_next_sibling()

def capture(window,name):
    paintable=Gtk.WidgetPaintable.new(window);snapshot=Gtk.Snapshot.new()
    paintable.snapshot(snapshot,float(window.get_width()),float(window.get_height()))
    node=snapshot.to_node()
    if node is None:return
    rect=Graphene.Rect();rect.init(0,0,window.get_width(),window.get_height())
    window.get_renderer().render_texture(node,rect).save_to_png(str(root/name))

phase=0;result={};started=time.monotonic()
def tick():
    global phase
    try:
        if time.monotonic()-started>15:raise TimeoutError(f'phase {phase}')
        if phase==0:
            app.send('Open the test app and write proof');phase=1
        elif phase==1 and app.pending:
            dialog=app.pending[0][1]
            capture(dialog,'approval.png')
            next(w for w in widgets(dialog) if isinstance(w,Gtk.Button) and w.get_label()=='Use YOLO for this and future tasks').emit('clicked')
            assert not app.start_token.event.is_set()
            assert app.selected_mode()=='yolo'
            assert app.store.get('mode')=='yolo'
            result['pending_action_enables_yolo_without_cancelling']=True
            phase=2
        elif phase==2 and not app.worker.is_alive():
            assert app.store.tasks(app.cid)[0]['status']=='complete'
            assert (work/'proof').read_text()=='YOLO worked'
            assert 'Mode: yolo.' in providers[0].complete.call_args.args[0][0]['content']
            assert launch.call_count==1
            assert not app.pending
            app.approve=Mock(side_effect=AssertionError('YOLO prompted again'))
            app.new_conversation();app.send('Do it again');phase=3
        elif phase==3 and not app.worker.is_alive():
            assert app.store.tasks(app.cid)[0]['status']=='complete'
            app.approve.assert_not_called()
            assert launch.call_count==2
            assert app.window.get_width()<=760
            assert 'YOLO is on' in app.disclosure.get_text()
            capture(app.window,'yolo.png')
            result['subsequent_task_skips_cloud_and_action_approvals']=True
            result['compact_layout']=True
            app.quit();return False
    except Exception as exc:
        result['error']=repr(exc);app.quit();return False
    return True

with patch('desktop_buddy.ui.Provider',side_effect=provider), patch('desktop_buddy.tools.Registry.launch',return_value={'launched':True,'verified':False}) as launch:
    GLib.timeout_add(250,tick)
    app.run([])
app.store.close()
restored=Store(Path(profile.name)/'state',Redactor())
try:
    assert restored.get('mode')=='yolo'
    result['mode_survives_reopen']=True
finally:restored.close();profile.cleanup()
(root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if 'error' in result:raise SystemExit(1)
