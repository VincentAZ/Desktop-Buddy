"""Native conversation layout. No web runtime or background service."""
from __future__ import annotations

from pathlib import Path
import json
import re
from html import escape
import gi
from . import __version__

gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf, GLib, GObject, Gdk, Graphene, Gtk, Pango


MODE_TITLES = ['Discuss only', 'Ask before acting', 'Just do it']
PREVIEW_TITLE = 'Buddy’s latest look at your screen'
PREVIEW_SIZE = (160, 90)
INPUT_HEIGHT = 80
INPUT_MIN = 48
INPUT_GROW = 240
MODE_IDS = ['plan', 'execute', 'yolo']
SIDEBAR_WIDTH = 260
SIDEBAR_MIN = 180
SIDEBAR_SHARE = .7


def sidebar_width(saved, window_width=0):
    """The sidebar width to use: the width the person dragged it to, never narrower
    than SIDEBAR_MIN and, in a known window, never wider than SIDEBAR_SHARE of it so
    the conversation keeps room. Anything unreadable falls back to the default."""
    try:
        width = int(saved)
    except (TypeError, ValueError):
        width = SIDEBAR_WIDTH
    if window_width and window_width > 0:
        width = min(width, max(SIDEBAR_MIN, int(window_width * SIDEBAR_SHARE)))
    return max(SIDEBAR_MIN, width)


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


def menu_button(icon, tooltip, actions, text=None):
    """An icon menu button, or with text a labelled one: people looking for a way to
    copy did not find a bare icon."""
    widget = Gtk.MenuButton(icon_name=icon) if text is None else Gtk.MenuButton()
    if text is not None:
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Image.new_from_icon_name(icon))
        row.append(label(text))
        widget.set_child(row)
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


class Thumbnail(GObject.Object, Gdk.Paintable):
    """Draws a texture but asks for a small size, so a full-size screenshot cannot widen its card."""

    def __init__(self, texture, size):
        super().__init__()
        self.texture, self.size = texture, size

    def do_get_intrinsic_width(self):
        return self.size[0]

    def do_get_intrinsic_height(self):
        return self.size[1]

    def do_snapshot(self, snapshot, width, height):
        self.texture.snapshot(snapshot, width, height)


def thumbnail(path, size=PREVIEW_SIZE):
    # Twice the shown size keeps it sharp on high-resolution screens.
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size[0] * 2, size[1] * 2, True)
    return Thumbnail(Gdk.Texture.new_for_pixbuf(pixbuf), size)


class Lightbox:
    """Shows a picture over the whole window. Click anywhere, Close, or Esc returns."""

    def __init__(self, window):
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.widget.add_css_class('lightbox')
        self.widget.set_visible(False)
        top = Gtk.Box(spacing=12)
        self.caption = label('', 'lightbox-caption', ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        top.append(self.caption)
        self.close_button = button('Close', lambda *_: self.close(), 'lightbox-close', 'window-close-symbolic')
        top.append(self.close_button)
        self.widget.append(top)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.widget.append(self.picture)
        hint = label('Click anywhere or press Esc to go back', 'lightbox-hint')
        hint.set_halign(Gtk.Align.CENTER)
        self.widget.append(hint)
        click = Gtk.GestureClick()
        click.connect('released', lambda *_: self.close())
        self.widget.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self.key)
        window.add_controller(keys)

    def open(self, path, caption):
        self.picture.set_filename(str(path))
        self.picture.update_property([Gtk.AccessibleProperty.LABEL], [caption])
        self.caption.set_text(caption)
        self.widget.set_visible(True)
        self.close_button.grab_focus()

    def close(self):
        self.widget.set_visible(False)

    def key(self, _controller, keyval, *_):
        if self.widget.get_visible() and keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False


def scroller(child, expand=True, minimum=0):
    widget = Gtk.ScrolledWindow()
    widget.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    widget.set_vexpand(expand)
    widget.set_min_content_height(minimum)
    widget.set_child(child)
    return widget


