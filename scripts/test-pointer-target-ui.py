#!/usr/bin/env python3
"""Nonfocusable GTK canvas: pointer guard and actual keys on a private X display.

Keyboard delivery uses XTest only inside the separately created test display.
Production GNOME portal delivery and approval focus restoration are not tested.
"""
import ctypes
import ctypes.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

if '--target' in sys.argv:
    import gi
    gi.require_version('Gtk','4.0')
    from gi.repository import Gtk,Gio,GLib,Gdk
    root=Path(sys.argv[-1]);title=root.name
    app=Gtk.Application(application_id='io.desktopbuddy.PointerTarget',flags=Gio.ApplicationFlags.NON_UNIQUE)
    def activate(_):
        window=Gtk.ApplicationWindow(application=app,title=title,default_width=420,default_height=180)
        canvas=Gtk.DrawingArea();canvas.set_focusable(False)
        canvas.update_property([Gtk.AccessibleProperty.LABEL],['Pointer-only canvas'])
        received={'text':'','saved':False}
        controller=Gtk.EventControllerKey();controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        def pressed(_controller,keyval,_keycode,modifiers):
            if keyval==Gdk.KEY_F6:
                other=Gtk.ApplicationWindow(application=app,title='Other test window',default_width=260,default_height=100)
                field=Gtk.Entry();field.connect('changed',lambda entry:received.update(other_text=entry.get_text()))
                other.set_child(field);other.present();field.grab_focus();received['other_text']=''
                return True
            if modifiers&Gdk.ModifierType.CONTROL_MASK and keyval==Gdk.KEY_s:
                received['saved']=True;return True
            character=Gdk.keyval_to_unicode(keyval)
            if character and not modifiers&Gdk.ModifierType.CONTROL_MASK:
                received['text']+=chr(character);return True
            return False
        controller.connect('key-pressed',pressed);window.add_controller(controller)
        window.set_child(canvas);window.present()
        def record():
            value={'active':window.is_active(),'focusable':canvas.get_focusable(),**received}
            path=root/'target.tmp';path.write_text(json.dumps(value));path.replace(root/'target.json')
            return True
        GLib.timeout_add(50,record)
    app.connect('activate',activate);app.run([]);raise SystemExit

if '--isolated' not in sys.argv:
    environment=dict(os.environ,GDK_BACKEND='x11',XDG_SESSION_TYPE='x11')
    environment['BUDDY_TEST_ORIGINAL_DISPLAY']=os.environ.get('DISPLAY','')
    environment.pop('WAYLAND_DISPLAY',None)
    raise SystemExit(subprocess.run(['xvfb-run','-a',sys.executable,__file__,'--isolated'],env=environment,timeout=30).returncode)

if ('BUDDY_TEST_ORIGINAL_DISPLAY' not in os.environ or not os.environ.get('DISPLAY')
        or os.environ['DISPLAY']==os.environ['BUDDY_TEST_ORIGINAL_DISPLAY'] or os.environ.get('WAYLAND_DISPLAY')):
    raise SystemExit('Run this script without --isolated; it creates its own private display.')

from desktop_buddy.desktop import Accessibility,Portal
from desktop_buddy.security import Policy,Redactor,StopToken
from desktop_buddy.state import Store
from desktop_buddy.tools import Registry

parent=ROOT/'.test-data'/'pointer-target';parent.mkdir(parents=True,exist_ok=True)
root=Path(tempfile.mkdtemp(prefix='pointer-target-',dir=parent));work=root/'work';work.mkdir()
print('Evidence: '+str(root/'result.json'),flush=True)
report={'scope':'Separate native GTK canvas on a private X display. Real AT-SPI targeting; pointer dispatch stubbed, keys delivered with XTest to this private display only. No personal app input or GNOME consent. Production portal delivery and approval restoration remain unverified.'}
child=subprocess.Popen([sys.executable,__file__,'--target',str(root)])
store=Store(root/'state',Redactor());token=StopToken()

def state():
    try:return json.loads((root/'target.json').read_text())
    except (OSError,ValueError):return {}

class DispatchProbe:
    def screen_input(self,arguments):return dict(arguments)
    def key(self,*_):raise AssertionError('An already active canvas must not need a window switch')
    def input(self,arguments,token,verify_target):
        verify_target()
        assert state().get('active'), 'Wrong active window'
        report['nonfocusable_canvas_reached_dispatch']=True
        return {'sent':False,'verified':False}

