from __future__ import annotations
import json
import hashlib
import os
from pathlib import Path
import stat
import threading
import uuid
import gi
from PIL import Image

gi.require_version('Gtk','4.0')
gi.require_version('Gdk','4.0')
from gi.repository import Gtk,Gdk,Gio,GLib
from .agent import Agent,needs_user_reply
from .browser import Browser
from .desktop import Portal,Accessibility
from .providers import Provider
from . import __version__
from .security import Policy,Redactor,StopToken,Cancelled
from .state import Store,Credentials,PROVIDERS,thinking_levels
from .tools import Registry
from .mcp_client import MCPManager
from .paths import default_data_dir, default_workspace

from .ui_widgets import CSS, build_workspace, welcome, message_card, activity_card, image_card
def text_view(editable=False):
    v=Gtk.TextView(editable=editable,wrap_mode=Gtk.WrapMode.WORD_CHAR)
    v.set_left_margin(8);v.set_right_margin(8);v.set_top_margin(8);v.set_bottom_margin(8)
    return v

def scroll(child,min_height=100):
    s=Gtk.ScrolledWindow();s.set_child(child);s.set_vexpand(True);s.set_min_content_height(min_height)
    return s

def button(label,callback,css=None):
    b=Gtk.Button(label=label);b.connect('clicked',callback)
    if css:b.add_css_class(css)
    return b

def content(view):
    b=view.get_buffer();return b.get_text(b.get_start_iter(),b.get_end_iter(),True)

SETUP_NEEDED='Choose an assistant in Settings to get started'