def resizable_sidebar(app, root, sidebar):
    """Let the person drag the line between the sidebar and the conversation to any
    width they like. The width is remembered; double-clicking the line resets it."""
    root.set_start_child(sidebar)
    root.set_resize_start_child(False)
    root.set_shrink_start_child(False)
    root.set_position(sidebar_width(app.store.get('sidebar_width', SIDEBAR_WIDTH)))
    pending = {}
    def save_width():
        pending.pop('id', None)
        position = root.get_position()
        # A position pinned at the largest allowed value usually means the window
        # got smaller, not that the person chose it, so it is not remembered.
        if sidebar.get_visible() and root.get_mapped() and position < root.get_property('max-position'):
            app.store.set('sidebar_width', sidebar_width(position))
        return False
    def position_changed(*_):
        if 'id' in pending:
            GLib.source_remove(pending['id'])
        pending['id'] = GLib.timeout_add(400, save_width)
    root.connect('notify::position', position_changed)
    reset = Gtk.GestureClick()
    def reset_width(_gesture, presses, x, _y):
        if presses == 2 and sidebar.get_visible() and abs(x - root.get_position()) <= 8:
            root.set_position(SIDEBAR_WIDTH)
            app.store.set('sidebar_width', SIDEBAR_WIDTH)
    reset.connect('pressed', reset_width)
    root.add_controller(reset)


