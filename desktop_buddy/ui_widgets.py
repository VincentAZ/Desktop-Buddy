"""Native conversation layout. No web runtime or background service."""
from __future__ import annotations

from pathlib import Path
import json
import re
from html import escape
import gi
from . import __version__

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk, Pango


CSS = '''
window { background: #f8f7f3; color: #263b35; }
headerbar { background: #f8f7f3; box-shadow: none; border-bottom: 1px solid #e2e5dd; }
button { background-image: none; box-shadow: none; border: 0; border-radius: 10px;
         padding: 10px 14px; background-color: #eaece5; color: #263b35; }
button:hover { background-color: #dde4d9; }
button.flat { background: transparent; }
button.flat:hover { background: #eaece5; }
button.suggested-action { background: #315c49; color: #ffffff; font-weight: 600; }
button.suggested-action:hover { background: #234735; }
button.destructive-action { background: #a03f35; color: #ffffff; }
button:disabled { opacity: .45; }
.sidebar { background: #eeefe8; border-right: 1px solid #dfe3d8; padding: 20px 14px; }
.brand { font-size: 20px; font-weight: 700; letter-spacing: -.4px; }
.muted { color: #65736b; }
.eyebrow { color: #65736b; font-size: 11px; font-weight: 600; letter-spacing: 1.2px; }
.conversation-title { font-size: 15px; font-weight: 600; }
.sidebar list { background: transparent; }
.sidebar row { border-radius: 10px; padding: 12px 10px; margin: 3px 0; }
.sidebar row:selected { background: #dce5d6; color: #263b35; }
.sidebar row:hover { background: #e3e8dd; }
entry, searchentry, dropdown > button { background: #ffffff; border: 1px solid #d8ded3; border-radius: 9px; }
entry, searchentry { padding: 6px 8px; }
.toolbar { padding: 16px 24px; }
.conversation { padding: 20px 0; }
.message { padding: 12px 0; }
.message-body { font-size: 16px; line-height: 1.4; }
.user-message { background: #e9eee4; border-radius: 18px; padding: 18px 22px; margin-left: 48px; }
.message-author { font-size: 12px; font-weight: 700; color: #42604b; }
button.copy-message { min-height: 22px; min-width: 22px; padding: 2px; opacity: .55; }
button.copy-message:hover { opacity: 1; }
.tool-card { border: 1px solid #dce2d6; border-radius: 12px; padding: 12px 16px; background: #f1f3ed; }
.tool-card label { font-size: 13px; }
.error-card { border-color: #d8afa0; background: #fff1e9; color: #6c392d; }
.composer { background: #ffffff; border: 1px solid #bdccbb; border-radius: 20px; padding: 12px; box-shadow: 0 4px 18px alpha(#263b35, .05); }
textview, textview text { background: transparent; color: #263b35; }
textview { padding: 4px; }
.composer textview { font-size: 16px; }
.composer dropdown > button { border: 0; background: #eef2e9; padding: 6px 10px; }
.attachment { background: #eaece5; border-radius: 8px; padding: 5px; }
.welcome-title { font-size: 36px; font-weight: 600; letter-spacing: -1px; }
.welcome { padding: 16px 6px; }
.compact .welcome { padding: 4px 6px; }
.compact .welcome-mark { padding: 8px; }
.compact .welcome-title { font-size: 28px; }
.welcome-subtitle { font-size: 16px; color: #65736b; }
.welcome-mark { background: #e1e9d9; color: #315c49; border-radius: 20px; padding: 18px; }
.suggestion { background: #ffffff; border: 1px solid #dce2d6; padding: 16px; }
.suggestion:hover { background: #eef2e9; }
button.jump-to-end { background: #ffffff; color: #263b35; border: 1px solid #c9d2c6; border-radius: 999px; padding: 10px; min-width: 20px; min-height: 20px; box-shadow: 0 3px 12px alpha(#263b35, .18); }
button.jump-to-end:hover { background: #eef2e9; }
.status { color: #65736b; font-size: 12px; }
.status.attention { color: #9c4139; font-size: 13px; font-weight: 700; }
.stop-button { color: #9c4139; }
.settings-section { background: #ffffff; border: 1px solid #dce2d6; border-radius: 12px; padding: 16px; }
.step-title { font-size: 16px; font-weight: 600; }
.model-choice { padding: 6px; border-radius: 10px; background: #ffffff; }
.model-choice row { border-radius: 8px; }
.model-choice row:selected { background: #dce8d7; color: #263b35; }
.model-choice row:hover { background: #eef2e9; }
popover contents { background: #f8f7f3; padding: 12px; border: 1px solid #d8ded3; }
menubutton.model-menu > button { background: #ffffff; border: 1px solid #d8ded3; padding: 8px 12px; }
menubutton.model-menu > button:hover { background: #eef2e9; }
checkbutton.menu-choice { padding: 6px 4px; border-radius: 8px; }
checkbutton.menu-choice:hover { background: #eef2e9; }
checkbutton.menu-choice:checked { background: #e1e9d9; }
'''