class PrivateKeyboard(Portal):
    def __init__(self):
        self.session='private-xvfb';self.events=0
        xlib=ctypes.util.find_library('X11');xtst=ctypes.util.find_library('Xtst')
        if not xlib or not xtst:raise RuntimeError('This private-display test requires libX11 and libXtst')
        self.x=ctypes.CDLL(xlib);self.xt=ctypes.CDLL(xtst)
        self.x.XOpenDisplay.argtypes=[ctypes.c_char_p];self.x.XOpenDisplay.restype=ctypes.c_void_p
        self.x.XKeysymToKeycode.argtypes=[ctypes.c_void_p,ctypes.c_ulong];self.x.XKeysymToKeycode.restype=ctypes.c_uint
        self.x.XFlush.argtypes=[ctypes.c_void_p];self.x.XCloseDisplay.argtypes=[ctypes.c_void_p]
        self.xt.XTestFakeKeyEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
        self.display=self.x.XOpenDisplay(os.environ['DISPLAY'].encode())
        if not self.display:raise RuntimeError('Private X display unavailable')
    def screen_input(self,args):return dict(args)
    def check_unlocked(self):pass
    def notify(self,method,signature,args,token):
        token.check();self.deliver(method,args)
    def call(self,interface,method,signature,args,path=None):self.deliver(method,args[-2:])
    def deliver(self,method,args):
        assert method=='NotifyKeyboardKeysym',method
        keycode=self.x.XKeysymToKeycode(self.display,args[0]);assert keycode
        assert self.xt.XTestFakeKeyEvent(self.display,keycode,args[1],0)
        self.events+=1
        self.x.XFlush(self.display)
    def close(self):self.x.XCloseDisplay(self.display)

keyboard=None
try:
    deadline=time.monotonic()+8
    while not state().get('active'):
        if time.monotonic()>deadline:raise TimeoutError('The isolated GTK target did not become active')
        token.wait(.05)
    assert state()['focusable'] is False
    a11y=Accessibility();node=None
    while time.monotonic()<deadline:
        observed=a11y.inspect(window=root.name,depth=8,limit=200)
        node=next((n for n in observed['nodes'] if n['name']==root.name and n['role']=='frame'),None)
        if node:break
        token.wait(.05)
    assert node is not None, 'Canvas window was not accessible'
    obj=a11y.node(node['path'])
    assert obj.get_application().get_process_id()==child.pid, 'Wrong process'
    try:focused=obj.get_component_iface().grab_focus()
    except Exception as error:
        focused=False;report['focus_rejection']=type(error).__name__
    assert not focused, 'Fixture window must reject keyboard focus'
    assert not obj.get_state_set().contains(a11y.A.StateType.FOCUSED)
    box=node['bounds'];report['observed_canvas']=node
    registry=Registry(Policy(work,'execute',False,lambda *_:True),store,
                      desktop=DispatchProbe(),accessibility=a11y)
    registry.execute('desktop.input',{'action':'click','target_path':node['path'],
        'expected_name':node['name'],'expected_window':root.name,
        'x':box['x']+box['width']/2,'y':box['y']+box['height']/2},token)
    keyboard=PrivateKeyboard();registry.desktop=keyboard
    destination={'target_path':node['path'],'expected_name':node['name'],
                 'expected_window':root.name,'keyboard_scope':'window'}
    registry.execute('desktop.input',{**destination,'action':'type','text':'canvas'},token)
    registry.execute('desktop.input',{**destination,'action':'key','keys':'Ctrl+s'},token)
    deadline=time.monotonic()+3
    while state().get('text')!='canvas' or not state().get('saved'):
        if time.monotonic()>deadline:raise AssertionError('Canvas did not receive text and shortcut: '+repr(state()))
        token.wait(.05)
    report['canvas_keyboard_readback']=state()
    registry.execute('desktop.input',{**destination,'action':'key','keys':'F6'},token)
    deadline=time.monotonic()+3
    while state().get('active') or 'other_text' not in state():
        if time.monotonic()>deadline:raise AssertionError('Other test window did not take focus')
        token.wait(.05)
    before=keyboard.events
    try:registry.execute('desktop.input',{**destination,'action':'type','text':'wrong window'},token)
    except Exception as error:
        from desktop_buddy.desktop import DesktopError
        assert isinstance(error,DesktopError) and 'must be active' in str(error),repr(error)
    else:raise AssertionError('Typing into the inactive canvas was allowed')
    assert keyboard.events==before and state()['other_text']=='',state()
    report['different_window_received_no_input']=True
    report['status']='passed'
except Exception as error:
    report.update(status='failed',error=repr(error),traceback=traceback.format_exc())
finally:
    if keyboard:keyboard.close()
    token.stop();child.terminate();child.wait(5);store.close()
    (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
raise SystemExit(0 if report['status']=='passed' else 1)
