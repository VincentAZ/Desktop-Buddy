#!/usr/bin/env python3
"""Native approval focus check; physical dispatch is a stub, no portal input.

The target runs in a separate GTK process and reports actual window activation.
An approval may proceed only after that target regains focus.
"""
import json,os,subprocess,sys,tempfile,threading,time,traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,Gio,GLib


def target(root):
    app=Gtk.Application(application_id='io.desktopbuddy.FocusTarget',flags=Gio.ApplicationFlags.NON_UNIQUE)
    def activate(_):
        window=Gtk.ApplicationWindow(application=app,title='Buddy approval focus target',default_width=460,default_height=180)
        entry=Gtk.Entry();entry.update_property([Gtk.AccessibleProperty.LABEL],['Approved target entry'])
        if '--truncated-text' in sys.argv:entry.set_max_length(5)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
        for side in ('top','bottom','start','end'):getattr(box,'set_margin_'+side)(24)
        box.append(Gtk.Label(label='Harmless approval focus test'));box.append(entry);window.set_child(box)
        def state():
            value={'active':window.is_active(),'focused':window.get_focus() is not None and
                   (window.get_focus()==entry or window.get_focus().is_ancestor(entry))}
            temp=root/'target.tmp';temp.write_text(json.dumps(value));temp.replace(root/'target.json')
            return True
        window.present();entry.grab_focus();GLib.timeout_add(30,state)
    app.connect('activate',activate);app.run([])


if '--target' in sys.argv:
    target(Path(sys.argv[-1]));raise SystemExit

from desktop_buddy.ui import BuddyApp
from desktop_buddy.desktop import Accessibility
from desktop_buddy.security import Policy,StopToken
from desktop_buddy.tools import Registry
from desktop_buddy.diagnostics import screen_activity_probe

parent=Path(__file__).resolve().parents[1]/'.test-data'/'approval-focus';parent.mkdir(parents=True,exist_ok=True)
root=Path(tempfile.mkdtemp(prefix='run-',dir=parent));app=BuddyApp(root/'state')
edit_text='--edit-text' in sys.argv
truncated_text='--truncated-text' in sys.argv
live_model='--live-model' in sys.argv
work=root/'work';work.mkdir();result={};started=time.monotonic();child=None;worker=None;phase=0
token=StopToken();done=threading.Event()

def target_state():
    try:return json.loads((root/'target.json').read_text())
    except (OSError,ValueError):return {}

class DispatchProbe:
    def screen_input(self,arguments):return dict(arguments)
    def key(self,*_):raise AssertionError('The app requires compositor focus restoration. This no-input fixture cannot verify the portal Alt+Tab fallback.')
    def input(self,arguments,token,verify_target=None):
        if edit_text:raise AssertionError('The direct-entry check must not dispatch physical input')
        verify_target()
        deadline=time.monotonic()+2
        while not target_state().get('active') or not target_state().get('focused'):
            token.wait(.05)
            if time.monotonic()>deadline:raise AssertionError('Physical input would reach a different window')
        result['target_active_and_focused_at_dispatch']=True
        return {'sent':False,'verified':False,'note':'Focus verified; physical dispatch deliberately stubbed.'}

