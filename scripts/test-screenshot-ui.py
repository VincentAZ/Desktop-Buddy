#!/usr/bin/env python3
"""Real GTK checks for the screenshot card, the full-window viewer and the resizable message box."""
import json
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0');gi.require_version('Gdk','4.0');gi.require_version('Graphene','1.0');gi.require_version('GdkPixbuf','2.0')
from gi.repository import Gdk,GdkPixbuf,Gtk,GLib,Graphene
from desktop_buddy.ui import BuddyApp
from desktop_buddy.ui_widgets import INPUT_HEIGHT
from desktop_buddy.themes import STYLES as CSS

root=Path(__file__).resolve().parents[1]/'.test-data'/'screenshot-ui'
root.mkdir(parents=True,exist_ok=True)
profile=tempfile.TemporaryDirectory(dir=root)
shot=Path(profile.name)/'observation.png'
# A wide screen-sized picture: big enough to widen any card that does not scale it down.
pixbuf=GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB,False,8,2560,1440);pixbuf.fill(0x2b3a44ff);pixbuf.savev(str(shot),'png',[],[])
app=BuddyApp(Path(profile.name)/'state')
app.store.set('window_width',1180);app.store.set('window_height',860)
c=app.store.configs()['openai'];c.model='gpt-6.1-sol';app.store.save_config(c)
app.store.set('provider','openai')
result={};phase=0;started=time.monotonic()

def capture(name):
    paintable=Gtk.WidgetPaintable.new(app.window);snapshot=Gtk.Snapshot.new()
    paintable.snapshot(snapshot,float(app.window.get_width()),float(app.window.get_height()))
    rect=Graphene.Rect();rect.init(0,0,app.window.get_width(),app.window.get_height())
    app.window.get_renderer().render_texture(snapshot.to_node(),rect).save_to_png(str(root/name))

def tick():
    global phase,grown,initial_height
    try:
        if time.monotonic()-started>20:raise TimeoutError(f'phase {phase}')
        if phase==0:
            initial_height=app.window.get_height()  # Window decorations differ on X11 and Wayland.
            for i in range(5):
                app.store.add_message(app.cid,{'role':'user','content':'Open Resolve please'})
                app.store.add_message(app.cid,{'role':'assistant','content':'I opened DaVinci Resolve. Some steps could not be confirmed. Choose Continue task to check what happened before retrying.'})
            app.render_history()
            app.on_event('screenshot',str(shot))
            app.on_event('done',{'status':'needs_review'})
            assert 'scrolledwindow > overshoot, scrolledwindow > undershoot { background: none' in CSS
            result['end_of_list_line_removed']=True
        elif phase==2:
            capture('screenshot-card.png')
            assert app.preview_box.get_visible() and app.preview_box.get_width()<500,app.preview_box.get_width()
            assert 'click to see it bigger' in app.preview_time.get_text()
            result['screenshot_card_is_small_and_labelled']=app.preview_box.get_width()
            app.preview_box.emit('clicked')
            assert app.lightbox.widget.get_visible()
        elif phase==3:
            capture('lightbox.png')
            assert app.lightbox.picture.get_width()>900,app.lightbox.picture.get_width()
            assert app.lightbox.caption.get_text()=='Buddy’s latest look at your screen'
            assert app.lightbox.key(None,Gdk.KEY_Escape) and not app.lightbox.widget.get_visible()
            result['lightbox_fills_window_and_esc_closes']=True
            assert app.input_scroll.get_height()<=INPUT_HEIGHT+2,app.input_scroll.get_height()
            app.input.get_buffer().set_text('\n'.join(f'Line {i} of a long request' for i in range(12)))
        elif phase==4:
            grown=app.input_scroll.get_height()
            assert grown>INPUT_HEIGHT+60,grown
            result['message_box_grows_with_text']=grown
            app.input.get_buffer().set_text('')
            app.input_height=320;app.apply_input_height()
        elif phase==5:
            capture('tall-input.png')
            assert app.window.get_height()==initial_height,app.window.get_height()
            assert app.input_scroll.get_height()>=200,app.input_scroll.get_height()
            assert app.transcript_scroll.get_height()>=140,app.transcript_scroll.get_height()
            result['message_box_drags_taller']=app.input_scroll.get_height()
            app.input_height=5000;app.apply_input_height()
        elif phase==6:
            assert app.window.get_height()==initial_height,app.window.get_height()
            assert app.transcript_scroll.get_height()>=140,app.transcript_scroll.get_height()
            result['huge_drag_never_grows_window_or_hides_conversation']=[app.input_scroll.get_height(),app.transcript_scroll.get_height()]
            app.input_height=INPUT_HEIGHT;app.apply_input_height()
            app.new_conversation()
            assert not app.preview_box.get_visible()
            result['new_conversation_hides_old_screenshot']=True
            app.quit();return False
        phase+=1
    except Exception as exc:
        result['error']=repr(exc);app.quit();return False
    return True

GLib.timeout_add(350,tick)
try:app.run([])
finally:app.store.close();profile.cleanup()
(root/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
if 'error' in result:raise SystemExit(1)
