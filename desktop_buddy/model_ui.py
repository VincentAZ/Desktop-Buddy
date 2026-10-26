"""Model selection and connection setup, separate from the conversation runner."""
from dataclasses import replace
import re
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
from .state import PROVIDERS, ProviderConfig, thinking_levels, thinking_label
from .providers import Provider
from .comfyui import ComfyUI
from .ui_widgets import label, button, scroller


NON_AGENT_MARKERS = (
    'audio', 'embedding', 'image', 'moderation', 'realtime', 'sora',
    'speech', 'transcribe', 'tts', 'whisper', 'babbage', 'davinci',
)
DEFAULT_MODEL_LIMIT = 16


def is_versioned_snapshot(model_id):
    """Dated provider snapshots stay available without overwhelming first choice."""
    value = model_id.casefold()
    return bool(re.search(r'-(?:20\d\d-\d\d-\d\d|\d{4})$', value))


def is_likely_agent_model(model_id):
    value = model_id.casefold()
    return not any(marker in value for marker in NON_AGENT_MARKERS)


def natural_parts(value):
    return tuple(int(part) if part.isdigit() else part for part in re.split(r'(\d+)', value.casefold()))


def model_sort_key(model_id):
    """Put newer general GPT/O-series names first, then use natural A-Z order."""
    value = model_id.casefold()
    family = 0 if value.startswith('gpt-') else 1 if re.match(r'o\d', value) else 2
    version = re.match(r'(?:gpt-|o)(\d+)(?:\.(\d+))?', value)
    major = int(version.group(1)) if version else 0
    minor = int(version.group(2) or 0) if version else 0
    return (family, -major, -minor, is_versioned_snapshot(value), natural_parts(value))


def entry(name, text=''):
    field = Gtk.Entry(text=str(text), hexpand=True)
    field.update_property([Gtk.AccessibleProperty.LABEL], [name])
    return field


def section(title):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    box.add_css_class('settings-section')
    box.append(label(title, 'step-title'))
    return box


def window(app, title, width=620, height=620):
    height = min(height, max(500, app.window.get_height() - 40))
    dialog = Gtk.Window(application=app, transient_for=app.window, modal=True,
                        title=title, default_width=width, default_height=height)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    for side in ('start', 'end', 'top', 'bottom'):
        getattr(box, 'set_margin_' + side)(20)
    dialog.set_child(box)
    return dialog, box


def set_thinking_choices(dropdown, provider_id, value='', model=''):
    choices = thinking_levels(provider_id, model)
    dropdown.values = [wire for _title, wire in choices]
    dropdown.set_model(Gtk.StringList.new([title for title, _wire in choices]))
    try:index = dropdown.values.index(value)
    except ValueError:index = 0
    dropdown.set_selected(index)
    dropdown.set_tooltip_text('The selected model must support this level. Provider default omits the setting.')


def thinking_value(dropdown):
    selected = dropdown.get_selected()
    return dropdown.values[selected] if selected < len(dropdown.values) else ''