def run_action():
    try:
        a11y=Accessibility();deadline=time.monotonic()+5;entry=None
        while time.monotonic()<deadline:
            tree=a11y.inspect(window='Buddy approval focus target',depth=8,limit=200)
            entry=next((n for n in tree['nodes'] if n['name']=='Approved target entry'),None)
            if entry:break
            token.wait(.1)
        if not entry:raise AssertionError('Target entry was not accessible')
        approve=(lambda name,detail,token:app.approve(name,detail,token) if name=='desktop.set_text' else False) if edit_text else app.approve
        registry=Registry(Policy(work,'execute',False,approve),app.store,desktop=DispatchProbe(),accessibility=a11y)
        arguments={'text':'Verified direct entry ✓','target_path':entry['path'],'expected_name':entry['name'],'expected_window':'Buddy approval focus target'}
        if edit_text:
            if live_model:
                from desktop_buddy.agent import Agent
                from desktop_buddy.providers import Provider
                from desktop_buddy.state import ProviderConfig
                config=ProviderConfig('lmstudio','http://127.0.0.1:1234/v1','chat',True,
                    model='qwen/qwen3-vl-8b',context_tokens=16384,max_output=2048,tools=True,vision=False)
                app.store.save_config(config);app.store.set('provider','lmstudio')
                app.agent=Agent(app.store,registry,Provider(config),max_rounds=8)
                app.agent.token=token
                cid=app.store.conversation('Native app text acceptance')
                app.agent.run(cid,'In the app titled Buddy approval focus target, replace the field labeled Approved target entry with exactly: Verified direct entry ✓. Read it back before you finish. Use accessible controls; no physical typing or clicking is needed.')
                (root/'messages.json').write_text(json.dumps(app.store.messages(cid),indent=2))
                if app.store.tasks(cid)[0]['status']!='complete':raise AssertionError('Live agent task did not complete')
                result['live_local_model_completed_task']=True
            else:
                outcome=registry.execute('desktop.set_text',arguments,token)
                if truncated_text:
                    from desktop_buddy.agent import tool_failed
                    if outcome['verified'] or not tool_failed('desktop.set_text',outcome):
                        raise AssertionError('A clipped field must not be treated as a successful text entry')
                    result['rejected_text_detected']=True
                elif not outcome['verified']:raise AssertionError('Direct entry readback did not match')
            observed=a11y.inspect(window='Buddy approval focus target',depth=8,limit=200)
            expected=arguments['text'][:5] if truncated_text else arguments['text']
            if not any(node.get('text')==expected for node in observed['nodes']):
                raise AssertionError('Changed text was not readable through the accessibility tree')
            result['clipped_text_readback_verified' if truncated_text else 'direct_text_readback_verified']=True
        else:
            registry.execute('desktop.input',{**arguments,'action':'type'},token)
            result['approval_returned_to_exact_target']=True
    except Exception as error:result.update(error=repr(error),traceback=traceback.format_exc())
    finally:done.set()

def widgets(w):
    yield w
    child=w.get_first_child()
    while child:yield from widgets(child);child=child.get_next_sibling()

def tick():
    global phase,child,worker
    try:
        if time.monotonic()-started>25:raise TimeoutError(f'phase {phase}')
        if not edit_text and screen_activity_probe()['status']=='active':raise RuntimeError('GNOME reports an active screen saver; focus acceptance cannot run. Focus the desktop normally and retry.')
        if phase==0:
            child=subprocess.Popen([sys.executable,__file__,*(['--truncated-text'] if truncated_text else []),'--target',str(root)]);phase=1
        elif phase==1 and (edit_text and target_state() or target_state().get('active') and target_state().get('focused')):
            result['target_focused_before_approval']=bool(target_state().get('active') and target_state().get('focused'))
            worker=threading.Thread(target=run_action);worker.start();phase=2
        elif phase==2 and app.pending and (edit_text or not target_state().get('active') and app.pending[0][1].is_active()):
            dialog=app.pending[0][1]
            result['approval_displayed']=True
            result['approval_window_active']=dialog.is_active()
            next(w for w in widgets(dialog) if isinstance(w,Gtk.Button) and w.get_label()=='Allow once').emit('clicked');phase=3
        if done.is_set():app.quit();return False
    except Exception as error:
        result.update(error=repr(error),traceback=traceback.format_exc());token.stop();app.quit();return False
    return True

GLib.timeout_add(100,tick)
try:app.run([])
finally:
    token.stop()
    if worker:worker.join(5)
    if child:child.terminate();child.wait(5)
    app.mcp.close();app.store.close()
(root/'result.json').write_text(json.dumps(result,indent=2));print(root/'result.json');print(json.dumps(result,indent=2))
if result.get('error') or not result.get('clipped_text_readback_verified' if truncated_text else 'direct_text_readback_verified' if edit_text else 'approval_returned_to_exact_target'):raise SystemExit(1)
