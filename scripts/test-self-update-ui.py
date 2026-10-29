#!/usr/bin/env python3
"""Native check of the "Let Buddy change its own code" setting and the restart bar.

Temporary profile, no model, no network. Saves screenshots under .test-data/self-update-ui.
"""
from pathlib import Path
import os
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
from desktop_buddy.ui import BuddyApp
from desktop_buddy.selfdev import SETTING
from desktop_buddy import selfdev

root = Path(__file__).resolve().parents[1]/'.test-data'/'self-update-ui'
root.mkdir(parents=True, exist_ok=True)
profile = tempfile.TemporaryDirectory(dir=root)
app = BuddyApp(Path(profile.name)/'state')
app.credentials.get = lambda *_: ''
failures = []


def walk(widget):
    child = widget.get_first_child()
    while child:
        yield child
        yield from walk(child)
        child = child.get_next_sibling()


def snapshot(window, name):
    paintable = Gtk.WidgetPaintable.new(window.get_child() or window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, paintable.get_intrinsic_width(), paintable.get_intrinsic_height())
    node = snapshot.to_node()
    if node is None: return
    import gi as _gi
    _gi.require_version('Gsk', '4.0')
    from gi.repository import Gsk, Graphene
    renderer = window.get_native().get_renderer()
    bounds = Graphene.Rect().init(0, 0, paintable.get_intrinsic_width(), paintable.get_intrinsic_height())
    texture = renderer.render_texture(node, bounds)
    texture.save_to_png(str(root/(name+'.png')))


def steps():
    yield 1.0
    settings = app.settings_dialog().controller
    settings.show_it()
    yield 0.5
    box = settings.self_update
    if not box.get_sensitive(): failures.append('checkbox is insensitive although this is a source checkout')
    if box.get_active(): failures.append('setting defaults to on')
    box.set_active(True)
    yield 0.2
    if not app.store.get(SETTING): failures.append('turning the box on did not save the setting')
    snapshot(settings.window, 'it-setup-own-code')
    box.set_active(False)
    if app.store.get(SETTING): failures.append('turning the box off did not clear the setting')
    settings.close()
    help_texts = [w.get_text() for w in walk(app.window) if isinstance(w, Gtk.Label)]
    if app.restart_bar.get_visible(): failures.append('restart bar shown with unchanged code')
    # The person can always restart from the sidebar, not only after a code change.
    if 'Restart Buddy' not in help_texts: failures.append('no Restart Buddy button in the sidebar')
    accels = app.get_accels_for_action('app.restart-app')  # GTK reports a normalised spelling
    if not accels or Gtk.accelerator_parse(accels[0]) != Gtk.accelerator_parse('<Primary><Shift>r'): failures.append('restart shortcut missing: '+repr(accels))
    # Pretend the files on disk changed: the bar must appear with a Restart Buddy button.
    selfdev._LOADED = 'stale'
    app.refresh_restart_bar()
    yield 0.3
    if not app.restart_bar.get_visible(): failures.append('restart bar hidden after code changed')
    labels = [w.get_label() for w in walk(app.restart_bar) if isinstance(w, Gtk.Button)]
    if labels != ['Restart Buddy']: failures.append('restart button missing: '+repr(labels))
    snapshot(app.window, 'restart-bar')
    # A working conversation must defer the restart instead of killing it.
    app.current_session.done = None
    class Worker:
        def is_alive(self): return True
    app.current_session.worker = Worker()
    app.restart_app()
    yield 0.2
    if 'other conversations' not in app.restart_label.get_text(): failures.append('busy restart did not defer: '+app.restart_label.get_text())
    if not app.get_windows(): failures.append('app quit while a conversation was working')
    if not app.restart_pending: failures.append('deferred restart was not remembered')
    app.refresh_restart_bar()
    if 'as soon as' not in app.restart_label.get_text(): failures.append('code-change text replaced the person’s pending restart')
    # When the last working conversation finishes, the remembered restart happens by itself.
    launched = []
    app.restart_app = lambda *_: launched.append(True) or False
    app.current_session.worker = None
    app.session_event(app.cid, app.current_session.generation, 'done', {'status': 'complete'})
    yield 0.2
    if not launched: failures.append('pending restart did not fire when the conversation finished')
    print('FAILURES: '+'; '.join(failures) if failures else 'Self-update UI check passed. Screenshots: '+str(root), flush=True)
    app.quit()


def drive():
    gen = steps()
    def step():
        try: delay = next(gen)
        except StopIteration: return False
        GLib.timeout_add(int(delay*1000), step)
        return False
    step()


app.connect('activate', lambda *_: GLib.timeout_add(500, lambda: drive() or False))
code = app.run([])
raise SystemExit(1 if failures or code else 0)