class ModelPicker:
    def __init__(self, app):
        self.app = app
        self.window, box = window(app, 'Change model', height=500)
        box.append(label('Choose a model', 'brand'))
        running = bool(app.worker and app.worker.is_alive())
        box.append(label('Your conversation is kept. The current task keeps its model unless you stop it.'
                         if running else 'Switch providers or models and continue this conversation.',
                         'muted', wrap=True))
        search = Gtk.SearchEntry(placeholder_text='Search your models and providers')
        self.search = search
        search.update_property([Gtk.AccessibleProperty.LABEL], ['Search models'])
        box.append(search)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class('model-choice')
        profiles = app.store.get('model_profiles', {})
        for config in app.store.configs().values():
            if config.model:profiles[config.id + ':' + config.model] = config.__dict__
        self.rows = []
        current = app.store.configs()[app.selected_id()]
        for values in sorted(profiles.values(), key=lambda v: (v['id'], v['model'])):
            config = ProviderConfig(**values)
            row = Gtk.ListBoxRow()
            row.config = config
            row.search_text = (config.model + ' ' + PROVIDERS[config.id][0]).casefold()
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for side in ('top', 'bottom', 'start', 'end'):getattr(content, 'set_margin_' + side)(10)
            content.append(label(config.model, ellipsize=3))
            suffix = 'Local' if config.local else 'Cloud'
            if (config.id, config.model) == (current.id, current.model):suffix += ' · Selected'
            content.append(label(PROVIDERS[config.id][0] + ' · ' + suffix, 'muted'))
            content.append(label('Thinking: ' + thinking_label(config.id, config.reasoning), 'status'))
            row.set_child(content)
            self.list.append(row)
            self.rows.append(row)
            if (config.id, config.model) == (current.id, current.model):self.list.select_row(row)
        self.empty = label('No saved models yet. Connect a provider below to choose one.', 'muted', wrap=True)
        box.append(self.empty)
        box.append(scroller(self.list, minimum=140))
        self.use = button('Use for next message' if running else 'Use model', lambda *_: self.choose(), 'suggested-action')
        self.stop_switch = button('Stop task & switch', lambda *_: self.choose(True))
        self.stop_switch.set_visible(running)
        self.note = label('', 'status', wrap=True)
        box.append(self.note)
        actions = Gtk.Box(spacing=8)
        actions.append(button('Connect / add model', self.setup))
        box.append(actions)
        footer = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        footer.append(button('Cancel', lambda *_: self.window.destroy()))
        footer.append(self.stop_switch)
        footer.append(self.use)
        box.append(footer)
        def selection(*_):
            row = self.list.get_selected_row()
            available = bool(row and row.get_visible())
            self.use.set_sensitive(available)
            self.stop_switch.set_sensitive(available)
            blocked = available and app.local.get_active() and not row.config.local
            self.note.set_text('Offline mode is on. Turn it off in Task options to use this online model.' if blocked else '')
        def filter_rows(*_):
            query = search.get_text().casefold().strip()
            for row in self.rows:row.set_visible(query in row.search_text)
            self.empty.set_visible(not any(r.get_visible() for r in self.rows))
            if self.rows:self.empty.set_text('No matching saved models. Connect / add model to find more.')
            selection()
        search.connect('notify::text', filter_rows)
        self.list.connect('row-selected', selection)
        self.list.connect('row-activated', lambda *_: self.choose())
        filter_rows()
        self.window.present()

    def setup(self, *_):
        self.window.destroy()
        self.app.providers_dialog()

    def choose(self, stop=False):
        row = self.list.get_selected_row()
        if not row or not row.get_visible():return
        config = row.config
        if stop and self.app.worker and self.app.worker.is_alive():
            self.app.cancel()
            self.note.set_text('Stopping the current task before switching…')
            self.use.set_sensitive(False)
            self.stop_switch.set_sensitive(False)
            def stopped():
                if self.app.worker and self.app.worker.is_alive():return True
                if self.window.get_visible():
                    self.app.select_model(config)
                    self.window.destroy()
                return False
            GLib.timeout_add(50, stopped)
        else:
            self.app.select_model(config)
            self.window.destroy()