MODE_TITLES = ['Discuss only', 'Ask before acting', 'Just do it']
MODE_IDS = ['plan', 'execute', 'yolo']


def label(text='', css=None, **kwargs):
    widget = Gtk.Label(label=text, xalign=0, **kwargs)
    if css:
        widget.add_css_class(css)
    return widget


def button(text, callback, css='flat', icon=None):
    widget = Gtk.Button(label=text)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    if icon:
        row = Gtk.Box(spacing=9)
        row.append(Gtk.Image.new_from_icon_name(icon))
        row.append(label(text))
        widget.set_child(row)
    if css:
        widget.add_css_class(css)
    widget.connect('clicked', callback)
    return widget


def icon_button(icon, tooltip, callback):
    widget = Gtk.Button.new_from_icon_name(icon)
    widget.set_tooltip_text(tooltip)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    widget.add_css_class('flat')
    widget.connect('clicked', callback)
    return widget


def menu_button(icon, tooltip, actions):
    widget = Gtk.MenuButton(icon_name=icon)
    widget.set_tooltip_text(tooltip)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    widget.add_css_class('flat')
    popover = Gtk.Popover()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    for title, callback in actions:
        def activate(_button, action=callback):
            popover.popdown()
            action()
        box.append(button(title, activate))
    popover.set_child(box)
    widget.set_popover(popover)
    return widget


def scroller(child, expand=True, minimum=0):
    widget = Gtk.ScrolledWindow()
    widget.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    widget.set_vexpand(expand)
    widget.set_min_content_height(minimum)
    widget.set_child(child)
    return widget


