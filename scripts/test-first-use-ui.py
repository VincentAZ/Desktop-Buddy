#!/usr/bin/env python3
"""Fresh Jane profile → visible IT setup → saved assistant, through native controls."""
from pathlib import Path
import json,sys,tempfile,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0');gi.require_version('Graphene','1.0')
from gi.repository import Gtk,GLib,Graphene
from desktop_buddy.ui import BuddyApp,content

root=Path(__file__).resolve().parents[1]/'.test-data'/'first-use';root.mkdir(parents=True,exist_ok=True)
profile=tempfile.TemporaryDirectory(dir=root);app=BuddyApp(Path(profile.name)/'state')
app.store.set('window_width',940);app.store.set('window_height',700)
phase=0;result={};started=time.monotonic()

def walk(widget):
    yield widget
    child=widget.get_first_child()
    while child:
        yield from walk(child);child=child.get_next_sibling()

def text(window):return '\n'.join(w.get_text() for w in walk(window) if isinstance(w,Gtk.Label) and w.get_mapped())

def click(window,title):next(w for w in walk(window) if isinstance(w,Gtk.Button) and w.get_label()==title).emit('clicked')

def capture(window,name):
    paintable=Gtk.WidgetPaintable.new(window);snapshot=Gtk.Snapshot.new()
    paintable.snapshot(snapshot,float(window.get_width()),float(window.get_height()));node=snapshot.to_node()
    if node is None:return False
    rect=Graphene.Rect();rect.init(0,0,window.get_width(),window.get_height())
    window.get_renderer().render_texture(node,rect).save_to_png(str(root/name));return True

def tick():
    global phase,settings,setup,help_window
    try:
        if time.monotonic()-started>25:raise TimeoutError(f'phase {phase}')
        if phase==0 and capture(app.window,'before-it-setup.png'):
            assert 'Choose an assistant' in text(app.window)
            assert not app.run_button.get_sensitive()
            assert 'Ready when you are' not in text(app.window)
            assert app.model_menu.button.get_mapped()
            app.input.get_buffer().set_text('Help with my quarterly report')
            click(app.window,'Set up the assistant');settings=app.preferences_window.controller;phase=1
        elif phase==1 and capture(settings.window,'setup-status.png'):
            # The welcome button lands straight on IT setup, where the assistant is chosen.
            assert settings.it_setup
            assert 'API key' not in text(settings.window)
            assert 'IT setup' in text(settings.window)
            settings.it_button.emit('clicked');assert not settings.it_setup
            help_window=settings.show_help();phase=11
        elif phase==11 and capture(help_window,'offline-help.png'):
            assert 'Start using Buddy' in text(help_window)
            assert 'no Desktop Buddy account or login' in text(help_window)
            assert not app.worker
            help_window.destroy()
            settings.it_button.emit('clicked');phase=2
        elif phase==2 and capture(settings.window,'it-setup.png'):
            assert app.model_menu.button.get_mapped()
            click(settings.window,'Add or configure a vendor…')
            setup=next(w.controller for w in app.get_windows() if w.get_title()=='Advanced assistant setup');phase=3
        elif phase==3:
            setup.provider.set_selected(app.provider_ids.index('ollama'))
            setup.model.set_text('it-fixture-assistant')
            setup.use_button.emit('clicked');phase=4
        elif phase==4 and setup.closed:
            assert app.selected_id()=='ollama'
            assert app.store.configs()['ollama'].model=='it-fixture-assistant'
            assert app.run_button.get_sensitive()
            assert content(app.input)=='Help with my quarterly report'
            assert not app.store.messages(app.cid)
            settings.close();phase=5
        elif phase==5 and capture(app.window,'after-it-setup.png'):
            assert 'it-fixture-assistant' in text(app.window)
            assert 'Ollama · Default thinking' in text(app.window)
            assert 'Ready when you are' in text(app.window)
            assert app.model_menu.button.get_mapped()
            result.update(offline_help_available_before_model_setup=True,
                          jane_sees_it_setup_needed=True,send_waits_for_assistant_configuration=True,
                          it_can_choose_vendor_and_model_in_app=True,draft_preserved_without_sending=True,
                          normal_ui_hides_vendor_configuration=True,live_service_verified=False)
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