def build_workspace(app, providers, text_view, default_workspace):
    """Native shell with model and appearance controls beside the conversation."""
    header = Gtk.HeaderBar()
    title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    title.append(label('Desktop Buddy', 'conversation-title'))
    app.version_label = label('Version '+__version__, 'muted')
    app.version_label.set_halign(Gtk.Align.CENTER)
    title.append(app.version_label)
    header.set_title_widget(title)
    history_toggle = button('Conversations', lambda *_: app.sidebar.set_visible(not app.sidebar.get_visible()), icon='sidebar-show-symbolic')
    header.pack_start(history_toggle)
    header.pack_start(button('Connect apps', lambda *_: app.mcp_dialog(), icon='network-server-symbolic'))
    app.stop_button = button('Stop everything', lambda *_: app.emergency('the Stop everything button'), 'flat')
    app.stop_button.add_css_class('stop-button')
    app.stop_button.set_tooltip_text('Stop actions and disconnect computer control · Ctrl+Shift+Escape')
    header.pack_end(app.stop_button)
    app.window.set_titlebar(header)
    shell = Gtk.Overlay()
    # The sidebar and the conversation share a split view: the line between them
    # can be dragged to make the sidebar any width.
    app.sidebar_split = root = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
    root.add_css_class('sidebar-split')
    shell.set_child(root)
    app.window.set_child(shell)
    app.lightbox = Lightbox(app.window)
    shell.add_overlay(app.lightbox.widget)
    app.sidebar = sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    sidebar.set_size_request(SIDEBAR_MIN, -1)
    sidebar.add_css_class('sidebar')
    sidebar.set_visible(False)
    resizable_sidebar(app, root, sidebar)
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
    app.restart_button = button('Restart Buddy', lambda *_: app.restart_app(), icon='view-refresh-symbolic')
    app.restart_button.set_tooltip_text('Close Buddy and open it again. Your conversations are kept. (Ctrl+Shift+R)')
    sidebar.append(app.restart_button)

    main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
    root.set_end_child(main)
    root.set_resize_end_child(True)
    root.set_shrink_end_child(False)
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
    toolbar.append(app.model_menu.button)
    from .themes import THEMES
    app.theme_menu=Gtk.MenuButton(icon_name='applications-graphics-symbolic')
    app.theme_menu.set_tooltip_text('Choose a theme')
    app.theme_menu.update_property([Gtk.AccessibleProperty.LABEL],['Choose a theme'])
    app.theme_menu.add_css_class('flat')
    theme_popover=Gtk.Popover()
    theme_box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6)
    theme_box.append(label('THEME','eyebrow'))
    app.theme_choices={};theme_group=None
    for key,(name,_palette) in THEMES.items():
        choice=Gtk.CheckButton(label=name)
        choice.add_css_class('menu-choice')
        if theme_group:choice.set_group(theme_group)
        else:theme_group=choice
        choice.set_active(key==app.theme)
        choice.connect('toggled',lambda row,k=key:app.apply_theme(k) if row.get_active() and app.theme!=k else None)
        app.theme_choices[key]=choice;theme_box.append(choice)
    theme_popover.set_child(theme_box);app.theme_menu.set_popover(theme_popover)
    toolbar.append(app.theme_menu)
    toolbar.append(icon_button('emblem-system-symbolic','Settings',lambda *_:app.settings_dialog()))
    app.copy_menu = menu_button('edit-copy-symbolic', 'Copy conversation', [
        ('Copy whole conversation', app.copy_conversation),
        ('Copy everything, including work details', lambda: app.copy_conversation(everything=True)),
        ('Copy what Buddy sees (all messages in its context window)', app.copy_context_window),
        ('Copy only my messages', lambda: app.copy_conversation('user')),
        ('Copy only Buddy’s replies', lambda: app.copy_conversation('assistant')),
    ], text='Copy')
    toolbar.append(app.copy_menu)
    toolbar.append(menu_button('view-more-symbolic', 'Conversation options', [
        ('Rename conversation', app.rename_conversation_dialog),
        ('Copy whole conversation', app.copy_conversation),
        ('Copy everything, including work details', lambda: app.copy_conversation(everything=True)),
        ('Continue unfinished task', app.resume),
        ('Delete conversation…', app.delete_conversation_dialog),
    ]))
    main.append(toolbar)

    app.transcript = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    app.transcript.add_css_class('conversation')
    app.transcript_scroll = scroller(app.transcript, minimum=140)
    app.transcript_scroll.add_css_class('transcript-scroll')
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
    footer.set_margin_top(8)
    footer.set_margin_bottom(20)
    main.append(footer)
    # The newest screenshot Buddy took. It is not part of any message, so it is
    # its own labelled card that opens full size on click.
    app.preview_box = Gtk.Button(halign=Gtk.Align.START)
    app.preview_box.add_css_class('screen-preview')
    app.preview_box.set_tooltip_text('See the screenshot bigger')
    app.preview_box.update_property([Gtk.AccessibleProperty.LABEL], ['See Buddy’s latest screenshot of your screen bigger'])
    preview_row = Gtk.Box(spacing=12)
    app.preview = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
    app.preview.set_size_request(PREVIEW_SIZE[0], PREVIEW_SIZE[1])
    preview_row.append(app.preview)
    preview_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
    preview_text.append(label(PREVIEW_TITLE, 'preview-title'))
    app.preview_time = label('', 'muted')
    preview_text.append(app.preview_time)
    preview_row.append(preview_text)
    app.preview_box.set_child(preview_row)
    app.preview_box.connect('clicked', lambda *_: app.show_image(app.preview_path, PREVIEW_TITLE))
    app.preview_box.set_visible(False)
    footer.append(app.preview_box)
    app.restart_bar = Gtk.Box(spacing=8)
    app.restart_bar.add_css_class('restart-bar')
    app.restart_label = label('Buddy’s code has changed. Restart to use the new version.', 'status', wrap=True)
    app.restart_label.set_hexpand(True)
    app.restart_bar.append(app.restart_label)
    app.restart_bar.append(button('Restart Buddy', lambda *_: app.restart_app(), 'suggested-action'))
    app.restart_bar.set_visible(False)
    footer.append(app.restart_bar)
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
    composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    composer.add_css_class('composer')
    handle = Gtk.Box()
    handle.add_css_class('resize-handle')
    grip = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True)
    grip.add_css_class('resize-grip')
    handle.append(grip)
    handle.set_cursor_from_name('ns-resize')
    handle.set_tooltip_text('Drag to make the message box taller or shorter · double-click to reset')
    composer.append(handle)
    app.input = text_view(True)
    app.input.update_property([Gtk.AccessibleProperty.LABEL], ['Message Buddy'])
    app.input.set_tooltip_text('Tell Buddy what you need. Ctrl+Enter to send.')
    overlay = Gtk.Overlay()
    input_scroll = scroller(app.input, False, INPUT_HEIGHT)
    # Grow with what is typed, and let the person drag the box taller or shorter.
    input_scroll.set_propagate_natural_height(True)
    app.input_scroll = input_scroll
    app.input_height = app.store.get('input_height', INPUT_HEIGHT)
    def apply_input_height():
        # The chosen height is what the box asks for, not a minimum: in a short
        # window it gives way to the conversation instead of making the window grow.
        cap = max(INPUT_HEIGHT, int((app.window.get_height() or 860) * .6))
        height = max(INPUT_MIN, min(app.input_height, cap))
        app.input.set_size_request(-1, height)
        input_scroll.set_max_content_height(max(height, INPUT_GROW))
        input_scroll.set_min_content_height(min(height, INPUT_HEIGHT))
    app.apply_input_height = apply_input_height
    drag = Gtk.GestureDrag()
    start = {}
    def window_y(x, y):
        found, point = handle.compute_point(app.window, Graphene.Point().init(x, y))
        return point.y if found else y
    def drag_begin(_gesture, x, y):
        # Track the pointer in window coordinates: the handle itself moves as the
        # box grows, so its own coordinates would fight the drag.
        start['y'] = window_y(x, y)
        start['height'] = max(input_scroll.get_height(), INPUT_MIN)
    def drag_update(gesture, dx, dy):
        found, x, y = gesture.get_start_point()
        if not found or 'y' not in start:return
        app.input_height = int(start['height'] + start['y'] - window_y(x + dx, y + dy))
        apply_input_height()
    def drag_end(*_):
        start.clear()
        app.input_height = max(INPUT_MIN, min(app.input_height, int((app.window.get_height() or 860) * .6)))
        app.store.set('input_height', app.input_height)
    drag.connect('drag-begin', drag_begin)
    drag.connect('drag-update', drag_update)
    drag.connect('drag-end', drag_end)
    handle.add_controller(drag)
    reset = Gtk.GestureClick()
    def reset_height(_gesture, presses, *_):
        if presses == 2:
            app.input_height = INPUT_HEIGHT
            app.store.set('input_height', INPUT_HEIGHT)
            apply_input_height()
    reset.connect('pressed', reset_height)
    handle.add_controller(reset)
    apply_input_height()
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
            apply_input_height()
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
        box.append(copy_heading(author, lambda: on_copy(text, 'Your message' if who in ('YOU', 'USER') else 'Buddy’s reply')))
    body = label(text, 'message-body', wrap=True, selectable=True)
    if who not in ('YOU','USER'):body.set_markup(message_markup(text))
    body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    box.append(body)
    return box


