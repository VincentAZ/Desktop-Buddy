#!/usr/bin/env python3
"""Real GTK setup/switching regression test with isolated HTTP and keyring fixtures."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gtk, GLib, Graphene
from desktop_buddy.ui import BuddyApp, content
from desktop_buddy.state import PROVIDERS

release = threading.Event()
entered = threading.Event()
discovery_release = threading.Event()
discovery_entered = threading.Event()
requests = []


class Server(BaseHTTPRequestHandler):
    def log_message(self, *_):pass

    def reply(self, body, mime):
        data = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):pass

    def do_GET(self):
        if '/slow/' in self.path:
            discovery_entered.set()
            discovery_release.wait(15)
        ids = ['gpt-3.5-turbo-0125','text-embedding-3-large','model-b','gpt-realtime-2',
               'gpt-5.4-2026-03-05','gpt-5.4-mini','model-a','gpt-5.6-luna']
        self.reply(json.dumps({'data':[{'id':model} for model in ids]}),'application/json')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append((self.path, body))
        messages = body.get('input', body.get('messages', []))
        probe = 'connection_probe' in json.dumps(body.get('tools', []))
        users = [m for m in messages if m.get('role') == 'user']
        if users and 'Hold response' in str(users[-1].get('content')):
            entered.set()
            release.wait(15)
        if self.path.endswith('/responses'):
            output = ([{'type':'function_call','call_id':'probe','name':'connection_probe','arguments':'{"value":"buddy-test"}'}]
                      if probe else [{'type':'message','role':'assistant','content':[{'type':'output_text','text':'fixture response'}]}])
            event = {'type':'response.completed','response':{'output':output,'usage':{'input_tokens':8,'output_tokens':3}}}
        else:
            delta = ({'tool_calls':[{'index':0,'id':'probe','function':{'name':'connection_probe','arguments':'{"value":"buddy-test"}'}}]}
                     if probe else {'content':'fixture response'})
            event = {'choices':[{'index':0,'delta':delta,'finish_reason':'tool_calls' if probe else 'stop'}]}
        self.reply('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n', 'text/event-stream')


class Credentials:
    def __init__(self):self.keys = {}
    def get(self, pid):return self.keys.get(pid, '')
    def set(self, pid, key):self.keys[pid] = key


root = Path(__file__).resolve().parents[1] / '.test-data' / 'model-ui'
root.mkdir(parents=True, exist_ok=True)
profile = tempfile.TemporaryDirectory(dir=root)
server = ThreadingHTTPServer(('127.0.0.1', 0), Server)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f'http://127.0.0.1:{server.server_port}'
app = BuddyApp(Path(profile.name)/'state')
app.credentials = Credentials()
# Approval here is for the synthetic local endpoint only; no real cloud request.
app.approve = lambda *_: True
app.store.set('provider', 'openai')
app.store.set('local_only', False)
app.store.set('window_width',940)
app.store.set('window_height',700)
for pid in ('openai','lmstudio'):
    c = app.store.configs()[pid]
    c.base_url = url
    app.store.save_config(c)
result = {}
phase = 0
started = time.monotonic()


def capture(window, filename):
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, float(window.get_width()), float(window.get_height()))
    node = snapshot.to_node()
    if node is None:
        window.present()
        window.queue_draw()
        return False
    rect = Graphene.Rect()
    rect.init(0, 0, window.get_width(), window.get_height())
    texture = window.get_renderer().render_texture(node, rect)
    texture.save_to_png(str(root/filename))
    return True


def pick(model, pid):
    picker = app.model_menu
    assert picker.button.get_mapped()
    picker.button.popup()
    row = picker.box.get_first_child()
    while row:
        if isinstance(row, Gtk.CheckButton):
            words = row.get_child()
            title = words.get_first_child()
            hint = title.get_next_sibling()
            if title.get_text() == model and hint.get_text().startswith(PROVIDERS[pid][0]+' ·'):
                row.set_active(True)
                picker.popover.popdown()
                return picker
        row = row.get_next_sibling()
    raise AssertionError(f'Model menu did not contain {pid}/{model}')


def choose_discovered(dialog, model):
    row = next(r for r in dialog.model_rows if r.model_id == model)
    dialog.model_list.select_row(row)
    assert dialog.model.get_text() == model
    return row


def finished():return app.worker and not app.worker.is_alive()


def tick():
    global phase, setup, cid, picker
    try:
        if time.monotonic()-started > 45:raise TimeoutError(f'Phase {phase} timed out')
        if phase == 0:
            setup = app.providers_dialog().controller
            setup.key.set_text('synthetic-test-key')
            assert not setup.use_button.get_sensitive()
            assert not setup.check_button.get_sensitive()
            assert setup.connect_button.get_sensitive()
            phase = 1
        elif phase == 1:
            if not capture(setup.window,'setup.png'):return True
            setup.connect_button.emit('clicked')
            phase = 2
        elif phase == 2 and setup.ids:
            assert not setup.model.get_text()
            assert not setup.use_button.get_sensitive()
            assert sum(r.get_visible() for r in setup.model_rows) <= 16
            result['connect_without_model'] = True
            result['no_implicit_first_model_selection'] = True
            assert setup.ids[:3] == ['gpt-5.6-luna','gpt-5.4-mini','gpt-5.4-2026-03-05'],setup.ids
            assert not next(r for r in setup.model_rows if r.model_id == 'text-embedding-3-large').get_visible()
            assert not next(r for r in setup.model_rows if r.model_id == 'gpt-3.5-turbo-0125').get_visible()
            setup.model_search.set_text('embedding')
            phase = 20
        elif phase == 20:
            visible=[r.model_id for r in setup.model_rows if r.get_visible()]
            assert visible == ['text-embedding-3-large'],(setup.model_search.get_text(),visible)
            result['default_filter_and_full_catalog_search'] = True
            setup.model_search.set_text('MODEL-A')
            phase = 21
        elif phase == 21:
            assert [r.model_id for r in setup.model_rows if r.get_visible()] == ['model-a']
            choose_discovered(setup,'model-a')
            assert setup.use_button.get_sensitive()
            result['live_case_insensitive_model_search'] = True
            setup.check_button.emit('clicked')
            phase = 3
        elif phase == 3 and not setup.operation_busy:
            assert 'verified' in setup.status.get_text()
            setup.thinking.set_selected(setup.thinking.values.index('high'))
            if not capture(setup.window,'connected.png'):return True
            result['model_tools_verified_in_dialog'] = True
            setup.use_button.emit('clicked')
            phase = 4
        elif phase == 4 and setup.closed:
            assert app.credentials.get('openai') == 'synthetic-test-key'
            assert app.store.configs()['openai'].model == 'model-a'
            assert app.store.configs()['openai'].reasoning == 'high'
            assert app.selected_id() == 'openai'
            assert app.model_menu.title.get_text() == 'model-a'
            assert app.model_menu.detail.get_text() == 'OpenAI · High thinking'
            result['visible_thinking_level_persisted'] = True
            cid = app.cid
            app.input.get_buffer().set_text('First message')
            app.run_button.emit('clicked')
            phase = 5
        elif phase == 5 and finished():
            assert app.store.tasks(cid)[0]['status'] == 'complete'
            assert requests[-1][1]['reasoning'] == {'effort':'high'}
            result['thinking_level_sent_to_provider'] = True
            setup = app.providers_dialog().controller
            setup.provider.set_selected(app.provider_ids.index('lmstudio'))
            assert not setup.model.get_text()
            assert setup.tools.get_active()
            setup.connect_button.emit('clicked')
            phase = 6
        elif phase == 6 and setup.ids:
            choose_discovered(setup,'model-b')
            setup.check_button.emit('clicked')
            phase = 7
        elif phase == 7 and not setup.operation_busy:
            setup.save_button.emit('clicked')
            phase = 8
        elif phase == 8 and app.store.configs()['lmstudio'].model == 'model-b':
            setup.close()
            app.input.get_buffer().set_text('Keep this draft')
            picker = pick('model-b','lmstudio')
            phase = 9
        elif phase == 9:
            assert app.selected_id() == 'lmstudio'
            assert 'model-b' in picker.title.get_text()
            result['saved_model_menu_selection'] = True
            capture(app.window,'picker.png')
            assert app.cid == cid
            assert content(app.input) == 'Keep this draft'
            assert len(app.store.messages(cid)) == 2
            result['switch_keeps_conversation_history_and_draft'] = True
            app.run_button.emit('clicked')
            phase = 10
        elif phase == 10 and finished():
            assert app.agent.provider.config.id == 'lmstudio'
            assert app.agent.provider.config.model == 'model-b'
            assert app.store.tasks(cid)[0]['status'] == 'complete'
            last = requests[-1][1]['messages']
            assert any(m.get('content') == 'First message' for m in last)
            result['same_conversation_cross_provider_history_sent'] = True
            capture(app.window,'conversation.png')
            app.send('Hold response')
            phase = 11
        elif phase == 11 and entered.is_set():
            picker = pick('model-a','openai')
            assert app.agent.provider.config.id == 'lmstudio'
            assert app.selected_id() == 'openai'
            assert 'next message' in app.model_hint.get_text()
            assert picker.title.get_text() == 'model-b'
            assert picker.detail.get_text().startswith('LM Studio ·')
            picker.thinking.set_selected(picker.thinking.values.index('low'))
            assert app.store.configs()['openai'].reasoning == 'low'
            assert app.agent.provider.config.id == 'lmstudio'
            assert picker.title.get_text() == 'model-b'
            assert 'Low thinking' in app.model_hint.get_text()
            release.set()
            result['running_task_keeps_model_next_message_switches'] = True
            phase = 12
        elif phase == 12 and finished():
            assert app.model_menu.title.get_text() == 'model-a'
            assert app.model_menu.detail.get_text() == 'OpenAI · Low thinking'
            app.send('After the switch')
            phase = 13
        elif phase == 13 and finished():
            assert app.agent.provider.config.id == 'openai'
            assert requests[-1][1]['reasoning'] == {'effort':'low'}
            assert app.cid == cid
            assert app.store.tasks(cid)[0]['status'] == 'complete'
            result['next_request_uses_selected_provider_without_restart'] = True
            release.clear()
            entered.clear()
            app.send('Hold response again')
            phase = 14
        elif phase == 14 and entered.is_set():
            app.cancel_button.emit('clicked')
            phase = 15
        elif phase == 15 and finished():
            assert app.store.tasks(cid)[0]['status'] == 'cancelled'
            pick('model-b','lmstudio')
            assert app.selected_id() == 'lmstudio'
            assert app.cid == cid
            result['stop_then_menu_switch_preserves_conversation'] = True
            release.set()
            setup = app.providers_dialog().controller
            setup.fields['base_url'].set_text(url+'/slow')
            setup.connect_button.emit('clicked')
            phase = 16
        elif phase == 16 and discovery_entered.is_set():
            setup.provider.set_selected(app.provider_ids.index('openai'))
            discovery_release.set()
            phase = 17
        elif phase == 17 and not app.aux_tokens:
            assert setup.model.get_text() == 'model-a'
            assert setup.selected_id() == 'openai'
            assert setup.fields['base_url'].get_text() == url
            result['stale_discovery_cannot_overwrite_other_provider'] = True
            setup.privacy.set_active(True)
            assert not setup.connect_button.get_sensitive()
            assert not setup.check_button.get_sensitive()
            assert app.local.get_active()
            result['privacy_control_visible_and_enforced'] = True
            setup.close()
            assert app.window.get_width() <= 960
            result['compact_window'] = [app.window.get_width(),app.window.get_height()]
            setup = app.providers_dialog().controller
            setup.provider.set_selected(app.provider_ids.index('anthropic'))
            setup.model.set_text('fixture-claude')
            for level in ('1024','4096','8192','16384','32768'):
                setup.fields['max_output'].set_text('4096')
                setup.thinking.set_selected(setup.thinking.values.index(level))
                config = setup.config()
                assert config.max_output > int(level)
                assert int(setup.fields['max_output'].get_text()) == config.max_output
            setup.use_button.emit('clicked')
            phase = 18
        elif phase == 18 and setup.closed:
            config = app.store.configs()['anthropic']
            assert config.reasoning == '32768' and config.max_output > 32768
            assert app.model_menu.title.get_text() == 'fixture-claude'
            assert app.model_menu.detail.get_text() == 'Anthropic · Maximum thinking'
            result['anthropic_setup_thinking_limits_and_save'] = True
            result['passed'] = True
            app.quit()
            return False
    except Exception as exc:
        import traceback
        traceback.print_exc()
        result['error'] = f'Phase {phase}: {exc}'
        app.quit()
        return False
    return True


GLib.timeout_add(150,tick)
try:app.run([])
finally:
    release.set()
    discovery_release.set()
    app.start_token.stop()
    server.shutdown()
    server.server_close()
    app.store.close()
    profile.cleanup()
(root/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2),flush=True)
if not result.get('passed'):raise SystemExit(1)
