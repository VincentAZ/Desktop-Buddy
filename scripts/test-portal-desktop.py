#!/usr/bin/env python3
"""Physical Wayland acceptance with visible progress and fresh durable evidence.

Only types a fixed phrase and clicks this test's own button after GNOME consent.
No automatic consent, arbitrary input, configuration changes, or cloud calls.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkWayland', '4.0')
from gi.repository import Gtk, Gio, GLib, Gdk, GdkWayland
from desktop_buddy.desktop import Portal, Accessibility
from desktop_buddy.security import StopToken

PHRASE = 'Desktop Buddy physical input verified'


class PhysicalCheck:
    def __init__(self, control, input_only=False):
        self.control = control
        self.input_only=input_only
        self.parent = Path(__file__).resolve().parents[1] / '.test-data' / 'portal-desktop'
        self.parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='run-', dir=self.parent))
        self.started = time.monotonic()
        self.result = {'started_utc': datetime.now(timezone.utc).isoformat(),
                       'run_directory': str(self.root), 'status': 'starting', 'events': [],
                       'scope':'keyboard only; screen and mouse not tested' if input_only else 'screen capture, keyboard and mouse in the dedicated test window' if control else 'screen capture only'}
        self.lock = threading.RLock()
        self.token = StopToken()
        self.portal = None
        self.started_worker = False
        self.finished = False
        self.status_label = None
        self.app = Gtk.Application(application_id='io.desktopbuddy.PhysicalCheck',
                                   flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.app.connect('activate', self.activate)
        self.save()
        print('Result: ' + str(self.root / 'result.json'), flush=True)

    def save(self):
        with self.lock:
            text = json.dumps(self.result, indent=2) + '\n'
            for path in (self.root / 'result.json', self.parent / 'result.json'):
                temporary = path.with_suffix('.tmp')
                temporary.write_text(text)
                temporary.replace(path)

    def phase(self, text, user_text=None):
        with self.lock:
            self.result['phase'] = text
            self.result['events'].append({'seconds': round(time.monotonic() - self.started, 2), 'stage': text})
            self.save()
        print(text, flush=True)
        if self.status_label is not None and user_text is not None:
            GLib.idle_add(lambda: self.status_label.set_text(user_text))

    def on_ui(self, function):
        done = threading.Event()
        value = []
        def run():
            try: value.append((True, function()))
            except Exception as error: value.append((False, error))
            done.set()
            return False
        GLib.idle_add(run)
        if not done.wait(5): raise TimeoutError('The test window stopped responding.')
        if not value[0][0]: raise value[0][1]
        return value[0][1]

    def focused(self):
        focus = self.window.get_focus()
        return self.window.is_active() and focus is not None and (focus == self.entry or focus.is_ancestor(self.entry))

    def work(self, handle):
        try:
            self.phase('Connecting to the desktop session bus','Opening your computer’s permission window…')
            self.portal = Portal(handle,capture_screen=not self.input_only,progress=lambda message:self.phase('Checking screen capture',message))
            request = self.portal.request
            def traced(interface, method, *args, **kwargs):
                self.phase(interface + '.' + method + ': waiting',
                           'Waiting for your computer to confirm access…' if method=='Start' else None)
                response = request(interface, method, *args, **kwargs)
                self.phase(interface + '.' + method + ': response received')
                return response
            self.portal.request = traced
            if self.control:
                self.result['connection'] = self.portal.connect(self.token)
                self.save()
                self.phase('Permission received.','Connected. Buddy will type a short message in the box below.')
                self.on_ui(lambda: (self.instructions.set_text('You don’t need to type anything. If nothing happens, click inside the box below.'),self.entry.set_visible(True),self.window.present(),self.entry.grab_focus()))
                deadline = time.monotonic() + 30
                while not self.on_ui(self.focused):
                    self.token.wait(.1)
                    if time.monotonic() > deadline:
                        raise RuntimeError('The test entry did not gain focus. No input was sent.')
                self.token.wait(.2)
                if not self.on_ui(self.focused): raise RuntimeError('Test entry lost focus. No input was sent.')
                self.phase('Typing the fixed test phrase into this focused entry','Buddy is typing…')
                def verify_target():
                    if not self.on_ui(self.focused):raise RuntimeError('The test entry lost focus. No further input was sent.')
                self.portal.input({'action': 'type', 'text': PHRASE}, self.token,verify_target=verify_target)
                self.token.wait(.5)
                observed = self.on_ui(self.entry.get_text)
                if observed != PHRASE: raise AssertionError('Input readback mismatch: ' + repr(observed))
                self.result['physical_keyboard_readback'] = observed
                self.phase('Keyboard readback verified.','Typing worked.' if self.input_only else 'Typing worked. Checking that Buddy can see your screen…')
            if self.control and not self.input_only:
                self.phase('Checking physical mouse input','Buddy is checking that it can click its own button…')
                self.on_ui(lambda:self.mouse_button.set_visible(True))
                self.token.wait(.3)
                accessibility=Accessibility()
                observed=accessibility.inspect(window=self.window.get_title(),depth=8)
                candidates=[node for node in observed['nodes'] if node.get('name')=='Buddy will click here']
                if len(candidates)!=1:raise RuntimeError('The test button could not be identified. No mouse input was sent.')
                target=accessibility.node(candidates[0]['path'])
                if target.get_application().get_process_id()!=os.getpid():
                    raise RuntimeError('The observed button belongs to another app. No mouse input was sent.')
                def bounds():
                    box=target.get_component_iface().get_extents(accessibility.A.CoordType.SCREEN)
                    return (box.x,box.y,box.width,box.height)
                original=bounds()
                x,y,width,height=original
                if width<=0 or height<=0:raise RuntimeError('The test button has no usable location.')
                arguments=self.portal.screen_input({'action':'click','x':x+width/2,'y':y+height/2})
                def verify_mouse_target():
                    if not self.on_ui(self.window.is_active) or bounds()!=original:
                        raise RuntimeError('The test window moved or lost focus. No mouse input was sent.')
                self.on_ui(lambda:setattr(self,'mouse_armed',True))
                self.portal.input(arguments,self.token,verify_target=verify_mouse_target)
                deadline=time.monotonic()+3
                while not self.on_ui(lambda:self.mouse_clicked):
                    self.token.wait(.05)
                    if time.monotonic()>deadline:raise RuntimeError('The test button did not receive the mouse click.')
                self.result['physical_mouse_click_verified']=True
                self.phase('Mouse click verified.','Clicking worked. Finishing the check…')
            if not self.input_only:
                self.result['screenshot'] = self.portal.screenshot(self.root / 'desktop.png', self.token)
            self.save()
            if not self.input_only:
                self.phase('Screenshot saved. Inspecting accessible applications.')
                self.result['applications'] = Accessibility().inspect(depth=1)
            self.token.check()
            self.result['status'] = 'passed'
        except Exception as error:
            self.result['status'] = 'cancelled' if self.token.event.is_set() else 'failed'
            self.result['error'] = str(error)
        finally:
            # Save outcome before cleanup, so even a stuck shutdown leaves evidence.
            self.save()
            if self.portal:
                self.result['permission_request']=getattr(self.portal,'last_request',None)
                try:
                    self.portal.close()
                    self.result['session_revoked'] = self.portal.session is None
                except Exception as error:
                    self.result['cleanup_error'] = str(error)
                    self.result['status'] = 'failed'
            self.result['elapsed_seconds'] = round(time.monotonic() - self.started, 2)
            self.finished = True
            self.save()
            print(json.dumps(self.result, indent=2), flush=True)
            GLib.idle_add(self.show_outcome)

    def show_outcome(self):
        self.entry.set_visible(False)
        self.mouse_button.set_visible(False)
        self.start_button.set_visible(False)
        passed=self.result['status']=='passed'
        self.heading.set_text('The check worked' if passed else 'The check did not finish')
        success=('Buddy typed the message. This check is finished. Mouse control and screen viewing still need checking.' if getattr(self,'input_only',False) else
                 'Buddy could see the shared screen, type and click. This check is finished.' if self.control else 'Buddy could see your shared screen. This check is finished.')
        self.instructions.set_text(success if passed else
            'You do not need to type anything or change your settings. Close this window; the details have been saved for investigation.')
        self.status_label.set_text('Computer access from this check is now closed.')
        self.cancel_button.set_label('Done')
        return False

    def start_worker(self, handle=''):
        if self.started_worker or self.finished: return False
        self.started_worker = True
        self.work_started=time.monotonic()
        self.start_button.set_visible(False)
        self.instructions.set_text('Allow Buddy to type when your computer asks. No screen selection is needed for this check. Buddy will do the typing for you.' if self.input_only else
            'Click the picture of the screen containing this window so it is selected, then click Share. If “Allow Remote Interaction” appears, turn it on. Buddy will do the typing and clicking.')
        threading.Thread(target=self.work, args=(handle,), daemon=True).start()
        return False

    def cancel(self, *_):
        if self.finished:self.app.quit();return True
        self.token.stop()
        self.phase('Cancelling and saving the result…','Stopping the check…')
        if not self.started_worker:
            self.result.update(status='cancelled', error='Cancelled before desktop access started.')
            self.save()
            self.finished = True
            self.app.quit()
        return True  # Keep the window alive until the worker records cancellation.

    def watchdog(self):
        if self.finished: return False
        if not self.started_worker:return True
        if time.monotonic() - self.work_started > 240:
            self.token.stop()
            self.result.update(status='failed', error='Overall 240-second deadline exceeded. See the last recorded stage.')
            self.save()
            self.finished = True
            self.app.quit()
            return False
        return True

    def activate(self, _app):
        try:
            self.window = Gtk.ApplicationWindow(application=self.app,
                title='Desktop Buddy — computer access', default_width=600, default_height=290)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
            for side in ('top', 'bottom', 'start', 'end'): getattr(box, 'set_margin_' + side)(24)
            self.heading=Gtk.Label(label='Let Buddy use your computer',xalign=0)
            box.append(self.heading)
            intro=('Click Start check when you’re ready. Allow Buddy to type when your computer asks. Buddy will type a short message here; you don’t need to type anything.' if getattr(self,'input_only',False) else
                   'Click Start check when you’re ready. Your computer will ask which screen Buddy can use. After you allow access, Buddy will type a short message and click a button here. You don’t need to type or click for it.')
            self.instructions=Gtk.Label(label=intro,xalign=0,wrap=True)
            box.append(self.instructions)
            self.entry = Gtk.Entry(placeholder_text='Click here — Buddy will type for you')
            self.entry.set_visible(False)
            box.append(self.entry)
            self.mouse_armed=False;self.mouse_clicked=False
            self.mouse_button=Gtk.Button(label='Buddy will click here')
            self.mouse_button.set_visible(False)
            self.mouse_button.connect('clicked',lambda *_:setattr(self,'mouse_clicked',self.mouse_armed))
            box.append(self.mouse_button)
            self.status_label = Gtk.Label(label='The check only types and clicks inside this window.', xalign=0, wrap=True)
            box.append(self.status_label)
            self.window_handle=''
            self.start_button=Gtk.Button(label='Start check')
            self.start_button.add_css_class('suggested-action')
            self.start_button.connect('clicked',lambda *_:self.start_worker(self.window_handle))
            box.append(self.start_button)
            cancel = Gtk.Button(label='Cancel');self.cancel_button=cancel
            cancel.connect('clicked', self.cancel)
            box.append(cancel)
            self.window.set_child(box)
            self.window.connect('close-request', self.cancel)
            self.window.present()
            def exported(surface, handle, *_):
                self.window_handle='wayland:'+handle
            surface = self.window.get_surface()
            if isinstance(surface, GdkWayland.WaylandToplevel):
                surface.export_handle(exported, None)
            GLib.timeout_add(1000, self.watchdog)
        except Exception as error:
            self.result.update(status='failed', error='Could not create the test window: ' + str(error))
            self.save()
            self.finished = True
            self.app.quit()

    def run(self):
        if Gdk.Display.get_default() is None and Gdk.Display.open(None) is None:
            self.result.update(status='blocked', error='No physical display is accessible. Run from the GNOME desktop terminal.')
            self.save()
            print(self.result['error'], flush=True)
            return 2
        self.app.run([])
        self.token.stop()
        if not self.finished:
            self.result.update(status='failed', error='The test exited before completion. No pass is claimed.')
            self.save()
        return 0 if self.result['status'] == 'passed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', action='store_true', help='Test keyboard and mouse input in the dedicated window after consent')
    parser.add_argument('--input-only',action='store_true',help='Isolate keyboard permission without requesting a screen stream; does not verify screen or mouse support')
    args = parser.parse_args()
    return PhysicalCheck(args.control or args.input_only,args.input_only).run()


if __name__ == '__main__':
    raise SystemExit(main())