def copy_heading(title_widget, on_copy):
    """A card heading with a plain "Copy" button on the right. The button is always
    visible: a faint icon was not found by people looking for a way to copy."""
    heading = Gtk.Box(spacing=6)
    title_widget.set_hexpand(True)
    title_widget.set_valign(Gtk.Align.CENTER)
    heading.append(title_widget)
    copy = button('Copy', lambda *_: on_copy(), icon='edit-copy-symbolic')
    copy.set_tooltip_text('Copy this text')
    copy.add_css_class('copy-message')
    heading.append(copy)
    return heading


def activity_card(who, text, on_copy=None):
    from .experience import activity_summary
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class('tool-card')
    if who == 'ERROR':box.add_css_class('error-card')
    title, explanation = activity_summary(who,text)
    heading = label(title,'step-title',wrap=True)
    if on_copy is None:box.append(heading)
    else:box.append(copy_heading(heading, lambda: on_copy(title+'\n'+explanation+'\n\n'+text, 'This step')))
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
        picture.set_cursor_from_name('zoom-in')
        click = Gtk.GestureClick()
        click.connect('released', lambda *_: on_open(file))
        picture.add_controller(click)
        box.append(picture)
    else:
        missing = label('The saved image is no longer available on disk.', 'muted', wrap=True)
        missing.set_tooltip_text(str(file))
        box.append(missing)
    return box
