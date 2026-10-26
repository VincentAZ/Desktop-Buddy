#!/usr/bin/env python3
"""Physical Wayland check with normal GNOME consent and a dedicated test window.

Never approves portal dialogs or sends input to an unrelated application. Keyboard
events are sent only while this window is active and its test entry has focus.
"""
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkWayland', '4.0')
from gi.repository import Gtk, Gio, GLib, GdkWayland
from desktop_buddy.desktop import Portal, Accessibility
from desktop_buddy.security import StopToken

root = Path(__file__).resolve().parents[1] / '.test-data' / 'portal-desktop'
root.mkdir(parents=True, exist_ok=True)
result = {}
token = StopToken()
app = Gtk.Application(application_id='io.desktopbuddy.PhysicalCheck', flags=Gio.ApplicationFlags.NON_UNIQUE)
portal = None
phrase = 'Desktop Buddy physical input verified'


def on_ui(func):
    done = threading.Event()
    value = []
    def run():
        try:value.append((True, func()))
        except Exception as exc:value.append((False, exc))
        done.set()
        return False
    GLib.idle_add(run)
    if not done.wait(5):raise TimeoutError('Test window stopped responding')
    if not value[0][0]:raise value[0][1]
    return value[0][1]


def work(handle):
    global portal
    try:
        portal = Portal(handle)
        if '--control' in sys.argv:
            result['connection'] = portal.connect(token)
            on_ui(lambda: (window.present(), entry.grab_focus()))
            deadline = time.monotonic() + 15
            def focused():
                focus = window.get_focus()
                return window.is_active() and focus is not None and (focus == entry or focus.is_ancestor(entry))
            while not on_ui(focused):
                token.wait(.1)
                if time.monotonic() > deadline:
                    raise RuntimeError('Test window did not gain focus; no input was sent. Focus it and retry.')
            token.wait(.2)
            if not on_ui(focused):raise RuntimeError('Test entry lost focus; input cancelled')
            portal.input({'action': 'type', 'text': phrase}, token)
            token.wait(.3)
            observed = on_ui(entry.get_text)
            if observed != phrase:raise AssertionError(f'Input readback mismatch: {observed!r}')
            result['physical_keyboard_readback'] = observed
            result['screenshot'] = portal.screenshot(root / 'desktop.png', token)
            portal.close()
            result['session_revoked'] = portal.session is None
        else:
            result['screenshot'] = portal.screenshot(root / 'desktop.png', token)
        result['applications'] = Accessibility().inspect(depth=1)
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        if portal:portal.close()
        (root / 'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)
        GLib.idle_add(app.quit)


def activate(_app):
    global window, entry
    window = Gtk.ApplicationWindow(application=app, title='Desktop Buddy — safe input check', default_width=620, default_height=240)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    for side in ('top', 'bottom', 'start', 'end'):getattr(box, 'set_margin_' + side)(24)
    box.append(Gtk.Label(label='Physical desktop check', xalign=0))
    box.append(Gtk.Label(label='Accept GNOME’s screen-sharing dialog to test. Input, when enabled, is limited to the field below. Close this window to cancel.', xalign=0, wrap=True))
    entry = Gtk.Entry(placeholder_text='Test input appears here')
    box.append(entry)
    cancel = Gtk.Button(label='Cancel check')
    cancel.connect('clicked', lambda *_: token.stop())
    box.append(cancel)
    window.set_child(box)
    window.connect('close-request', lambda *_: token.stop() or False)
    window.present()
    def exported(surface, handle, *_):
        def start():
            threading.Thread(target=work, args=('wayland:' + handle,), daemon=True).start()
            return False
        GLib.timeout_add(700, start)
    window.get_surface().export_handle(exported, None)


app.connect('activate', activate)
app.run([])
token.stop()
if portal:portal.close()
if result.get('error') or not result:raise SystemExit(1)
