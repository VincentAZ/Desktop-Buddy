#!/usr/bin/env python3
"""Real GTK regressions for drafts, startup failures, recovery and small layouts."""
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0');gi.require_version('Graphene','1.0')
from gi.repository import Gtk,GLib,Graphene
from desktop_buddy.ui import BuddyApp,content

root=Path(__file__).resolve().parents[1]/'.test-data'/'redesign'
root.mkdir(parents=True,exist_ok=True)
profile=tempfile.TemporaryDirectory(dir=root)
app=BuddyApp(Path(profile.name)/'state')
app.store.set('window_width',760);app.store.set('window_height',640)
c=app.store.configs()['openai'];c.model='gpt-6.1-sol';app.store.save_config(c)
app.store.set('provider','openai');app.store.set('local_only',False)
app.credentials.get=Mock(side_effect=RuntimeError('Test keyring unavailable'))
result={};phase=0;started=time.monotonic()

def capture(name):
    paintable=Gtk.WidgetPaintable.new(app.window);snapshot=Gtk.Snapshot.new()
    paintable.snapshot(snapshot,float(app.window.get_width()),float(app.window.get_height()))
    node=snapshot.to_node()
    assert node is not None
    rect=Graphene.Rect();rect.init(0,0,app.window.get_width(),app.window.get_height())
    app.window.get_renderer().render_texture(node,rect).save_to_png(str(root/name))

def select(cid):
    row=app.history.get_first_child()
    while row and row.cid!=cid:row=row.get_next_sibling()
    assert row is not None
    app.history.select_row(row)

def tick():
    global phase,first,second
    try:
        if time.monotonic()-started>20:raise TimeoutError(f'phase {phase}')
        if phase==0:
            capture('welcome-760.png')
            assert app.window.get_width()<=760,app.window.get_width()
            assert app.mode.get_selected()==1
            desktop=app.desktop_button
            assert desktop.get_accessible_role()==Gtk.AccessibleRole.BUTTON
            assert desktop.get_child().get_first_child().get_icon_name()=='video-display-symbolic'
            result['desktop_control_is_in_sidebar']=True
            first=app.cid
            app.input.get_buffer().set_text('Draft for first conversation')
            app.new_conversation();second=app.cid
            assert content(app.input)==''
            app.input.get_buffer().set_text('Draft for second conversation')
            select(first)
            assert content(app.input)=='Draft for first conversation'
            select(second)
            assert content(app.input)=='Draft for second conversation'
            result['drafts_belong_to_conversations']=True
            app.input.get_buffer().set_text('Keep my message even when startup fails')
            app.send();phase=1
        elif phase==1 and not app.worker.is_alive():
            messages=app.store.messages(second)
            assert len(messages)==1 and messages[0]['content']=='Keep my message even when startup fails'
            assert app.store.tasks(second)[0]['status']=='failed'
            assert app.recovery_button.get_visible()
            assert app.run_button.get_sensitive() and not app.spinner.get_spinning()
            result['startup_failure_preserves_message_and_recovers_controls']=True
            app.render_history()
            assert app.recovery_button.get_visible()
            phase=2
        elif phase==2:
            capture('recoverable-error.png')
            app.new_conversation()
            assert app.status.get_text()=='Ready when you are'
            assert not app.recovery_button.get_visible()
            result['new_conversation_clears_failed_status']=True
            app.store.add_message(app.cid,{'role':'user','content':'An existing conversation'})
            app.render_history()
            app.input.get_buffer().set_text('A draft to delete')
            app.save_draft();deleted=app.cid
            dialog=app.delete_conversation_dialog()
            def widgets(w):
                yield w
                child=w.get_first_child()
                while child:
                    yield from widgets(child);child=child.get_next_sibling()
            next(w for w in widgets(dialog) if isinstance(w,Gtk.Button) and w.get_label()=='Delete conversation').emit('clicked')
            assert deleted not in app.store.get('drafts',{})
            assert content(app.input)!='A draft to delete'
            result['delete_clears_unsent_draft']=True
            app.window.set_default_size(1180,860)
            app.new_conversation();phase=3
        elif phase==3:
            capture('welcome-1180.png')
            result['sizes']=[760,1180]
            app.quit();return False
    except Exception as exc:
        result['error']=repr(exc);app.quit();return False
    return True

GLib.timeout_add(350,tick)
try:app.run([])
finally:app.store.close();profile.cleanup()
(root/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
if 'error' in result:raise SystemExit(1)