def build_workspace(app, providers, text_view, default_workspace):
    """Task-first native shell. Technical controls live in one options popover."""
    header = Gtk.HeaderBar()
    title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    title.append(label('Desktop Buddy', 'conversation-title'))
    header.set_title_widget(title)
    history_toggle = button('Conversations', lambda *_: app.sidebar.set_visible(not app.sidebar.get_visible()), icon='sidebar-show-symbolic')
    header.pack_start(history_toggle)
    header.pack_start(button('Connect apps', lambda *_: app.mcp_dialog(), icon='network-server-symbolic'))
    app.stop_button = button('Stop everything', lambda *_: app.emergency('the Stop everything button'), 'flat')
    app.stop_button.add_css_class('stop-button')
    app.stop_button.set_tooltip_text('Stop actions and disconnect computer control · Ctrl+Shift+Escape')
    header.pack_end(app.stop_button)
    app.window.set_titlebar(header)
    root = Gtk.Box()
    app.window.set_child(root)
    app.sidebar = sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    sidebar.set_size_request(210, -1)
    sidebar.add_css_class('sidebar')
    sidebar.set_visible(False)
    root.append(sidebar)
    sidebar.append(button('New conversation', lambda *_: app.new_conversation(), 'suggested-action', 'list-add-symbolic'))
    app.history_search = Gtk.SearchEntry(placeholder_text='Find a conversation')
    app.history_search.update_property([Gtk.AccessibleProperty.LABEL], ['Search conversations'])
    app.history_search.connect('search-changed', lambda *_: app.refresh_history())
    sidebar.append(app.history_search)
    sidebar.append(label('YOUR CONVERSATIONS', 'eyebrow'))
    app.history = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
    app.history.connect('row-selected', app.select_history)
    sidebar.append(scroller(app.history))
    app.desktop_button = button('Computer access', lambda *_: app.connect_desktop(), icon='video-display-symbolic')
    sidebar.append(app.desktop_button)
    sidebar.append(button('Work folder', lambda *_: app.open_work_folder(), icon='folder-open-symbolic'))
    sidebar.append(button('Things to remember', lambda *_: app.memory_dialog(), icon='document-properties-symbolic'))
    sidebar.append(button('Recent activity', lambda *_: app.audit_dialog(), icon='document-open-recent-symbolic'))
    sidebar.append(button('Settings', lambda *_: app.settings_dialog(), icon='emblem-system-symbolic'))
    sidebar.append(button('Connected apps', lambda *_: app.mcp_dialog(), icon='network-server-symbolic'))

    main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
    root.append(main)
    toolbar = Gtk.Box(spacing=8)
    toolbar.add_css_class('toolbar')
    toolbar.append(icon_button('list-add-symbolic', 'New conversation', lambda *_: app.new_conversation()))
    app.conversation_title = label('New conversation', 'conversation-title', ellipsize=Pango.EllipsizeMode.END, single_line_mode=True)
    app.conversation_title.set_hexpand(True)
    app.conversation_title.set_max_width_chars(30)
    toolbar.append(app.conversation_title)
    app.provider_ids = list(providers)
    app.selector = Gtk.DropDown.new_from_strings([v[0] for v in providers.values()])
    chosen = app.store.get('provider', 'lmstudio')
    app.selector.set_selected(app.provider_ids.index(chosen) if chosen in app.provider_ids else 5)
    app.selector.connect('notify::selected', app.provider_changed)
    from .model_ui import ModelMenu
    app.model_menu = ModelMenu(app)
    app.assistant_status=label('', 'muted',ellipsize=Pango.EllipsizeMode.END)
    app.assistant_status.set_max_width_chars(24)
    app.assistant_status.set_tooltip_text('Your assistant is set up by IT. Open Settings for details.')
    toolbar.append(app.assistant_status)
    toolbar.append(icon_button('emblem-system-symbolic','Settings',lambda *_:app.settings_dialog()))
    toolbar.append(menu_button('view-more-symbolic', 'Conversation options', [
        ('Rename conversation', app.rename_conversation_dialog),
        ('Copy whole conversation', app.copy_conversation),
        ('Copy only my messages', lambda: app.copy_conversation('user')),
        ('Copy only Buddy’s replies', lambda: app.copy_conversation('assistant')),
        ('Continue unfinished task', app.resume),
        ('Delete conversation…', app.delete_conversation_dialog),
    ]))
    main.append(toolbar)

    app.transcript = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    app.transcript.add_css_class('conversation')
    app.transcript_scroll = scroller(app.transcript, minimum=140)
    conversation = Gtk.Overlay()
    conversation.set_child(app.transcript_scroll)
    app.jump_button = Gtk.Button.new_from_icon_name('go-bottom-symbolic')
    app.jump_button.add_css_class('jump-to-end')
    app.jump_button.set_tooltip_text('Jump to the end of the conversation')
    app.jump_button.update_property([Gtk.AccessibleProperty.LABEL], ['Jump to the end of the conversation'])
    app.jump_button.set_halign(Gtk.Align.CENTER)
    app.jump_button.set_valign(Gtk.Align.END)
    app.jump_button.set_margin_bottom(14)
    app.jump_button.set_visible(False)
    app.jump_button.connect('clicked', lambda *_: app.scroll_end())
    conversation.add_overlay(app.jump_button)
    adjustment = app.transcript_scroll.get_vadjustment()
    # Stay pinned to the newest message while content grows (opening a long
    # conversation, streaming a reply) until the person scrolls up themselves.
    pinned = {'end': True}
    def update_jump(*_):
        away = adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value()
        pinned['end'] = away < 120
        app.jump_button.set_visible(away > 120)
    def content_changed(*_):
        if pinned['end']:
            adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
            # This signal fires during the viewport's own layout pass, and a
            # value set here is recorded but not always applied to that pass: the
            # scrollbar then reads "at the end" while the newest card stays below
            # the fold. Re-run the layout once this pass is over so it lands.
            GLib.idle_add(lambda: pinned['end'] and app.scroll_end())
        update_jump()
    adjustment.connect('value-changed', update_jump)
    adjustment.connect('changed', content_changed)
    main.append(conversation)
    app.stream_view = text_view()
    app.stream_scroll = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    app.stream_scroll.add_css_class('message')
    app.stream_scroll.append(label('Buddy', 'message-author'))
    app.stream_scroll.append(app.stream_view)
    app.stream_scroll.set_visible(False)

    footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    footer.set_margin_bottom(20)
    main.append(footer)
    app.preview = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
    app.preview.set_size_request(-1, 140)
    app.preview_box = Gtk.Expander(label='View computer screenshot')
    app.preview_box.set_child(app.preview)
    app.preview_box.set_visible(False)
    footer.append(app.preview_box)
    status_row = Gtk.Box(spacing=8)
    app.spinner = Gtk.Spinner()
    status_row.append(app.spinner)
    app.status = label('Ready when you are', 'status', wrap=True, lines=3, ellipsize=Pango.EllipsizeMode.END)
    app.status.set_hexpand(True)
    status_row.append(app.status)
    app.recovery_button = button('Continue task', lambda *_: app.resume())
    app.recovery_button.set_visible(False)
    status_row.append(app.recovery_button)
    footer.append(status_row)
    composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    composer.add_css_class('composer')
    app.input = text_view(True)
    app.input.update_property([Gtk.AccessibleProperty.LABEL], ['Message Buddy'])
    app.input.set_tooltip_text('Tell Buddy what you need. Ctrl+Enter to send.')
    overlay = Gtk.Overlay()
    input_scroll = scroller(app.input, False, 80)
    input_scroll.set_max_content_height(180)
    overlay.set_child(input_scroll)
    placeholder = label('Tell me what you’d like help with…', 'muted')
    placeholder.set_halign(Gtk.Align.START)
    placeholder.set_valign(Gtk.Align.START)
    placeholder.set_margin_top(12)
    placeholder.set_margin_start(12)
    placeholder.set_can_target(False)
    app.input.get_buffer().connect('changed', lambda b: placeholder.set_visible(b.get_char_count() == 0))
    overlay.add_overlay(placeholder)
    composer.append(overlay)
    app.attachments = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,column_spacing=8,row_spacing=8,max_children_per_line=4,min_children_per_line=1)
    app.attachments.set_visible(False)
    composer.append(app.attachments)
    keys = Gtk.EventControllerKey()
    keys.connect('key-pressed', app.input_key)
    app.input.add_controller(keys)
    controls = Gtk.Box(spacing=8)
    app.mode = Gtk.DropDown.new_from_strings(MODE_TITLES)
    app.mode.update_property([Gtk.AccessibleProperty.LABEL], ['How Buddy can help'])
    saved_mode = app.store.get('mode', 'execute')
    app.mode.set_selected({'plan': 0, 'execute': 1, 'yolo': 2}.get(saved_mode, 1))
    app.mode.set_tooltip_text('Discuss only: Buddy reads and explains, changes nothing. Ask before acting: Buddy works in its work folder and asks you before anything else. Just do it: Buddy acts without asking. Stop always works.')
    app.mode.connect('notify::selected', app.mode_changed)
    controls.append(app.mode)
    controls.append(icon_button('mail-attachment-symbolic', 'Attach a file', lambda *_: app.choose_image()))
    options = Gtk.MenuButton(icon_name='video-display-symbolic')
    options.set_tooltip_text('Computer and work folder')
    options.update_property([Gtk.AccessibleProperty.LABEL], ['Computer and work folder'])
    options.add_css_class('flat')
    pop = Gtk.Popover()
    settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    settings.set_size_request(320, -1)
    settings.append(label('Your computer', 'step-title'))
    app.local = Gtk.CheckButton(label='Keep Buddy offline')
    app.local.set_active(app.store.get('local_only', True))
    app.local.set_tooltip_text('Use a trusted local model. Block network tools, app launch and desktop input; isolate shell networking. Observation and workspace file tools remain available.')
    app.local.connect('toggled', app.local_changed)
    saved_workspace = app.store.get('workspace', str(default_workspace))
    if not Path(saved_workspace).expanduser().is_dir():
        # The saved folder was moved or deleted (for example a renamed checkout).
        # Fall back to the default instead of blocking every Send on a dead path.
        Path(default_workspace).mkdir(parents=True, exist_ok=True)
        saved_workspace = str(default_workspace)
        app.store.set('workspace', saved_workspace)
    app.workspace = Gtk.Entry(text=saved_workspace)
    app.workspace.update_property([Gtk.AccessibleProperty.LABEL], ['Working folder'])
    app.workspace.connect('activate', lambda *_: (app.save_preferences(), pop.popdown()))
    settings.append(button('Open work folder', lambda *_: (pop.popdown(), app.open_work_folder())))
    settings.append(button('Let Buddy use my computer', lambda *_: (pop.popdown(), app.connect_desktop())))
    settings.append(button('Take a screenshot', lambda *_: (pop.popdown(), app.observe())))
    app.usage = label('', 'status', wrap=True)
    pop.set_child(settings)
    options.set_popover(pop)
    controls.append(options)
    controls.append(Gtk.Box(hexpand=True))
    app.cancel_button = button('Stop', lambda *_: app.cancel('the Stop button'))
    app.cancel_button.set_visible(False)
    controls.append(app.cancel_button)
    app.run_button = button('Send', lambda *_: app.send(), 'suggested-action')
    app.run_button.set_tooltip_text('Send · Ctrl+Enter')
    controls.append(app.run_button)
    composer.append(controls)
    footer.append(composer)
    app.disclosure = label('', 'status', wrap=True)
    footer.append(app.disclosure)

    # Keep a comfortable reading measure at wide sizes, without a fixed minimum
    # that forces smaller windows wider than the screen.
    last_width = [0]
    def resize(widget, _clock):
        width = widget.get_width()
        size = (width, widget.get_height())
        if size != last_width[0]:
            last_width[0] = size
            if size[1] < 680:widget.add_css_class('compact')
            else:widget.remove_css_class('compact')
            if getattr(app, 'welcome_mark', None):app.welcome_mark.set_visible(size[1] >= 680)
            margin = max(20, (width - 800) // 2)
            for child in (app.transcript, footer):
                child.set_margin_start(margin)
                child.set_margin_end(margin)
        return True
    main.add_tick_callback(resize)


def welcome(app):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    box.add_css_class('welcome')
    mark = Gtk.Image.new_from_icon_name('face-smile-symbolic')
    mark.set_pixel_size(32)
    mark.set_halign(Gtk.Align.START)
    mark.add_css_class('welcome-mark')
    app.welcome_mark = mark
    box.append(mark)
    configured=bool(app.store.configs()[app.selected_id()].model)
    box.append(label('What can I help you with?' if configured else 'Welcome to Desktop Buddy', 'welcome-title', wrap=True))
    box.append(label('Describe the result you want. You can review the work and stop Buddy at any time.' if configured else 'One step before we start: choose which AI service Buddy should use.', 'welcome-subtitle', wrap=True))
    if not configured:
        setup = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        setup.add_css_class('settings-section')
        setup.append(label('Choose an assistant', 'step-title'))
        setup.append(label('Pick a service such as OpenAI, Anthropic, or a model running on this computer, and enter its key if it needs one. This is usually done once by whoever set up this computer.', 'muted', wrap=True))
        connect = button('Set up the assistant', lambda *_: app.model_dialog(), 'suggested-action')
        connect.set_halign(Gtk.Align.START)
        setup.append(connect)
        box.append(setup)
    else:
        box.append(label('A FEW IDEAS TO GET STARTED', 'eyebrow'))
        ideas = Gtk.Box(spacing=10, homogeneous=True)
        box.append(ideas)
        for title, description, prompt, icon in [
            ('Make sense of my files', 'Find what matters in a folder', 'Help me understand and organize the files in my working folder. Show me a plan before moving anything.', 'folder-documents-symbolic'),
            ('Use my apps', 'Do something in an app on this computer', 'Help me with a task in one of my apps. Ask which app, what I want to achieve, and what must stay unchanged.', 'preferences-desktop-apps-symbolic'),
            ('Think it through', 'Turn a to-do into a clear next step', 'Help me break a task into manageable steps. Ask me what I am trying to accomplish.', 'dialog-information-symbolic'),
        ]:
            def choose(_button, text=prompt):
                app.input.get_buffer().set_text(text)
                app.input.grab_focus()
            choice = button(title, choose, 'suggestion')
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            glyph = Gtk.Image.new_from_icon_name(icon)
            glyph.set_halign(Gtk.Align.START)
            row.append(glyph)
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            words.append(label(title, 'step-title', wrap=True))
            words.append(label(description, 'muted', wrap=True))
            row.append(words)
            choice.set_child(row)
            ideas.append(choice)
    return box


def message_markup(text):
    """Render common response formatting without trusting model-supplied markup."""
    def inline(value):
        parts = re.split(r'(`[^`\n]+`)', value)
        rendered = []
        for part in parts:
            if part.startswith('`') and part.endswith('`'):
                rendered.append('<tt>'+escape(part[1:-1])+'</tt>')
            else:
                safe = escape(part)
                safe = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', safe)
                rendered.append(safe)
        return ''.join(rendered)
    output = [];code = False
    for line in text.splitlines():
        if line.lstrip().startswith('```'):
            code = not code
            continue
        if code:output.append('<tt>'+escape(line)+'</tt>')
        elif re.match(r'^#{1,6} ', line):output.append('<b>'+inline(line.lstrip('#').strip())+'</b>')
        elif re.match(r'^\s*[-*] ', line):output.append('• '+inline(line.lstrip()[2:]))
        else:output.append(inline(line))
    return '\n'.join(output)


def message_card(who, text, on_copy=None):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class('message')
    if who in ('YOU', 'USER'):
        box.add_css_class('user-message')
    author = label('You' if who in ('YOU', 'USER') else 'Desktop Buddy', 'message-author')
    if on_copy is None:
        box.append(author)
    else:
        heading = Gtk.Box(spacing=6)
        author.set_hexpand(True)
        author.set_valign(Gtk.Align.CENTER)
        heading.append(author)
        copy = icon_button('edit-copy-symbolic', 'Copy this message', lambda *_: on_copy(text))
        copy.add_css_class('copy-message')
        heading.append(copy)
        box.append(heading)
    body = label(text, 'message-body', wrap=True, selectable=True)
    if who not in ('YOU','USER'):body.set_markup(message_markup(text))
    body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    box.append(body)
    return box


def activity_card(who, text):
    from .experience import activity_summary
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class('tool-card')
    if who == 'ERROR':box.add_css_class('error-card')
    title, explanation = activity_summary(who,text)
    box.append(label(title,'step-title',wrap=True))
    detail=label(explanation,'muted',wrap=True,selectable=True)
    detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR);box.append(detail)
    expander=Gtk.Expander(label='Technical details')
    raw=label(text,'muted',wrap=True,selectable=True)
    raw.set_wrap_mode(Pango.WrapMode.WORD_CHAR);raw.set_margin_top(10)
    expander.set_child(raw);box.append(expander)
    return box


def image_card(path, on_open):
    """Render a saved local observation without reading it into transcript text."""
    from pathlib import Path

    file = Path(path)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    box.add_css_class('tool-card')
    top = Gtk.Box(spacing=8)
    title = label('Visual observation', 'message-author')
    title.set_hexpand(True)
    top.append(title)
    if file.is_file():
        top.append(button('Open', lambda *_: on_open(file)))
    box.append(top)
    if file.is_file():
        picture = Gtk.Picture.new_for_filename(str(file))
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_size_request(-1, 220)
        picture.set_tooltip_text(str(file))
        picture.update_property([Gtk.AccessibleProperty.LABEL], ['Saved visual observation'])
        box.append(picture)
    else:
        missing = label('The saved image is no longer available on disk.', 'muted', wrap=True)
        missing.set_tooltip_text(str(file))
        box.append(missing)
    return box
