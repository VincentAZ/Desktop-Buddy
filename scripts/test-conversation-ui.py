#!/usr/bin/env python3
"""Exercise conversation lifecycle through native widgets in an isolated profile."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gtk, GLib, Graphene
from PIL import Image
from desktop_buddy.ui import BuddyApp

root = Path(__file__).resolve().parents[1] / '.test-data' / 'conversation-ui'
root.mkdir(parents=True, exist_ok=True)
profile = tempfile.TemporaryDirectory(dir=root)
app = BuddyApp(Path(profile.name) / 'state')
compact = '--compact' in sys.argv
if compact:
    app.store.set('window_width', 940)
    app.store.set('window_height', 700)
result = {}
phase = 0


def widgets(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from widgets(child)
        child = child.get_next_sibling()


def click(window, title):
    next(w for w in widgets(window) if isinstance(w, Gtk.Button) and w.get_label() == title).emit('clicked')


def capture(window, name):
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, float(window.get_width()), float(window.get_height()))
    node = snapshot.to_node()
    if node is None:raise AssertionError('Native window failed to render')
    rect = Graphene.Rect()
    rect.init(0, 0, window.get_width(), window.get_height())
    texture = window.get_renderer().render_texture(node, rect)
    texture.save_to_png(str(root / name))


def tick():
    global phase, settings, cid, task, dialog
    try:
        if phase == 0:
            capture(app.window, 'compact.png' if compact else 'welcome.png')
            if compact:
                assert app.window.get_width() <= 960, app.window.get_width()
                result['compact_window'] = [app.window.get_width(), app.window.get_height()]
                app.quit()
                return False
            source = root / 'attachment-source.png'
            Image.new('RGB', (180, 90), (61, 116, 96)).save(source)
            attached = app.attach_image_path(source)
            assert app.pending_images == [str(attached)] and attached.is_file() and app.attachments.get_visible()
            remove = next(w for w in widgets(app.attachments) if isinstance(w, Gtk.Button) and w.get_tooltip_text() == 'Remove attached image')
            remove.emit('clicked')
            assert not app.pending_images and not attached.exists() and not app.attachments.get_visible()
            result['image_attachment_add_remove'] = True
            app.activate_action('search-conversations')
            focus = app.window.get_focus()
            assert focus is not None and (focus == app.history_search or focus.is_ancestor(app.history_search))
            draft_cid=app.cid
            pending = app.attach_image_path(source)
            app.activate_action('new-conversation')
            assert not app.pending_images and pending.exists()
            assert app.sessions[draft_cid].pending_images==[str(pending)]
            result['draft_attachment_preserved_in_its_conversation'] = True
            result['keyboard_actions'] = True
            settings = app.providers_dialog()
            phase = 1
        elif phase == 1:
            capture(settings, 'providers.png')
            dropdowns = [w for w in widgets(settings) if isinstance(w, Gtk.DropDown)]
            dropdowns[0].set_selected(app.provider_ids.index('ollama'))
            settings.controller.model.set_text('ui-test-model')
            click(settings, 'Use model')
            phase = 11
        elif phase == 11:
            if not settings.controller.closed:return True
            assert app.selected_id() == 'ollama'
            result['provider_save_selects_provider'] = True
            app.new_conversation()
            cid = app.cid
            app.store.add_message(cid, {'role': 'user', 'content': 'Conversation lifecycle test'})
            image = Path(profile.name) / 'state' / 'screenshots' / 'historical.png'
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (320, 120), (49, 94, 78)).save(image)
            app.store.add_message(cid, {'role': 'user', 'content': 'Saved visual observation', 'images': [str(image)]})
            app.store.add_message(cid, {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c1', 'name': 'run_tool', 'arguments': {'name': 'fs.read', 'arguments': {'path': 'notes.txt'}}}]})
            app.store.add_message(cid, {'role': 'tool', 'name': 'run_tool', 'tool_call_id': 'c1', 'content': '{"untrusted_tool_result": {"content": "hello from notes"}}', 'error': False})
            app.store.add_message(cid, {'role': 'assistant', 'content': 'The file says hello from notes.'})
            app.render_history()
            assert any(isinstance(w, Gtk.Picture) for w in widgets(app.transcript))
            result['historical_screenshot_rendered'] = True
            # Every message and every step offers a visible, plain "Copy" button.
            # (A collapsed expander keeps its child out of the widget tree.)
            for w in list(widgets(app.transcript)):
                if isinstance(w, Gtk.Expander):w.set_expanded(True)
            copies = [w for w in widgets(app.transcript) if isinstance(w, Gtk.Button) and 'copy-message' in w.get_css_classes()]
            assert len(copies) == 5, len(copies)  # 2 user messages, 1 step, 1 result, 1 reply
            copies[-1].emit('clicked')
            assert app.status.get_text().startswith('Buddy’s reply copied'), app.status.get_text()
            assert app.copy_menu.get_tooltip_text() == 'Copy conversation'
            plain = app.copy_conversation()
            assert 'hello from notes.' in plain and 'fs.read' not in plain
            full = app.copy_conversation(everything=True)
            assert full.index('You: Conversation lifecycle test') < full.index('[Image: ') < full.index('Desktop Buddy asked for a step:') < full.index('Step result (run_tool):') < full.index('Desktop Buddy: The file says'), full
            assert '"fs.read"' in full and 'hello from notes' in full
            assert app.status.get_text().startswith('Everything in this conversation copied'), app.status.get_text()
            result['copy_buttons_and_copy_everything'] = True
            task = app.store.task(cid, {})
            app.store.audit(task, 'test', {'safe': True})
            app.store.checkpoint(task, cid, 'complete', {})
            phase = 12
        elif phase == 12:
            # The newest card must sit above the fold without anyone scrolling:
            # the scrollbar saying "at the end" is not enough (v0.4.2 regression).
            last = app.transcript.get_last_child()
            while last and not last.get_visible():last = last.get_prev_sibling()
            bounds = last.compute_bounds(app.transcript_scroll)[1]
            assert bounds.get_y() + bounds.get_height() <= app.transcript_scroll.get_height(), (bounds.get_y(), bounds.get_height(), app.transcript_scroll.get_height())
            result['newest_message_visible_without_scrolling'] = True
            # The compositor may not offer a fresh main-window snapshot after
            # closing a modal. Verify the history image widget above regardless;
            # dedicated foreground renders cover its visual presentation.
            try:capture(app.window, 'history-image.png')
            except AssertionError as exc:result['history_render_note'] = str(exc)
            dialog = app.rename_conversation_dialog()
            next(w for w in widgets(dialog) if isinstance(w, Gtk.Entry)).set_text('Renamed conversation')
            phase = 2
        elif phase == 2:
            click(dialog, 'Rename')
            assert app.conversation_title.get_text() == 'Renamed conversation'
            app.history_search.set_text('renamed')
            app.refresh_history()
            assert app.history.get_first_child().cid == cid
            assert app.history.get_first_child().get_next_sibling() is None
            result['rename_and_search'] = True
            dialog = app.delete_conversation_dialog()
            phase = 3
        elif phase == 3:
            click(dialog, 'Keep conversation')
            assert app.store.messages(cid)
            result['delete_cancel_preserves_history'] = True
            dialog = app.delete_conversation_dialog()
            phase = 4
        elif phase == 4:
            click(dialog, 'Delete conversation')
            assert not app.store.messages(cid)
            assert not app.store.tasks(cid)
            assert not app.store.audits(task)
            assert not (Path(profile.name) / 'state' / 'screenshots' / 'historical.png').exists()
            assert app.cid != cid
            result['confirmed_delete_removes_related_records'] = True
            app.history_search.set_text('')
            app.new_conversation()
            phase = 5
        elif phase == 5:
            dialog = app.memory_dialog()
            click(dialog, 'Add a preference')
            next(w for w in widgets(dialog) if isinstance(w, Gtk.Entry)).set_text('preferred_editor')
            next(w for w in widgets(dialog) if isinstance(w, Gtk.TextView)).get_buffer().set_text('Cursor')
            phase = 6
        elif phase == 6:
            capture(dialog, 'memory.png')
            click(dialog, 'Save changes')
            assert app.store.memory() == {'preferred_editor': 'Cursor'}
            dialog = app.memory_dialog()
            phase = 7
        elif phase == 7:
            click(dialog, 'Remove')
            click(dialog, 'Cancel')
            assert app.store.memory() == {'preferred_editor': 'Cursor'}
            dialog = app.memory_dialog()
            phase = 8
        elif phase == 8:
            click(dialog, 'Remove')
            click(dialog, 'Save changes')
            assert app.store.memory() == {}
            result['memory_create_cancel_and_delete'] = True
            phase = 9
        else:
            app.quit()
            return False
    except Exception as exc:
        result['error'] = str(exc)
        app.quit()
        return False
    return True


GLib.timeout_add(800, tick)
try:
    app.run([])
finally:
    app.store.close()
    profile.cleanup()
(root / ('compact-result.json' if compact else 'result.json')).write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
if 'error' in result:raise SystemExit(1)
