#!/usr/bin/env python3
"""Use Buddy's real AT-SPI backend against a dedicated harmless GTK window."""
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio, GLib
from desktop_buddy.desktop import Accessibility

root = Path(__file__).resolve().parents[1] / '.test-data' / 'accessibility-control'
root.mkdir(parents=True, exist_ok=True)
result = {}
changed = threading.Event()
app = Gtk.Application(application_id='io.desktopbuddy.AccessibilityCheck', flags=Gio.ApplicationFlags.NON_UNIQUE)
title = f'Desktop Buddy — accessibility control check {os.getpid()}'


def worker():
    try:
        backend = Accessibility()
        deadline = time.monotonic() + 10
        button_node = None
        while time.monotonic() < deadline:
            tree = backend.inspect(window=title, depth=10, limit=500)
            button_node = next((node for node in tree['nodes'] if node.get('name') == 'Set verification result' and node.get('actions')), None)
            if button_node:break
            time.sleep(.2)
        if not button_node:raise AssertionError('Dedicated button was not exposed through AT-SPI')
        action = 'click' if 'click' in button_node['actions'] else button_node['actions'][0]
        invoked = backend.act(button_node['path'], action, 'Set verification result')
        if not invoked['accepted']:raise AssertionError('AT-SPI rejected the action')
        if not changed.wait(3):raise AssertionError('GTK callback did not run')
        observed = backend.inspect(window=title, depth=10, limit=500)
        if not any(node.get('name') == 'Accessibility action verified' for node in observed['nodes']):
            raise AssertionError('Changed UI state was not observable through AT-SPI')
        result.update({'button_path':button_node['path'],'available_actions':button_node['actions'],
            'invocation_accepted':True,'changed_state_observed':True})
    except Exception as error:result['error']=str(error)
    finally:
        (root/'result.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2),flush=True)
        GLib.idle_add(app.quit)


def activate(_app):
    window=Gtk.ApplicationWindow(application=app,title=title,default_width=520,default_height=230)
    box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=18)
    for side in ('top','bottom','start','end'):getattr(box,'set_margin_'+side)(28)
    box.append(Gtk.Label(label='Safe AT-SPI action test',xalign=0))
    status=Gtk.Label(label='Waiting for accessibility action',xalign=0)
    box.append(status)
    button=Gtk.Button(label='Set verification result')
    def clicked(*_):status.set_label('Accessibility action verified');changed.set()
    button.connect('clicked',clicked);box.append(button)
    window.set_child(box);window.present()
    threading.Thread(target=worker,daemon=True).start()


app.connect('activate',activate)
app.run([])
if result.get('error') or not result:raise SystemExit(1)