class ConnectionDialog:
    def __init__(self, app):
        self.app = app
        self.generation = 0
        self.token = None
        self.closed = False
        self.filling = False
        self.window, outer = window(app, 'Set up your assistant', height=700)
        self.window.connect('close-request', self.close)
        outer.append(label('Set up your assistant', 'brand'))
        outer.append(label('Choose your AI service, connect your account, then pick a model.', 'muted', wrap=True))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.append(scroller(body, minimum=260))
        self.connection_expander = Gtk.Expander(label='1  Choose your AI service')
        self.connection_expander.add_css_class('settings-section')
        self.connection_expander.set_expanded(True)
        connection = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        connection.set_margin_top(10)
        self.connection_expander.set_child(connection)
        body.append(self.connection_expander)
        self.provider = Gtk.DropDown.new_from_strings([v[0] for v in PROVIDERS.values()])
        self.provider.update_property([Gtk.AccessibleProperty.LABEL], ['AI service'])
        self.provider.set_selected(app.selector.get_selected())
        connection.append(self.provider)
        self.key_label = label('Connection key (API key)')
        connection.append(self.key_label)
        self.key = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True)
        self.key.set_property('placeholder-text', 'Paste a key, or leave blank to use your saved key')
        self.key.update_property([Gtk.AccessibleProperty.LABEL], ['Connection key (API key)'])
        connection.append(self.key)
        self.key_hint = label('Get an API key from your AI service’s account settings. Saved securely in your computer’s keyring.', 'muted', wrap=True)
        connection.append(self.key_hint)
        self.privacy = Gtk.CheckButton(label='Keep Buddy offline (local models only)')
        self.privacy.set_active(app.local.get_active())
        self.privacy.connect('toggled', lambda w: app.local.set_active(w.get_active()))
        connection.append(self.privacy)
        self.privacy_note = label('', 'status', wrap=True)
        connection.append(self.privacy_note)
        self.connect_button = button('Connect', lambda *_: self.connect(), 'suggested-action')
        connect_row = Gtk.Box(spacing=10)
        connect_row.append(self.connect_button)
        connection.append(connect_row)
        self.connection_status = label('No model selection needed to connect.', 'status', wrap=True)
        self.connection_status.set_hexpand(True)
        connect_row.append(self.connection_status)

        models = section('2  Choose a model')
        body.append(models)
        self.model_search = Gtk.SearchEntry(placeholder_text='Search all provider models')
        self.model_search.update_property([Gtk.AccessibleProperty.LABEL], ['Search available models'])
        models.append(self.model_search)
        self.model_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.model_list.add_css_class('model-choice')
        model_scroll = scroller(self.model_list, False, 190)
        model_scroll.set_max_content_height(260)
        model_scroll.set_propagate_natural_height(True)
        models.append(model_scroll)
        self.model_count = label('Connect above to load models.', 'status', wrap=True)
        models.append(self.model_count)
        self.show_all_models = Gtk.CheckButton(label='Show all provider models')
        models.append(self.show_all_models)
        self.model_empty = label('No models to show.', 'muted', wrap=True)
        self.model_empty.set_visible(False)
        models.append(self.model_empty)
        self.ids = []
        self.model_rows = []
        self.model_search.connect('notify::text', self.filter_models)
        self.show_all_models.connect('toggled', self.filter_models)
        self.model_list.connect('row-selected', self.model_selected)
        self.model_list.connect('row-activated', self.model_activated)
        manual = Gtk.Expander(label='Enter a model ID manually')
        self.model = entry('Model ID')
        self.model.set_placeholder_text('Exact model ID from your provider')
        self.model.connect('changed', self.model_changed)
        manual.set_child(self.model)
        models.append(manual)
        self.capability_note = label('', 'status', wrap=True)
        models.append(self.capability_note)
        thinking_row = Gtk.Box(spacing=10)
        thinking_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        thinking_text.append(label('Thinking level'))
        thinking_text.append(label('Lower is faster; higher can improve difficult work. Support depends on the model.', 'muted', wrap=True))
        thinking_row.append(thinking_text)
        self.thinking = Gtk.DropDown()
        self.thinking.update_property([Gtk.AccessibleProperty.LABEL], ['Thinking level'])
        thinking_row.append(self.thinking)
        models.append(thinking_row)
        self.check_button = button('Test connection (optional)', lambda *_: self.verify())

        advanced = Gtk.Expander(label='Advanced settings')
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        advanced.set_child(details)
        body.append(advanced)
        self.fields = {}
        for name, title in [('base_url','API base URL'), ('context_tokens','Context limit'),
                            ('max_output','Maximum response tokens'),
                            ('input_cost','Input price per million tokens'), ('output_cost','Output price per million tokens'),
                            ('api_version','Anthropic API version'), ('workspace_id','Anthropic workspace ID')]:
            details.append(label(title, 'muted'))
            self.fields[name] = entry(title)
            details.append(self.fields[name])
        self.protocol = Gtk.DropDown.new_from_strings(['responses','chat','anthropic','ollama'])
        details.append(label('API format', 'muted'))
        details.append(self.protocol)
        self.tools = Gtk.CheckButton(label='Allow computer tools (turn off only for text-only models)')
        self.vision = Gtk.CheckButton(label='Allow images (turn off only for text-only models)')
        details.append(self.check_button)
        details.append(self.tools)
        details.append(self.vision)
        details.append(label('Buddy tries tool calling automatically. The optional connection test does not run computer actions.', 'muted', wrap=True))
        details.append(button('Remove saved API key', lambda *_: self.remove_key()))
        local_tools = Gtk.Expander(label='Other app settings · ComfyUI')
        local_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.comfy = entry('ComfyUI URL', app.store.get('comfyui_url','http://127.0.0.1:8188'))
        local_box.append(label('ComfyUI URL', 'muted'))
        local_box.append(self.comfy)
        local_box.append(button('Save & test ComfyUI', lambda *_: self.test_comfy()))
        local_tools.set_child(local_box)
        body.append(local_tools)

        self.status = label('3  Use the selected model to continue your conversation.', 'status', wrap=True)
        outer.append(self.status)
        footer = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        footer.append(button('Close', lambda *_: self.close()))
        self.save_button = button('Save connection', lambda *_: self.save(False))
        footer.append(self.save_button)
        self.use_button = button('Use model', lambda *_: self.save(True), 'suggested-action')
        footer.append(self.use_button)
        outer.append(footer)
        self.operation_busy = False
        self.provider.connect('notify::selected', self.fill)
        self.privacy.connect('toggled', lambda *_: self.refresh_privacy())
        self.fill()
        self.window.present()

    def close(self, *_):
        self.closed = True
        self.generation += 1
        if self.token:self.token.stop()
        self.window.destroy()
        return True

    def selected_id(self):
        return self.app.provider_ids[self.provider.get_selected()]

    def fill(self, *_):
        self.generation += 1
        if self.token:self.token.stop()
        self.filling = True
        c = self.app.store.configs()[self.selected_id()]
        for name, field in self.fields.items():field.set_text(str(getattr(c, name)))
        set_thinking_choices(self.thinking, c.id, c.reasoning, c.model)
        self.protocol.set_selected(['responses','chat','anthropic','ollama'].index(c.protocol))
        self.model.set_text(c.model)
        self.tools.set_active(c.use_tools)
        self.vision.set_active(c.use_vision)
        self.key.set_text('')
        self.key_label.set_text('API key (optional for local servers)' if c.local else 'Connection key (API key)')
        self.key_label.set_tooltip_text('Stored in GNOME Keyring; never in the conversation or config file.')
        catalog = self.app.store.get('model_catalogs', {}).get(c.id, {})
        ids = catalog.get('ids', []) if catalog.get('base_url') == c.base_url else []
        self.set_models(ids, c.model)
        self.filling = False
        self.connection_status.set_text('No model selection needed to connect.')
        self.connection_expander.set_label('1  Connection · '+PROVIDERS[c.id][0])
        self.connection_expander.set_expanded(not bool(c.model))
        self.status.set_text('3  Use the selected model to continue your conversation.')
        self.busy(False)

    def set_models(self, ids, selected=''):
        while self.model_list.get_first_child():self.model_list.remove(self.model_list.get_first_child())
        self.ids = sorted(set(filter(None, ([selected] if selected else []) + ids)), key=model_sort_key)
        self.model_rows = []
        selected_row = None
        primary_seen = 0
        for model_id in self.ids:
            row = Gtk.ListBoxRow()
            row.model_id = model_id
            row.search_text = model_id.casefold()
            row.primary = is_likely_agent_model(model_id) and not is_versioned_snapshot(model_id)
            row.featured = row.primary and primary_seen < DEFAULT_MODEL_LIMIT
            if row.primary:primary_seen += 1
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            for side in ('top','bottom','start','end'):getattr(content,'set_margin_'+side)(8)
            content.append(label(model_id, ellipsize=3))
            detail = 'General/chat candidate' if is_likely_agent_model(model_id) else 'Specialized model type'
            if is_versioned_snapshot(model_id):detail += ' · dated snapshot'
            if model_id == selected:detail += ' · currently selected'
            content.append(label(detail, 'status'))
            row.set_child(content)
            self.model_list.append(row)
            self.model_rows.append(row)
            if model_id == selected:selected_row = row
        self.model_search.set_text('')
        self.show_all_models.set_active(False)
        self.show_all_models.set_label(f'Show all {len(self.ids)} provider models')
        self.filter_models()
        if selected_row:self.model_list.select_row(selected_row)
        else:self.model_list.unselect_all()

    def model_selected(self, *_):
        row = self.model_list.get_selected_row()
        if not self.filling and row and row.get_visible():self.model.set_text(row.model_id)

    def model_activated(self, _list, row):
        if row and row.get_visible():self.model.set_text(row.model_id)

    def filter_models(self, *_):
        query = self.model_search.get_text().casefold().strip()
        terms = query.split()
        show_all = self.show_all_models.get_active()
        visible = 0
        for row in self.model_rows:
            matches = all(term in row.search_text for term in terms)
            # Searching always scans the entire catalog. With an empty search,
            # keep the initial list focused on undated text/reasoning candidates.
            row.set_visible(matches and (bool(query) or show_all or row.featured))
            visible += int(row.get_visible())
        total = len(self.model_rows)
        self.model_empty.set_visible(bool(total) and not visible)
        self.model_empty.set_text('No models match that search.' if query else 'No general/chat models found. Show all model types to inspect the full catalog.')
        if not total:self.model_count.set_text('Connect above to load models.')
        elif query:self.model_count.set_text(f'{visible} of {total} models match · search includes the full catalog')
        elif show_all:self.model_count.set_text(f'Showing all {total} models · sorted by family/version, then name')
        else:
            candidates=sum(int(row.primary) for row in self.model_rows)
            self.model_count.set_text(f'Showing {visible} of {candidates} general/chat candidates · {total} models reported')

    def model_changed(self, *_):
        if self.filling:return
        saved = self.app.store.get('model_profiles', {}).get(self.selected_id()+':'+self.model.get_text().strip())
        self.tools.set_active(not saved or saved.get('tools') is not False)
        self.vision.set_active(not saved or saved.get('vision') is not False)
        if saved:
            for name in ('context_tokens','max_output','input_cost','output_cost'):
                self.fields[name].set_text(str(saved[name]))
            set_thinking_choices(self.thinking, self.selected_id(), saved.get('reasoning', ''), self.model.get_text().strip())
        else:set_thinking_choices(self.thinking, self.selected_id(), '', self.model.get_text().strip())
        self.ready()

    def ready(self):
        selected = bool(self.model.get_text().strip())
        self.use_button.set_sensitive(selected)
        self.check_button.set_sensitive(selected)
        self.capability_note.set_text('Ready to help with tasks. No separate tool test is required.' if selected and self.tools.get_active()
                                     else 'Computer tools are turned off in advanced settings. This model can still chat.' if selected
                                     else 'Choose from the searchable list after connecting, or enter an ID manually.')

    def refresh_privacy(self):
        cloud = not PROVIDERS[self.selected_id()][3]
        blocked = cloud and self.privacy.get_active()
        self.privacy_note.set_text('Turn off Keep Buddy offline above to use this online service.' if blocked
                                  else 'Cloud requests go to the selected provider. Task content still requires approval.' if cloud
                                  else 'Connects to a model service on this computer.')
        self.connect_button.set_sensitive(not blocked)
        if blocked:self.check_button.set_sensitive(False)

    def config(self):
        c = self.app.store.configs()[self.selected_id()]
        c.model = self.model.get_text().strip()
        for name, field in self.fields.items():
            raw = field.get_text().strip()
            setattr(c, name, int(raw) if name in ('context_tokens','max_output') else float(raw or 0) if name.endswith('_cost') else raw)
        c.reasoning = thinking_value(self.thinking)
        c.protocol = ['responses','chat','anthropic','ollama'][self.protocol.get_selected()]
        c.tools = self.tools.get_active()
        c.vision = self.vision.get_active()
        return c.validate()

    def busy(self, value):
        self.operation_busy = value
        for widget in (self.key,self.protocol,self.tools,self.vision,self.privacy,*self.fields.values()):widget.set_sensitive(not value)
        for widget in (self.connect_button,self.check_button,self.save_button,self.use_button,self.model_search,self.model_list,self.model,self.thinking):
            widget.set_sensitive(not value)
        if not value:
            self.model_search.set_sensitive(bool(self.ids))
            self.model_list.set_sensitive(bool(self.ids))
            self.ready()
            self.refresh_privacy()

    def run(self, work, success):
        self.generation += 1
        generation = self.generation
        if self.token:self.token.stop()
        self.busy(True)
        def finish(value=None, error=None):
            if self.closed or generation != self.generation:return False
            self.busy(False)
            if error:
                self.connection_status.set_text('Connection or model check did not finish.')
                self.status.set_text(self.app.redactor.text(error))
            else:success(value)
            return False
        def worker(token):
            try:
                value = work(token)
                token.check()
                GLib.idle_add(finish, value, None)
            except Exception as exc:GLib.idle_add(finish, None, str(exc))
        self.token = self.app.auxiliary(worker)

    def connect(self):
        try:c = self.config()
        except Exception as exc:self.status.set_text(str(exc));return
        secret = self.key.get_text()
        local = self.privacy.get_active()
        self.connection_status.set_text('Connecting and loading your model list…')
        self.status.set_text('Connecting…')
        def work(token):
            key = secret or self.app.credentials.get(c.id)
            return Provider(c, key, self.app.redactor).models(token, local)
        def done(data):
            ids = [m['id'] for m in data if isinstance(m.get('id'),str)]
            catalog = self.app.store.get('model_catalogs', {})
            catalog[c.id] = {'base_url':c.base_url,'ids':ids}
            self.app.store.set('model_catalogs',catalog)
            self.filling = True
            self.set_models(ids, c.model)
            self.filling = False
            self.connection_status.set_text(f'Connected · {len(ids)} models available')
            self.connection_expander.set_label('1  Connected · '+PROVIDERS[c.id][0])
            self.connection_expander.set_expanded(False)
            self.status.set_text('Choose a model from the sorted list, then click Use model.' if ids else 'Connected, but no models were listed. Enter a model ID manually.')
            self.ready()
        self.run(work,done)

    def verify(self):
        try:c = self.config()
        except Exception as exc:self.status.set_text(str(exc));return
        if not c.model:return
        secret = self.key.get_text()
        local = self.privacy.get_active()
        self.status.set_text('Checking this model’s response and tool calling…')
        def work(token):
            if not c.local and not self.app.approve('Connection inference test',f'Send a small tool test to {c.id}/{c.model}? This may incur a small charge.',token):
                raise ValueError('Model check cancelled.')
            provider = Provider(replace(c, tools=True),secret or self.app.credentials.get(c.id),self.app.redactor)
            tool = {'name':'connection_probe','description':'Connection test only; no computer action.',
                    'parameters':{'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}}
            answer,_ = provider.complete([{'role':'user','content':'Call connection_probe with value buddy-test.'}],[tool],token,local_only=local)
            return any(x['name']=='connection_probe' and x['arguments'].get('value')=='buddy-test' for x in answer.get('tool_calls',[]))
        def done(valid):
            if valid:self.tools.set_active(True)
            self.ready()
            self.status.set_text('Model connected. Tool calling verified. Click Use model.' if valid else 'The model replied without using the test tool. This does not prove tools are unsupported; you can still use the model.')
        self.run(work,done)

    def save(self, use):
        try:c = self.config()
        except Exception as exc:self.status.set_text(str(exc));return
        if use and not c.model:return
        secret = self.key.get_text()
        self.status.set_text('Saving connection…')
        def work(token):
            if secret:
                token.check()
                self.app.redactor.register(secret)
                self.app.credentials.set(c.id,secret)
            return c
        def done(saved):
            self.app.store.save_config(saved)
            self.key.set_text('')
            self.status.set_text('Connection saved. Choose a model to continue.' if not saved.model else 'Saved. Use model to switch in this conversation.')
            if self.app.selected_id() == saved.id:
                self.app.provider_changed()
                if saved.model:self.status.set_text('Saved. This model is selected for your next message.')
            if use:
                self.app.select_model(saved)
                self.close()
        self.run(work,done)

    def remove_key(self):
        pid = self.selected_id()
        self.run(lambda t:self.app.credentials.set(pid,''), lambda _:self.status.set_text('Saved API key removed.'))

    def test_comfy(self):
        try:client = ComfyUI(self.comfy.get_text().strip())
        except Exception as exc:self.status.set_text(str(exc));return
        url = self.comfy.get_text().strip()
        def done(data):
            self.app.store.set('comfyui_url',url)
            self.status.set_text('ComfyUI connected · '+str(data.get('version') or 'version unavailable'))
        self.run(lambda token:client.status(token), done)
