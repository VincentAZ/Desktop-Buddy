#!/usr/bin/env python3
"""Exercise Jane's everyday settings without opening developer setup."""
from pathlib import Path
import sys,tempfile,time,json
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0');gi.require_version('Graphene','1.0')
from gi.repository import Gtk,GLib,Graphene
from desktop_buddy.ui import BuddyApp,content
from desktop_buddy.ui_widgets import activity_card

root=Path(__file__).resolve().parents[1]/'.test-data'/'jane';root.mkdir(parents=True,exist_ok=True)
profile=tempfile.TemporaryDirectory(dir=root);app=BuddyApp(Path(profile.name)/'state')
app.store.set('window_width',940);app.store.set('window_height',700)
c=app.store.configs()['openai'];c.model='fixture-internal-model-id';app.store.save_config(c)
app.store.set('provider','openai');app.store.set('local_only',False)
phase=0;started=time.monotonic();result={}

def walk(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from walk(child);child=child.get_next_sibling()

def mapped_text(window):
    return '\n'.join(w.get_text() for w in walk(window) if isinstance(w,Gtk.Label) and w.get_mapped())

def capture(window,name):
    paintable=Gtk.WidgetPaintable.new(window);snapshot=Gtk.Snapshot.new()
    paintable.snapshot(snapshot,float(window.get_width()),float(window.get_height()));node=snapshot.to_node()
    if node is None:return False
    rect=Graphene.Rect();rect.init(0,0,window.get_width(),window.get_height())
    window.get_renderer().render_texture(node,rect).save_to_png(str(root/name));return True

def tick():
    global phase,settings,memory,original
    try:
        if time.monotonic()-started>25:raise TimeoutError(f'Phase {phase}')
        if phase==0 and capture(app.window,'home.png'):
            text=mapped_text(app.window)
            assert not app.model_menu.button.get_mapped()
            assert 'fixture-internal-model-id' not in text
            assert 'YOLO' not in text
            assert 'Execution' not in text
            assert app.mode.get_selected_item().get_string()=='Ask before acting'
            assert app.model_menu.button.get_parent() is None
            settings=app.settings_dialog().controller;phase=1
        elif phase==10:
            assert app.model_menu.button.get_mapped()
            app.model_menu.button.popup();phase=11
        elif phase==11 and capture(app.model_menu.popover,'assistant.png'):
            menu=app.model_menu
            assert not menu.advanced.get_expanded()
            assert not menu.answering.get_expanded()
            assert not any(w.get_mapped() for w in walk(menu.box) if isinstance(w,Gtk.Button) and w.get_label()=='Add or set up a model…')
            menu.advanced.set_expanded(True);phase=12
        elif phase==12:
            assert any(w.get_mapped() for w in walk(app.model_menu.box) if isinstance(w,Gtk.Button) and w.get_label()=='Add or set up a model…')
            app.model_menu.popover.popdown()
            settings.close();assert app.model_menu.button.get_parent() is None
            result['it_can_open_assistant_switch_and_vendor_setup']=True
            for _ in range(8):app.append('RESULT','{"verified":true}')
            assert app.activity_group.count==8
            assert not app.activity_group.get_expanded()
            result['routine_steps_grouped_with_full_details_retained']=True
            app.append('ERROR','ConnectionRefusedError: fixture raw error /tmp/debug.py')
            phase=3
        elif phase==1 and capture(settings.window,'settings.png'):
            text=mapped_text(settings.window)
            for term in ('API key','Context limit','Model ID','fixture-internal-model-id'):
                assert term not in text,term
            assert not settings.it_setup
            assert 'Internet access' in text and 'Where Buddy saves your work' in text
            settings.offline.set_active(True);assert app.local.get_active();assert app.store.get('local_only')
            settings.offline.set_active(False);assert not app.local.get_active()
            settings.close();assert not app.model_menu.button.get_mapped()
            settings=app.settings_dialog().controller;phase=2
        elif phase==2:
            assert not settings.it_setup
            settings.it_button.emit('clicked');phase=10
        elif phase==3 and capture(app.window,'recovery.png'):
            text=mapped_text(app.window)
            assert 'ConnectionRefusedError' not in text
            assert 'fixture raw error' not in text
            result['main_window_hides_it_configuration']=True
            result['settings_privacy_and_reopen_work']=True
            result['errors_present_recovery_before_raw_details']=True
            app.on_event('done',{'status':'needs_review','task_id':'fixture','unread':False})
            assert app.recovery_button.get_visible()
            assert 'still need checking' in app.status.get_text()
            result['unresolved_steps_are_not_announced_as_finished']=True
            review_cid=app.cid
            tid=app.store.task(review_cid,{})
            app.store.checkpoint(tid,review_cid,'needs_review',{'unresolved_operations':['fixture']})
            app.cid=app.store.conversation('Unrelated conversation');app.render_history()
            assert not app.recovery_button.get_visible()
            app.cid=review_cid;app.render_history()
            assert app.recovery_button.get_visible()
            assert 'still need checking' in app.status.get_text()
            result['reopening_unresolved_conversation_keeps_continue_task']=True
            app.append('ERROR','invalid_api_key: synthetic authentication fixture')
            settings_button=next(w for w in walk(app.transcript) if isinstance(w,Gtk.Button) and w.get_label()=='Settings')
            settings_button.emit('clicked');assert app.preferences_window
            app.preferences_window.controller.close()
            result['error_recovery_opens_settings_directly']=True
            original={'Weekly report':{'Columns':['Region','Revenue'],'Include totals':True}}
            app.store.save_memory(original);memory=app.memory_dialog();phase=4
        elif phase==4 and capture(memory,'preferences.png'):
            editor=next(w for w in walk(memory) if isinstance(w,Gtk.TextView))
            value=content(editor);assert '• Region' in value and 'Include totals: Yes' in value
            assert '{' not in value and '"Columns"' not in value
            next(w for w in walk(memory) if isinstance(w,Gtk.Button) and w.get_label()=='Save changes').emit('clicked')
            assert app.store.memory()==original
            memory=app.memory_dialog();phase=5
        elif phase==5:
            editor=next(w for w in walk(memory) if isinstance(w,Gtk.TextView))
            editor.get_buffer().set_text('Show revenue by region, with a total.')
            next(w for w in walk(memory) if isinstance(w,Gtk.Button) and w.get_label()=='Save changes').emit('clicked')
            assert app.store.memory()=={'Weekly report':'Show revenue by region, with a total.'}
            result['structured_preferences_readable_preserved_and_editable_as_text']=True
            with patch('desktop_buddy.ui.Gio.AppInfo.launch_default_for_uri') as launch:
                app.open_work_folder()
                launch.assert_called_once_with(Path(app.workspace.get_text()).resolve().as_uri(),None)
            result['work_folder_opens_selected_location']=True
            app.quit();return False
    except Exception as error:
        import traceback
        result['error']=repr(error);result['phase']=phase;result['traceback']=traceback.format_exc();app.quit();return False
    return True

GLib.timeout_add(150,tick)
try:app.run([])
finally:app.mcp.close();app.store.close();profile.cleanup()
(root/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if 'error' in result:raise SystemExit(1)