class BuddyApp(Gtk.Application):
    def __init__(self,data_dir,smoke_test=False):
        resolved = Path(data_dir).resolve()
        app_id = 'io.desktopbuddy.App'
        if resolved != default_data_dir().resolve():
            app_id += '.p' + hashlib.sha256(str(resolved).encode()).hexdigest()[:20]
        super().__init__(application_id=app_id,flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.data_dir=resolved;self.smoke_test=smoke_test
        # Register before opening SQLite. A second launcher click activates the
        # existing process without attempting to acquire its state-directory lock.
        self.register(None)
        if self.get_is_remote():return
        self.redactor=Redactor();self.store=Store(self.data_dir,self.redactor);self.credentials=Credentials(self.redactor)
        self.mcp=MCPManager(self.store,self.credentials)
        self.agent=None;self.start_token=StopToken();self.worker=None;self.pending=[];self.desktop=None;self.a11y=None;self.aux_tokens=[]
        self.browser=Browser(self.data_dir/'browser-profile',headless=not self.store.get('browser_visible',True));self.cid=None;self.emergency_latched=False;self.window_handle='';self.task_usage={};self.pending_images=[];self.pending_files=[]
        self.connect('activate',self.activate)
        stop=Gio.SimpleAction.new('emergency',None);stop.connect('activate',lambda *_:self.emergency('the Ctrl+Shift+Escape shortcut'));self.add_action(stop)
        new=Gio.SimpleAction.new('new-conversation',None);new.connect('activate',lambda *_:self.new_conversation());self.add_action(new)
        search=Gio.SimpleAction.new('search-conversations',None);search.connect('activate',lambda *_:self.focus_history());self.add_action(search)
        settings=Gio.SimpleAction.new('provider-settings',None);settings.connect('activate',lambda *_:self.settings_dialog());self.add_action(settings)
        # Same code path as the Desktop control button, reachable from a terminal for
        # testing: gapplication action io.desktopbuddy.App connect-desktop
        connect=Gio.SimpleAction.new('connect-desktop',None);connect.connect('activate',lambda *_:self.connect_desktop());self.add_action(connect)
        self.set_accels_for_action('app.emergency',['<Primary><Shift>Escape'])
        self.set_accels_for_action('app.new-conversation',['<Primary>n'])
        self.set_accels_for_action('app.search-conversations',['<Primary>k'])
        self.set_accels_for_action('app.provider-settings',['<Primary>comma'])

    def activate(self,*_):
        # PyGObject prints exceptions raised by signal handlers but does not make
        # Application.run() fail. Preserve that failure for the launcher/smoke check.
        try:self._activate()
        except Exception as error:
            import sys
            self.startup_error=self.redactor.text(str(error))
            print('Desktop Buddy could not start: '+self.startup_error,file=sys.stderr)
            self.quit()

    def _activate(self):
        if hasattr(self,'window'):self.window.present();return
        display=Gdk.Display.get_default()
        if not display:raise RuntimeError('No physical desktop display. Launch inside the GNOME session.')
        Gtk.Settings.get_default().set_property('gtk-application-prefer-dark-theme',False)
        css=Gtk.CssProvider();css.load_from_data(CSS.encode());Gtk.StyleContext.add_provider_for_display(display,css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window=Gtk.ApplicationWindow(application=self,title='Desktop Buddy v'+__version__,default_width=self.store.get('window_width',1180),default_height=self.store.get('window_height',860))
        self.window.connect('close-request',self.close_requested)
        default = default_workspace(self.data_dir)
        default.mkdir(parents=True,exist_ok=True)
        build_workspace(self, PROVIDERS, text_view, default)
        self.refresh_history();self.restore_draft();self.provider_changed()
        self.window.present()
        self.export_window_handle()
        if self.smoke_test:GLib.timeout_add(2000,lambda:self.quit() or False)

    def export_window_handle(self):
        try:
            gi.require_version('GdkWayland','4.0')
            from gi.repository import GdkWayland
            surface=self.window.get_surface()
            if isinstance(surface,GdkWayland.WaylandToplevel):
                def exported(surface,handle,*_):
                    self.window_handle='wayland:'+handle
                    if self.desktop:self.desktop.parent_window=self.window_handle
                surface.export_handle(exported,None)
        except (ValueError,AttributeError):pass

    def input_key(self,controller,keyval,keycode,state):
        if keyval==Gdk.KEY_Return and state&Gdk.ModifierType.CONTROL_MASK:self.send();return True
        return False

    def selected_id(self):return self.provider_ids[self.selector.get_selected()]

    def provider_changed(self,*_):
        pid=self.selected_id();self.store.set('provider',pid);c=self.store.configs()[pid]
        self.refresh_thinking_selector(c)
        self.update_model_hint()
        self.update_disclosure()
        self.update_readiness()
        if getattr(self,'preferences_window',None):self.preferences_window.controller.refresh_assistant()
        if getattr(self, 'welcome_card', None):self.render_history()

    def update_readiness(self,task_finished=False):
        if not hasattr(self,'run_button'):return
        ready=bool(self.store.configs()[self.selected_id()].model)
        running=not task_finished and bool(self.worker and self.worker.is_alive())
        self.run_button.set_sensitive(ready or running)
        if not ready and not running:
            self.status.set_text(SETUP_NEEDED)
            self.run_button.set_tooltip_text('Choose an assistant first: Settings → IT setup.')
        else:
            self.run_button.set_tooltip_text('Send · Ctrl+Enter')
            if ready and self.status.get_text()==SETUP_NEEDED:self.status.set_text('Ready when you are')

    def update_disclosure(self):
        config = self.store.configs()[self.selected_id()]
        mode=self.selected_mode()
        text={'plan':'Discuss only · Buddy reads and explains but changes nothing.',
              'execute':'Buddy works in its work folder on its own and asks you before anything else: commands, typing, clicking, connected apps, sending data.',
              'yolo':'Just do it · Buddy acts without asking. Press Stop at any time.'}[mode]
        if self.local.get_active():
            text+=' Offline · connected apps and computer control are off.' if config.local else ' Your selected assistant needs internet. Open Settings to allow it.'
        elif not config.local and mode!='yolo':text+=' Sharing with '+PROVIDERS[config.id][0]+' needs your approval.'
        self.disclosure.set_text(text)

    def focus_history(self):
        self.sidebar.set_visible(True)
        self.history_search.grab_focus()

    def save_draft(self):
        if not self.cid:return
        drafts = self.store.get('drafts', {})
        draft = content(self.input)
        if draft:drafts[self.cid] = draft
        else:drafts.pop(self.cid, None)
        self.store.set('drafts', drafts)

    def restore_draft(self):
        self.input.get_buffer().set_text(self.store.get('drafts', {}).get(self.cid, ''))


    def refresh_thinking_selector(self, config=None):
        """The model button shows model + thinking level and owns the menu to change both."""
        if getattr(self,'model_menu',None):self.model_menu.refresh()

    def update_model_hint(self):
        if not hasattr(self,'model_hint'):return
        active=getattr(self,'active_model',None)
        if active:
            selected=self.store.configs()[self.selected_id()]
            different=(active.id,active.model)!=(selected.id,selected.model)
            text=f'Right now Buddy is using {active.model}. Your new choice is used from your next message.' if different else 'Buddy keeps using this for the current task. Changes apply to your next message.'
        else:text=''
        self.model_hint.set_text(text)

    def select_model(self,config):
        self.store.save_config(config)
        self.selector.set_selected(self.provider_ids.index(config.id))
        self.provider_changed()

    def mode_changed(self,*_):
        # Never kill a running task from a dropdown. The new mode is used from the
        # next message; the running task keeps the mode it started with.
        self.save_preferences()
        self.update_disclosure()
        if self.selected_mode()=='yolo' and not getattr(self,'granting_yolo',False) and not self.store.get('yolo_acknowledged',False):
            self.confirm_yolo()
        if not getattr(self, 'granting_yolo', False) and self.worker and self.worker.is_alive():
            self.status.set_text('That applies to your next message. The running task keeps going; press Stop if you want it to end.')

    def confirm_yolo(self):
        """One plain explanation the first time somebody turns on Just do it.
        Declining switches back to Ask before acting."""
        dialog,box=self.conversation_dialog('Let Buddy act without asking?',
            'With Just do it, Buddy will run commands, type, click, open apps, use your connected apps and send information to your online assistant without asking first.\n\n'
            'You can still press Stop at any time, and Buddy never reads your saved passwords. Switch back to Ask before acting whenever you like.')
        row=Gtk.Box(spacing=8,halign=Gtk.Align.END)
        decided=[False]
        def choose(yes):
            if decided[0]:return
            decided[0]=True
            if yes:self.store.set('yolo_acknowledged',True)
            else:
                self.granting_yolo=True
                try:self.mode.set_selected(1)
                finally:self.granting_yolo=False
                self.save_preferences();self.update_disclosure()
            dialog.destroy()
        row.append(button('Keep asking me',lambda *_:choose(False)))
        row.append(button('Turn on Just do it',lambda *_:choose(True),'suggested-action'))
        box.append(row);dialog.connect('close-request',lambda *_:choose(False) or False)
        dialog.present();self.yolo_dialog=dialog
        return dialog

    def selected_mode(self):
        return ('plan', 'execute', 'yolo')[self.mode.get_selected()]

    def enable_yolo_for_task(self, token):
        token.check()
        if not self.agent or self.agent.token is not token:
            raise ValueError('This task is no longer active.')
        self.agent.registry.policy.mode = 'yolo'
        self.store.audit(self.agent.task_id, 'approval_mode', {'mode': 'yolo', 'source': 'user_button'})
        self.store.set('yolo_acknowledged',True)
        self.granting_yolo = True
        try:self.mode.set_selected(2)
        finally:self.granting_yolo = False
        self.status.set_text('Just do it is on · Buddy continues without asking. Press Stop to end the task.')

    def local_changed(self,*_):
        if self.worker and self.worker.is_alive():self.emergency('turning Keep Buddy offline on or off')
        if self.local.get_active():
            self.browser.close();self.mcp.close()
            for token in list(self.aux_tokens):token.stop()
        self.save_preferences()
        self.update_disclosure()

    def save_preferences(self):
        self.store.set('mode',self.selected_mode())
        self.store.set('local_only',self.local.get_active())
        if hasattr(self,'workspace'):self.store.set('workspace',self.workspace.get_text())

    def choose_workspace(self, popover=None):
        chooser=Gtk.FileDialog(title='Choose a working folder',modal=True)
        current=Path(self.workspace.get_text()).expanduser()
        if current.is_dir():chooser.set_initial_folder(Gio.File.new_for_path(str(current)))
        def selected(dialog,result):
            try:
                folder=dialog.select_folder_finish(result)
                path=folder.get_path()
                if path:
                    self.workspace.set_text(path);self.save_preferences()
                    if popover:popover.popdown()
            except GLib.Error as error:
                if not error.matches(Gtk.DialogError.quark(),Gtk.DialogError.DISMISSED):self.notice(str(error))
        chooser.select_folder(self.window,None,selected)

    def open_work_folder(self):
        path=Path(self.workspace.get_text()).expanduser()
        if not path.is_dir():
            self.notice('Buddy’s work folder is missing. Open Settings to choose another folder.');return
        try:Gio.AppInfo.launch_default_for_uri(path.resolve().as_uri(),None)
        except GLib.Error:self.notice('Buddy could not open the work folder. You can choose a different folder in Settings.')

    def choose_image(self):
        chooser=Gtk.FileDialog(title='Attach a file',modal=True)
        image_filter=Gtk.FileFilter();image_filter.set_name('Documents, spreadsheets and images');image_filter.add_mime_type('image/*')
        from .attachments import SUPPORTED
        for suffix in SUPPORTED:
            image_filter.add_pattern('*'+suffix);image_filter.add_pattern('*'+suffix.upper())
        filters=Gio.ListStore.new(Gtk.FileFilter);filters.append(image_filter)
        chooser.set_filters(filters);chooser.set_default_filter(image_filter)
        def selected(dialog,result):
            try:
                file=dialog.open_finish(result)
                path=file.get_path()
                if path:self.attach_file_path(path)
            except GLib.Error as error:
                if not error.matches(Gtk.DialogError.quark(),Gtk.DialogError.DISMISSED):
                    self.store.audit('','file_picker_error',{'error':str(error)})
                    self.notice('Buddy could not open the selected file. Try choosing it again, or ask IT to check file access.')
            except ValueError as error:self.notice(str(error))
            except Exception as error:
                self.store.audit('','attachment_error',{'error':str(error)})
                self.notice('Buddy could not attach this file. Check that you can open it and that it has not moved. For images, try saving a PNG or JPEG copy.')
        chooser.open(self.window,None,selected)

    def attach_file_path(self,path):
        from .attachments import SUPPORTED,ingest
        suffix=Path(path).suffix.lower()
        if suffix not in SUPPORTED:
            if suffix not in Image.registered_extensions():
                raise ValueError('Choose an Excel (.xlsx), Word (.docx), PDF, CSV, text file or supported image such as PNG or JPEG. For an older Office file, save a copy in the newer format first.')
            return self.attach_image_path(path)
        if len(self.pending_images)+len(self.pending_files)>=8:raise ValueError('Attach up to eight files to one message.')
        item=ingest(self.store.root,path);self.pending_files.append(item);self.refresh_attachments()
        return item

    def attach_image_path(self,path):
        if len(self.pending_images)+len(self.pending_files)>=8:raise ValueError('Attach up to eight files to one message.')
        source=Path(path)
        fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            with os.fdopen(fd,'rb') as handle:
                info=os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size>40*1024*1024:
                    raise ValueError('Image must be a regular file smaller than 40 MiB.')
                with Image.open(handle) as image:
                    if image.width*image.height>40000000:raise ValueError('Image exceeds 40 million pixels.')
                    image=image.convert('RGB');image.thumbnail((1920,1920))
                    folder=self.store.root/'images';folder.mkdir(parents=True,exist_ok=True)
                    output=folder/(uuid.uuid4().hex+'.png')
                    image.save(output);os.chmod(output,0o600)
        except Exception:
            try:output.unlink()
            except (NameError,FileNotFoundError):pass
            raise
        self.pending_images.append(str(output));self.refresh_attachments()
        return output

    def refresh_attachments(self):
        while self.attachments.get_first_child():self.attachments.remove(self.attachments.get_first_child())
        for path in self.pending_images:
            item=Gtk.Box(spacing=6);item.add_css_class('attachment')
            picture=Gtk.Picture.new_for_filename(path);picture.set_content_fit(Gtk.ContentFit.COVER);picture.set_size_request(56,56)
            picture.update_property([Gtk.AccessibleProperty.LABEL],['Attached image']);item.append(picture)
            remove=Gtk.Button.new_from_icon_name('window-close-symbolic');remove.add_css_class('flat');remove.set_tooltip_text('Remove attached image')
            remove.update_property([Gtk.AccessibleProperty.LABEL],['Remove attached image'])
            def discard(_button,image=path):
                self.pending_images.remove(image)
                try:Path(image).unlink()
                except FileNotFoundError:pass
                self.refresh_attachments()
            remove.connect('clicked',discard);item.append(remove);self.attachments.append(item)
        for file in self.pending_files:
            item=Gtk.Box(spacing=6);item.add_css_class('attachment')
            item.append(Gtk.Image.new_from_icon_name('text-x-generic-symbolic'))
            from gi.repository import Pango
            name=Gtk.Label(label=file['name'],xalign=0,max_width_chars=28,hexpand=True,ellipsize=Pango.EllipsizeMode.END);name.set_tooltip_text(file['name']);item.append(name)
            remove=Gtk.Button.new_from_icon_name('window-close-symbolic');remove.add_css_class('flat');remove.set_tooltip_text('Remove '+file['name'])
            remove.update_property([Gtk.AccessibleProperty.LABEL],['Remove '+file['name']])
            def discard_file(_button,file=file):
                self.pending_files.remove(file)
                path=Path(file['path']);path.unlink(missing_ok=True);path.parent.rmdir();self.refresh_attachments()
            remove.connect('clicked',discard_file);item.append(remove);self.attachments.append(item)
        self.attachments.set_visible(bool(self.pending_images or self.pending_files))

    def clear_attachments(self,delete=False):
        paths=list(self.pending_images);self.pending_images.clear()
        files=list(self.pending_files);self.pending_files.clear()
        if delete:
            for path in paths:
                try:Path(path).unlink()
                except FileNotFoundError:pass
            for file in files:
                path=Path(file['path']);path.unlink(missing_ok=True)
                try:path.parent.rmdir()
                except OSError:pass
        self.refresh_attachments()

    def new_conversation(self):
        if self.worker and self.worker.is_alive():self.notice('Cancel or finish the current task before changing conversations.');return
        self.save_draft()
        self.clear_attachments(True)
        self.history_search.set_text('')
        self.cid=self.store.conversation();self.refresh_history();self.render_history();self.restore_draft()

    def conversation_dialog(self, title, description):
        dialog = Gtk.Window(title=title, application=self, transient_for=self.window,
                            modal=True, default_width=460)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(box, 'set_margin_' + side)(24)
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class('brand')
        box.append(heading)
        box.append(Gtk.Label(label=description, xalign=0, wrap=True))
        dialog.set_child(box)
        return dialog, box

    def rename_conversation_dialog(self):
        cid = self.cid
        title = next((r['title'] for r in self.store.conversations() if r['id'] == cid), '')
        dialog, box = self.conversation_dialog('Rename conversation', 'Choose a name you can find again.')
        entry = Gtk.Entry(text=title)
        entry.set_max_length(100)
        box.append(entry)
        error = Gtk.Label(xalign=0, wrap=True)
        box.append(error)
        row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        def save(*_):
            try:
                self.store.rename_conversation(cid, entry.get_text())
                self.refresh_history()
                if self.cid == cid:self.conversation_title.set_text(entry.get_text().strip())
                dialog.destroy()
            except ValueError as exc:error.set_text(str(exc))
        row.append(button('Cancel', lambda *_: dialog.destroy()))
        row.append(button('Rename', save, 'suggested-action'))
        entry.connect('activate', save)
        box.append(row)
        dialog.present()
        return dialog

    def delete_conversation_dialog(self):
        if self.worker and self.worker.is_alive():
            self.notice('Stop the current task before deleting a conversation.')
            return
        cid = self.cid
        title = next((r['title'] for r in self.store.conversations() if r['id'] == cid), 'Conversation')
        dialog, box = self.conversation_dialog('Delete conversation?',
            f'“{title}” and its messages, saved progress, and activity records will be permanently removed.\n\n'
            'Private screenshots and attachments used only here will also be deleted. Files in your work folder, saved preferences, and IT settings will remain. This cannot be undone.')
        row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        def remove(*_):
            try:
                self.store.delete_conversation(cid)
                if self.cid == cid:
                    self.clear_attachments(True);self.cid = None
                self.refresh_history()
                self.render_history();self.restore_draft()
                dialog.destroy()
            except ValueError as exc:self.notice(str(exc))
        row.append(button('Keep conversation', lambda *_: dialog.destroy()))
        row.append(button('Delete conversation', remove, 'destructive-action'))
        box.append(row)
        dialog.present()
        return dialog

    def model_dialog(self):
        settings=self.settings_dialog()
        settings.controller.show_it()
        self.model_menu.refresh()
        return settings

    def refresh_history(self):
        while self.history.get_first_child():self.history.remove(self.history.get_first_child())
        rows=self.store.conversations()
        if not rows:
            self.cid=self.store.conversation();rows=self.store.conversations()
        if self.cid is None:self.cid=rows[0]['id']
        query = self.history_search.get_text().strip().casefold()
        for item in rows:
            if query and query not in item['title'].casefold():continue
            row=Gtk.ListBoxRow();row.cid=item['id'];label=Gtk.Label(label=' '.join(item['title'].split()),xalign=0,ellipsize=3,single_line_mode=True);label.set_max_width_chars(24);row.set_tooltip_text(item['title']);row.set_child(label);self.history.append(row)
            if row.cid==self.cid:self.history.select_row(row)

    def select_history(self,box,row):
        if not row:return
        if self.worker and self.worker.is_alive():return
        if row.cid!=self.cid:
            self.save_draft();self.clear_attachments(True)
            self.cid=row.cid;self.restore_draft()
        self.render_history()

    def render_history(self):
        self.activity_group=None
        while self.transcript.get_first_child():self.transcript.remove(self.transcript.get_first_child())
        self.preview_box.set_visible(False)
        self.stream_scroll.set_visible(False)
        self.conversation_title.set_text(' '.join(next((r['title'] for r in self.store.conversations() if r['id']==self.cid),'Conversation').split()))
        messages=self.store.messages(self.cid)
        self.welcome_card = welcome(self) if not messages else None
        if self.welcome_card:self.transcript.append(self.welcome_card)
        for m in messages:
            if m.get('role')=='system':continue
            if m.get('content'):self.append('RESULT' if m['role']=='tool' else m['role'].upper(),m['content'])
            if m.get('tool_calls'):self.append('TOOLS',json.dumps(m['tool_calls'],ensure_ascii=False,indent=2))
            for image in m.get('images',[]):self.append_image(image)
            for file in m.get('files',[]):self.append_file(file)
        self.transcript.append(self.stream_scroll)
        # Land at the newest message once the cards have been laid out.
        GLib.timeout_add(80,self.scroll_end)
        tasks=self.store.tasks(self.cid)
        self.task_usage={}
        if tasks:
            for record in self.store.audits(tasks[0]['id']):
                if record['event']=='usage':self.add_usage(json.loads(record['body']))
        else:self.usage.set_text('')
        needs_review = bool(tasks and tasks[0]['status']=='needs_review')
        recoverable = bool(tasks and tasks[0]['status'] in ('interrupted','cancelled','failed','needs_review'))
        self.recovery_button.set_visible(recoverable)
        self.status.set_text('Some steps still need checking. Read the message above before using the result.' if needs_review else
                            'This task stopped before finishing. You can continue when ready.' if recoverable else 'Ready when you are')
        self.update_readiness()
        if tasks and tasks[0]['status']=='failed':
            errors=[json.loads(r['body']).get('error','') for r in self.store.audits(tasks[0]['id']) if r['event']=='task_error']
            if errors:self.append('ERROR',errors[-1])


    def append(self,who,text):
        if getattr(self,'welcome_card',None):
            self.transcript.remove(self.welcome_card);self.welcome_card=None
        text=self.redactor.text(text if isinstance(text,str) else json.dumps(text,ensure_ascii=False))
        card=message_card(who,text,self.copy_text) if who in ('YOU','USER','ASSISTANT','BUDDY') else activity_card(who,text)
        if who=='ERROR':
            from .experience import friendly_error
            guidance=friendly_error(text)[1]
            if 'Settings' in guidance or 'Connected apps' in guidance:
                actions=Gtk.Box(spacing=8)
                if 'Settings' in guidance:actions.append(button('Settings',lambda *_:self.settings_dialog()))
                if 'Connected apps' in guidance:actions.append(button('Connected apps',lambda *_:self.mcp_dialog()))
                card.append(actions)
        adjustment=self.transcript_scroll.get_vadjustment()
        follow=adjustment.get_upper()-adjustment.get_page_size()-adjustment.get_value()<100
        if who in ('TOOL','TOOLS','RESULT'):
            group=getattr(self,'activity_group',None)
            if group is None or group.get_parent() is not self.transcript:
                group=Gtk.Expander(label='Work details')
                group.steps=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
                group.count=0;group.set_child(group.steps);self.activity_group=group
                if self.stream_scroll.get_parent():self.transcript.insert_child_after(group,self.stream_scroll.get_prev_sibling())
                else:self.transcript.append(group)
            group.steps.append(card);group.count+=1;group.set_label(f'Work details · {group.count} updates')
            if follow:GLib.idle_add(self.scroll_end)
            return
        self.activity_group=None
        if self.stream_scroll.get_parent():
            self.transcript.insert_child_after(card,self.stream_scroll.get_prev_sibling())
        else:self.transcript.append(card)
        if follow or who in ('YOU','USER'):GLib.idle_add(self.scroll_end)

    def append_image(self,path):
        if getattr(self,'welcome_card',None):
            self.transcript.remove(self.welcome_card);self.welcome_card=None
        card=image_card(path,self.show_image)
        if self.stream_scroll.get_parent():
            self.transcript.insert_child_after(card,self.stream_scroll.get_prev_sibling())
        else:self.transcript.append(card)

    def append_file(self,file):
        from .ui_widgets import label
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5);box.add_css_class('tool-card')
        box.append(label(file['name'],wrap=True,selectable=True))
        box.append(label('Attached copy · your original stays unchanged','muted',wrap=True))
        if self.stream_scroll.get_parent():self.transcript.insert_child_after(box,self.stream_scroll.get_prev_sibling())
        else:self.transcript.append(box)

    def copy_text(self,text,what='This message'):
        """Put text on the clipboard and say so in plain words."""
        Gdk.Display.get_default().get_clipboard().set(text)
        self.status.remove_css_class('attention');self.status.set_text(f'{what} copied. Paste it anywhere with Ctrl+V.')
        return text

    def copy_conversation(self,role=None):
        """Copy every message, or only yours / only Buddy's, as plain text."""
        parts=[]
        for m in self.store.messages(self.cid):
            if m.get('role') not in ('user','assistant') or not m.get('content'):continue
            if role and m['role']!=role:continue
            parts.append(('You: ' if m['role']=='user' else 'Desktop Buddy: ')+self.redactor.text(m['content']))
        if not parts:
            self.status.set_text('Nothing to copy yet.' if role is None else 'No messages from you yet.' if role=='user' else 'No replies from Buddy yet.');return ''
        what='Whole conversation' if role is None else 'Your messages' if role=='user' else 'Buddy’s replies'
        return self.copy_text('\n\n'.join(parts),what)

    def scroll_end(self):
        a=self.transcript_scroll.get_vadjustment();a.set_value(max(0,a.get_upper()-a.get_page_size()))
        # Setting a value the adjustment already holds emits nothing, so the viewport
        # would keep a stale offset. Always ask it to lay out again at the current value.
        viewport=self.transcript_scroll.get_child()
        if viewport is not None:viewport.queue_allocate()
        return False

    def emit(self,name,data):GLib.idle_add(self.on_event,name,data)

    def on_event(self,name,data):
        if name=='status':self.status.remove_css_class('attention');self.status.set_text(self.redactor.text(str(data)))
        elif name=='attention':self.alert(str(data))
        elif name=='stream':
            self.stream_scroll.set_visible(True)
            # Show the entire accumulated, redacted prefix, never individual raw chunks.
            self.stream_view.get_buffer().set_text(self.redactor.stream_text(str(data))[-8000:])
        elif name=='message':self.last_reply=data['content'];self.append('BUDDY',data['content']);self.stream_view.get_buffer().set_text('');self.stream_scroll.set_visible(False)
        elif name in ('tool','result','error'):
            self.append(name.upper(),data if isinstance(data,str) else json.dumps(data,indent=2,ensure_ascii=False))
            if name=='error':
                from .experience import friendly_error
                self.alert(friendly_error(data)[0])
        elif name=='usage':self.add_usage(data)
        elif name=='screenshot':
            self.preview.set_filename(str(data));self.preview_box.set_visible(True)
            # Never present a new window here: that would steal focus from the app
            # being controlled and invalidate the next desktop input action.
        elif name=='done':
            self.active_model=None;self.update_model_hint()
            self.stream_scroll.set_visible(False)
            self.cancel_button.set_visible(False)
            self.spinner.stop()
            self.run_button.set_sensitive(True);self.history.set_sensitive(True);self.workspace.set_sensitive(True)
            self.recovery_button.set_visible(data['status'] in ('failed','cancelled','interrupted','needs_review'))
            waiting=data['status']=='complete' and needs_user_reply(getattr(self,'last_reply',''))
            if waiting:self.alert('Buddy needs something from you · read the message above and reply')
            elif data['status']=='failed':self.alert('Something went wrong. See the message above.')
            elif data['status']=='needs_review':self.alert('Some steps still need checking. Read the message above before using the result.')
            else:
                self.status.remove_css_class('attention')
                source=getattr(self,'stop_source',None)
                stopped=('Stopped by '+source if source else 'Stopped')+' · your progress is saved'
                self.status.set_text({'complete':'Finished · ready for your next message','cancelled':stopped}.get(data['status'],'Ready when you are'))
                if data['status']=='complete':self.notify('Finished','Buddy finished the task.')
            if data.get('unread') or getattr(self,'saved_followup',False):
                self.status.set_text('Your follow-up is saved · send another message to continue with it')
            self.update_readiness(task_finished=True)
        return False

    def alert(self,text):
        """Something needs the person: say it in the status line, in red, and on the desktop."""
        text=self.redactor.text(text)
        self.status.add_css_class('attention');self.status.set_text(text)
        self.notify('Desktop Buddy needs you',text)

    def notify(self,title,body):
        if getattr(self,'window',None) and self.window.is_active():return
        try:
            note=Gio.Notification.new(title);note.set_body(body);self.send_notification('buddy-status',note)
        except Exception:pass

    def add_usage(self, data):
        for key in ('input_tokens', 'output_tokens', 'elapsed_seconds', 'estimated_cost_usd', 'reported_cost_usd'):
            if key in data:self.task_usage[key]=self.task_usage.get(key,0)+(data[key] or 0)
        usage=self.task_usage
        self.usage.set_text(f'Task total · {usage.get("input_tokens",0):,} in / {usage.get("output_tokens",0):,} out'
            +f' · {usage.get("elapsed_seconds",0):.1f}s inference'
            +(f' · ${usage["reported_cost_usd"]:.4f} reported' if 'reported_cost_usd' in usage
              else f' · est. ${usage["estimated_cost_usd"]:.4f}' if 'estimated_cost_usd' in usage else ''))

    def ensure_backends(self):
        from .desktop import LazyBackend
        if not self.desktop:self.desktop=LazyBackend(lambda:Portal(self.window_handle,save_token=lambda value:self.store.set('desktop_restore_token',value),load_token=lambda:self.store.get('desktop_restore_token',''),progress=self.computer_access_progress))
        if not self.a11y:self.a11y=LazyBackend(Accessibility)

    def approve(self,name,detail,token):
        done=threading.Event();decision=[False];holder=[]
        def show():
            if token.event.is_set():done.set();return False
            dialog=Gtk.Window(title='Review action',transient_for=self.window,modal=True,default_width=620,default_height=620,application=self)
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
            for side in ('top','bottom','start','end'):getattr(box,'set_margin_'+side)(24)
            dialog.set_child(box)
            from .experience import action_name,approval_summary
            heading=Gtk.Label(label=action_name(name),xalign=0,wrap=True);heading.add_css_class('brand');box.append(heading)
            hints={'shell.run':'Computer instructions run with your account’s permissions and can change files, apps or settings. The explanation below is provided by Buddy.',
                'Cloud data disclosure':'This task uses an online service. Information Buddy reads for the task may leave this computer.',
                'comfyui.run':'Buddy will use the image service on this computer to create and save images.',
                'desktop.input':'Buddy will use your keyboard or mouse in an app. That app may send information online.',
                'desktop.accessible_action':'Buddy will use a control in an app. That app may send information online.',
                'desktop.set_text':'Buddy will replace the contents of this field. That app may save or send the text online.',
                'app.launch':'The application opens locally and may use its own network connection.'}
            hint=Gtk.Label(label=hints.get(name,'Desktop Buddy needs permission to perform this sensitive action.'),xalign=0,wrap=True);hint.add_css_class('muted');box.append(hint)
            context=getattr(self.agent,'review_context',None) if self.agent and self.agent.token is token else None
            try:matching=context and context['name']==name and context['arguments']==json.loads(detail)
            except (ValueError,KeyError):matching=False
            policy=self.agent.registry.policy if matching else None
            reviewable=name!='shell.run' or bool(matching and context['purpose'])
            summary=approval_summary(name,detail,{c.id:c.name for c in self.mcp.configs().values()},
                purpose=context['purpose'] if matching else '',workspace=str(policy.workspace) if policy else '',local_only=policy.local_only if policy else False)
            if name!='shell.run' and matching and context['purpose']:
                summary='Buddy proposes:\n'+context['purpose']+'\n\n'+summary
            readable=Gtk.Label(label=self.redactor.text(summary),xalign=0,wrap=True,selectable=True,valign=Gtk.Align.START)
            readable.set_wrap_mode(__import__('gi.repository',fromlist=['Pango']).Pango.WrapMode.WORD_CHAR)
            box.append(scroll(readable,200))
            expander=Gtk.Expander(label='Technical details')
            view=text_view();view.set_monospace(True);view.get_buffer().set_text(self.redactor.text(detail));expander.set_child(scroll(view,160));box.append(expander)
            note=Gtk.Label(label='Allow once approves only this step. Skip action leaves it out.',xalign=0,wrap=True);note.add_css_class('muted');box.append(note)
            row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=10,halign=Gtk.Align.END);box.append(row)
            def finish(yes):
                decision[0]=yes and not token.event.is_set();dialog.destroy()
                # Resume only after GTK has processed the dialog's destruction;
                # physical tools then restore their pinned target before input.
                GLib.idle_add(lambda:done.set() or False)
            if self.agent and self.agent.token is token:
                def use_yolo(*_):
                    if token.event.is_set():finish(False);return
                    self.enable_yolo_for_task(token)
                    finish(True)
                yolo = button('Allow and stop asking', use_yolo)
                yolo.set_sensitive(reviewable)
                yolo.set_tooltip_text('Approve this step and switch to Just do it: Buddy continues without asking. Stop still works.')
                box.insert_child_after(yolo, note)
            skip=button('Skip action',lambda *_:finish(False));row.append(skip)
            allow=button('Allow once',lambda *_:finish(True),'suggested-action');allow.set_sensitive(reviewable);row.append(allow)
            stop=button('Stop everything',lambda *_:self.emergency(),'destructive-action');stop.set_tooltip_text('Cancel all queued actions and revoke desktop control');row.append(stop)
            dialog.connect('close-request',lambda *_:finish(False) or False)
            holder.append(dialog);self.pending.append((done,dialog));dialog.present();skip.grab_focus();readable.select_region(0,0);return False
        GLib.idle_add(show)
        try:
            while not done.wait(.05):token.check()
            token.check();return decision[0]
        finally:
            if holder:GLib.idle_add(holder[0].destroy)
            self.pending[:]=[(e,d) for e,d in self.pending if e is not done]

    def send(self,prompt=None,*,resume=False):
        prompt=prompt or content(self.input).strip()
        if not prompt:
            if self.pending_images or self.pending_files:self.notice('Tell Buddy what you would like to do with the attached file, then choose Send.')
            return
        if self.worker and self.worker.is_alive():return self.send_during_task(prompt)
        self.stop_source=None;self.saved_followup=False
        if self.emergency_latched:
            self.emergency_latched=False;self.status.set_text('Starting a new request. Computer access may need to be allowed again.')
        self.save_preferences();config=self.store.configs()[self.selected_id()]
        if not config.model:self.settings_dialog();return
        if self.local.get_active() and not config.local:self.notice('Buddy is offline. Open Settings and turn off Keep Buddy offline to use your online assistant.');return
        if self.pending_images and not config.use_vision:self.notice('This assistant cannot read images. Choose an assistant that can read images, or remove the image before sending.');return
        workspace=Path(self.workspace.get_text()).expanduser().resolve()
        if not workspace.is_dir():self.notice('Buddy’s work folder is missing. Open Settings and choose a different folder under “Where Buddy saves your work”.');return
        images=list(self.pending_images);files=list(self.pending_files)
        if not self.store.messages(self.cid):
            with self.store.lock,self.store.db:self.store.db.execute('UPDATE conversations SET title=? WHERE id=?',(self.redactor.text(' '.join(prompt.split()))[:80],self.cid))
            self.refresh_history()
        # Persist before credentials/backends initialize so a startup failure cannot
        # erase the user's message or leave an unreferenced attachment behind.
        message={'role':'user','content':prompt}
        if images:message['images']=images
        if files:message['files']=files
        self.store.add_message(self.cid,message)
        self.input.get_buffer().set_text('');self.save_draft();self.clear_attachments(False)
        self.conversation_title.set_text(next(r['title'] for r in self.store.conversations() if r['id']==self.cid))
        self.append('YOU',prompt)
        for image in images:self.append_image(image)
        for file in files:self.append_file(file)
        self.task_usage={};self.usage.set_text('')
        self.cancel_button.set_visible(True)
        self.recovery_button.set_visible(False);self.spinner.start();self.status.set_text('Getting started…')
        # Send stays enabled: messages typed during a task are delivered at its next step.
        self.history.set_sensitive(False);self.workspace.set_sensitive(False);self.start_token=StopToken();self.agent=None
        mode=self.selected_mode();local=self.local.get_active();cid=self.cid
        self.active_model=config;self.update_model_hint()
        def work():
            try:
                self.ensure_backends()
                key=self.credentials.get(config.id) if not config.local else self.credentials.get(config.id)
                policy=Policy(workspace,mode,local,self.approve,protected=[self.store.root,Path(__file__).parent])
                registry=Registry(policy,self.store,self.desktop,self.a11y,self.clipboard,self.browser,self.mcp)
                self.start_token.check()
                self.agent=Agent(self.store,registry,Provider(config,key,self.redactor),self.emit)
                self.agent.token=self.start_token
                if self.emergency_latched:self.agent.stop()
                self.agent.run(cid,prompt,resume=resume,images=images,user_saved=True,files=files)
            except Cancelled:self.emit('done',{'status':'cancelled'})
            except Exception as e:
                tid=self.store.task(cid,{'request':prompt})
                self.store.audit(tid,'task_error',{'error':str(e)})
                self.store.checkpoint(tid,cid,'failed',{})
                self.emit('error',str(e));self.emit('done',{'status':'failed'})
        self.worker=threading.Thread(target=work,daemon=True,name='agent-task');self.worker.start()

    def send_during_task(self,prompt):
        """Hand a message to the running task without stopping it."""
        agent=self.agent
        if not agent or agent.conversation_id!=self.cid:
            self.notice('Buddy is still starting this task. Try again in a moment, or press Stop.');return
        images=list(self.pending_images);files=list(self.pending_files)
        if images and self.active_model and not self.active_model.use_vision:
            self.notice('The assistant working on this task cannot read images. Remove the image or wait until you can choose another assistant.');return
        try:queued=agent.say(prompt,images,files=files)
        except Exception as error:
            self.notice('Could not save your message: '+str(error));return
        self.input.get_buffer().set_text('');self.save_draft();self.clear_attachments(False)
        self.append('YOU',prompt)
        for image in images:self.append_image(image)
        for file in files:self.append_file(file)
        if queued:
            self.status.set_text('Message received · Buddy reads it before its next step (a running command is not interrupted; press Stop to cancel it).')
        else:
            self.saved_followup=True
            self.status.set_text('This task is finishing · your message is saved for your next request')

    def resume(self):
        tasks=self.store.tasks(self.cid)
        if not tasks:self.notice('No previous task to resume.');return
        previous=tasks[0]
        if previous['status']=='complete':self.notice('The last task completed. Send a new request to continue.');return
        self.send('Resume the previous interrupted task using the conversation and this checkpoint. First inspect current state; never repeat an uncertain effect without asking. Checkpoint: '+previous['checkpoint'],resume=True)

    def cancel(self,source='the Stop button'):
        self.stop_source=source
        if self.worker and self.worker.is_alive():
            self.store.audit(getattr(self.agent,'task_id',None) or 'ui','stopped',{'by':source})
        self.start_token.stop()
        if self.agent:self.agent.stop()
        for token in self.aux_tokens:token.stop()
        for done,dialog in list(self.pending):done.set();dialog.destroy()
        self.status.set_text('Stopping… waiting for the current action to respond')

    def emergency(self,source='the Stop everything button'):
        self.emergency_latched=True;self.cancel(source)
        if self.desktop:threading.Thread(target=self.desktop.close,daemon=True).start()
        self.browser.close()
        self.mcp.close()
        self.status.set_text('Stopped · computer control and app connections are closed')

    def auxiliary(self,func):
        token=StopToken();self.aux_tokens.append(token)
        def run():
            try:func(token)
            except Cancelled:self.emit('status','Operation cancelled.')
            except Exception as e:self.emit('error',str(e))
            finally:self.aux_tokens.remove(token)
        threading.Thread(target=run,daemon=True).start()
        return token

    def computer_access_progress(self,message):
        def update():
            dialog=getattr(self,'computer_access_dialog',None)
            if dialog and dialog.pending and not dialog.closed:
                if not dialog.token or not dialog.token.event.is_set():
                    dialog.heading.set_text('Checking computer access')
                    dialog.message.set_text(message)
                    dialog.status.set_text('You don’t need to do anything.')
            return False
        GLib.idle_add(update)

    def connect_desktop(self):
        current=getattr(self,'computer_access_dialog',None)
        if current and not current.closed:
            current.window.present();return current
        from .desktop_access_ui import ComputerAccessDialog
        dialog=ComputerAccessDialog(self);self.computer_access_dialog=dialog
        self.status.set_text('Waiting for your computer to confirm access…')
        def work(t):
            result=None;error=None;cancelled=False
            try:
                t.check();self.ensure_backends();result=self.desktop.connect(t);t.check()
                self.store.audit('desktop-control','connected',{k:result.get(k) for k in ('remembered','logical_size')})
                self.emit('status','Computer access is ready.' + (' Your computer saved this permission for next time.' if result.get('remembered') else ' Your computer may ask you to allow access again next time.'))
            except Cancelled:
                if self.desktop:self.desktop.close()
                cancelled=True;self.emit('status','Computer connection cancelled.')
            except Exception as e:
                error=str(e);self.store.audit('desktop-control','connect_failed',{'error':error})
                self.emit('status','Buddy couldn’t connect to your computer. Details are saved in Recent activity.')
            finally:GLib.idle_add(dialog.finish,result,error,cancelled)
        dialog.token=self.auxiliary(work)
        return dialog

    def observe(self):
        def work(t):
            self.ensure_backends()
            try:result=self.desktop.screenshot(self.store.root/'screenshots'/'observation.png',t)
            except Exception as e:
                self.store.audit('desktop-control','screenshot_failed',{'error':str(e)});raise
            self.emit('screenshot',result['path']);self.emit('result',self.a11y.inspect(depth=1));self.emit('status','Screenshot saved on this computer. It has not been sent to your assistant.')
        self.auxiliary(work)

    def clipboard(self,operation,text):
        done=threading.Event();out=[]
        def run():
            cb=Gdk.Display.get_default().get_clipboard()
            if operation=='write':cb.set(text);out.append({'set':True});done.set()
            else:
                def read(c,result):
                    try:out.append({'text':c.read_text_finish(result)})
                    except Exception as e:out.append({'error':str(e)})
                    done.set()
                cb.read_text_async(None,read)
            return False
        GLib.idle_add(run)
        if not done.wait(10):raise TimeoutError('Clipboard request timed out.')
        return out[0]

    def notice(self,message):
        self.dialog_text('Desktop Buddy',message)

    def dialog_text(self,title,text,editable=False,on_save=None):
        d=Gtk.Window(title=title,application=self,transient_for=self.window,modal=True,default_width=560,default_height=280 if not editable else 560)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10);d.set_child(box)
        for side in ('top','bottom','start','end'):getattr(box,'set_margin_'+side)(24)
        if editable:
            view=text_view(True);view.get_buffer().set_text(text)
        else:
            view=Gtk.Label(label=text,xalign=0,wrap=True,selectable=True)
            view.add_css_class('message-body')
            view.set_wrap_mode(__import__('gi.repository',fromlist=['Pango']).Pango.WrapMode.WORD_CHAR)
        box.append(scroll(view))
        if on_save:
            def save(*_):
                try:on_save(content(view));d.destroy()
                except Exception as e:self.status.set_text(str(e))
            box.append(button('Save',save,'suggested-action'))
        box.append(button('Close',lambda *_:d.destroy()));d.present();return d

    def memory_dialog(self):
        dialog, box = self.conversation_dialog('Things to remember',
            'Preferences and workflow notes saved on this computer. Changes take effect when you save.')
        dialog.set_default_size(680, 560)
        entries = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(scroll(entries, 240))
        rows = []
        def add_row(name='', value=''):
            from .experience import preference_text
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            group.add_css_class('settings-section')
            top = Gtk.Box(spacing=8)
            key = Gtk.Entry(text=name, placeholder_text='For example: How I like my answers')
            key.set_hexpand(True)
            top.append(key)
            editor = text_view(True)
            shown=preference_text(value);editor.get_buffer().set_text(shown)
            item = (key, editor, value, shown)
            def remove(*_):
                rows.remove(item)
                entries.remove(group)
            top.append(button('Remove', remove))
            group.append(top)
            group.append(scroll(editor, 60))
            entries.append(group)
            rows.append(item)
        for name, value in self.store.memory().items():add_row(name, value)
        error = Gtk.Label(xalign=0, wrap=True)
        box.append(error)
        actions = Gtk.Box(spacing=8)
        actions.append(button('Add a preference', lambda *_: add_row()))
        def save(*_):
            try:
                values = {}
                for key, editor, original, shown in rows:
                    name = key.get_text().strip()
                    if not name:raise ValueError('Give each preference a name so you can find it later.')
                    if name in values:raise ValueError('Two preferences have the same name. Give them different names.')
                    value = content(editor)
                    values[name] = original if value==shown else value
                self.store.save_memory(values)
                dialog.destroy()
            except ValueError as exc:error.set_text(str(exc))
        actions.append(button('Save changes', save, 'suggested-action'))
        actions.append(button('Cancel', lambda *_: dialog.destroy()))
        box.append(actions)
        dialog.present()
        return dialog

    def audit_dialog(self):
        from datetime import datetime
        dialog, box = self.conversation_dialog('Recent activity', 'Review the latest steps Buddy took. Technical details are kept separately under each step.')
        dialog.set_default_size(760, 620)
        events = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for record in self.store.audits():
            card = activity_card('RESULT', record['body'])
            stamp = datetime.fromtimestamp(record['created']).strftime('%b %d · %H:%M:%S')
            caption = Gtk.Label(label=stamp, xalign=0)
            caption.add_css_class('muted')
            card.prepend(caption)
            events.append(card)
        if not events.get_first_child():events.append(Gtk.Label(label='No recorded activity yet.'))
        box.append(scroll(events, 350))
        box.append(button('Close', lambda *_: dialog.destroy()))
        dialog.present()
        return dialog

    def show_image(self,path):
        d=Gtk.Window(title='Desktop observation · local image',application=self,transient_for=self.window,default_width=1000,default_height=700)
        picture=Gtk.Picture.new_for_filename(str(path));picture.set_can_shrink(True);picture.set_content_fit(Gtk.ContentFit.CONTAIN);d.set_child(picture);d.present()

    def mcp_dialog(self):
        from .mcp_ui import ConnectionsDialog
        dialog=ConnectionsDialog(self)
        dialog.window.controller=dialog
        return dialog.window

    def settings_dialog(self):
        from .preferences_ui import PreferencesDialog
        current=getattr(self,'preferences_window',None)
        if current:
            current.controller.refresh_assistant();current.present();return current
        dialog=PreferencesDialog(self);dialog.window.controller=dialog
        self.preferences_window=dialog.window
        return dialog.window

    def providers_dialog(self,preselect=None):
        from .model_ui import ConnectionDialog
        dialog=ConnectionDialog(self,preselect)
        dialog.window.controller=dialog
        return dialog.window

    def close_requested(self,*_):
        if not self.window.is_maximized():
            self.store.set('window_width',self.window.get_width())
            self.store.set('window_height',self.window.get_height())
        self.save_draft();self.clear_attachments(True);self.emergency('closing the app')
        self.quit();return False
