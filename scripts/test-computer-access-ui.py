#!/usr/bin/env python3
"""Native permission-status UI against a controlled peer; never requests OS access."""
import json,sys,tempfile,threading,time,traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0');gi.require_version('Graphene','1.0')
from gi.repository import Gtk,GLib,Graphene
from desktop_buddy.ui import BuddyApp
from desktop_buddy.desktop import DesktopError

root=Path(__file__).resolve().parents[1]/'.test-data/computer-access-ui'
root.mkdir(parents=True,exist_ok=True)
profile=tempfile.TemporaryDirectory(dir=root)
app=BuddyApp(Path(profile.name)/'state')

class Peer:
    def __init__(self):self.release=threading.Event();self.calls=0;self.outcome='success';self.session=None
    def connect(self,token):
        self.calls+=1
        while not self.release.wait(.01):token.check()
        token.check()
        if self.outcome=='failure':raise DesktopError('RemoteDesktop.Start: internal test failure')
        self.session='/fixture/granted'
        if self.outcome=='cancel_at_grant':token.stop()
        return {'connected':True,'remembered':True,'logical_size':[1920,1080]}
    def close(self):self.session=None

peer=Peer();app.desktop=peer;app.ensure_backends=lambda:None
phase=0;started=time.monotonic();result={'scope':'UI lifecycle only; OS permissions are not requested'}

def capture(window,name):
    paintable=Gtk.WidgetPaintable.new(window);snapshot=Gtk.Snapshot.new()
    paintable.snapshot(snapshot,float(window.get_width()),float(window.get_height()))
    node=snapshot.to_node()
    if node is None:return False
    rect=Graphene.Rect();rect.init(0,0,window.get_width(),window.get_height())
    window.get_renderer().render_texture(node,rect).save_to_png(str(root/name));return True

def tick():
    global phase,dialog
    try:
        if time.monotonic()-started>25:raise TimeoutError(f'phase {phase}')
        if phase==0:
            dialog=app.connect_desktop()
            assert app.connect_desktop() is dialog
            assert 'ready' not in dialog.heading.get_text().lower()
            phase=1
        elif phase==1 and peer.calls==1 and capture(dialog.window,'waiting.png'):
            assert peer.session is None
            app.computer_access_progress('Checking that Buddy can see your shared screen…');phase=7
        elif phase==7 and 'Checking' in dialog.heading.get_text():
            assert dialog.pending and 'ready' not in dialog.heading.get_text().lower()
            assert 'don’t need' in dialog.status.get_text()
            result['frame_check_has_plain_progress']=True
            peer.release.set();phase=2
        elif phase==2 and not dialog.pending and capture(dialog.window,'ready.png'):
            assert peer.calls==1 and 'ready' in dialog.heading.get_text()
            dialog.done_button.emit('clicked');assert app.computer_access_dialog is None
            result['duplicate_clicks_make_one_request']=True
            peer.outcome='failure';dialog=app.connect_desktop();phase=3
        elif phase==3 and not dialog.pending and capture(dialog.window,'failed.png'):
            assert dialog.heading.get_text()=='Buddy couldn’t connect'
            assert 'RemoteDesktop' not in dialog.message.get_text()
            result['failure_has_plain_visible_outcome']=True
            dialog.done_button.emit('clicked');peer.release.clear()
            peer.outcome='success';dialog=app.connect_desktop();phase=4
        elif phase==4 and peer.calls==3:
            dialog.done_button.emit('clicked');phase=5
        elif phase==5 and not dialog.pending:
            assert dialog.heading.get_text()=='Connection cancelled'
            assert peer.session is None
            dialog.done_button.emit('clicked');peer.outcome='cancel_at_grant';peer.release.set()
            dialog=app.connect_desktop();phase=6
        elif phase==6 and not dialog.pending:
            assert dialog.heading.get_text()=='Connection cancelled'
            assert peer.session is None
            result['cancel_before_and_after_grant_closes_access']=True
            dialog.done_button.emit('clicked');app.quit();return False
    except Exception:
        result['error']=traceback.format_exc();app.quit();return False
    return True

GLib.timeout_add(120,tick)
try:app.run([])
finally:
    peer.release.set()
    for token in app.aux_tokens:token.stop()
    app.store.close();profile.cleanup()
(root/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
raise SystemExit(1 if 'error' in result else 0)
