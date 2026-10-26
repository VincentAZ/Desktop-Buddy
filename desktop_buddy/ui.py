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
from .agent import Agent
from .browser import Browser
from .desktop import Portal,Accessibility
from .providers import Provider
from . import __version__
from .security import Policy,Redactor,StopToken,Cancelled
from .state import Store,Credentials,PROVIDERS,thinking_levels
from .tools import Registry
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
        self.agent=None;self.start_token=StopToken();self.worker=None;self.pending=[];self.desktop=None;self.a11y=None;self.aux_tokens=[]
        self.browser=Browser(self.data_dir/'browser-profile');self.cid=None;self.emergency_latched=False;self.window_handle='';self.task_usage={};self.pending_images=[]
        self.connect('activate',self.activate)
        stop=Gio.SimpleAction.new('emergency',None);stop.connect('activate',lambda *_:self.emergency());self.add_action(stop)
        new=Gio.SimpleAction.new('new-conversation',None);new.connect('activate',lambda *_:self.new_conversation());self.add_action(new)
        search=Gio.SimpleAction.new('search-conversations',None);search.connect('activate',lambda *_:self.focus_history());self.add_action(search)
        settings=Gio.SimpleAction.new('provider-settings',None);settings.connect('activate',lambda *_:self.providers_dialog());self.add_action(settings)
        self.set_accels_for_action('app.emergency',['<Primary><Shift>Escape'])
        self.set_accels_for_action('app.new-conversation',['<Primary>n'])
        self.set_accels_for_action('app.search-conversations',['<Primary>k'])
        self.set_accels_for_action('app.provider-settings',['<Primary>comma'])

    def activate(self,*_):
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
        if getattr(self, 'welcome_card', None):self.render_history()

    def update_disclosure(self):
        config = self.store.configs()[self.selected_id()]
        if self.selected_mode() == 'yolo':
            text = 'YOLO is on · commands, files and apps without asking. ' + ('Offline mode still applies.' if self.local.get_active() else 'Online requests are allowed.')
        elif self.local.get_active():
            text = 'Offline mode is on. Other apps may still use the internet.' if config.local else 'Offline mode is on. Open Task options to allow this cloud connection.'
        else:
            text = 'Using a model on this computer. Online actions need your permission.' if config.local else 'Your conversation goes to '+PROVIDERS[config.id][0]+' after you allow it.'
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
        if not getattr(self, 'granting_yolo', False) and self.worker and self.worker.is_alive():self.cancel()
        self.save_preferences()
        self.update_disclosure()

    def selected_mode(self):
        return ('plan', 'execute', 'yolo')[self.mode.get_selected()]

    def enable_yolo_for_task(self, token):
        token.check()
        if not self.agent or self.agent.token is not token:
            raise ValueError('This task is no longer active.')
        self.agent.registry.policy.mode = 'yolo'
        self.store.audit(self.agent.task_id, 'approval_mode', {'mode': 'yolo', 'source': 'user_button'})
        self.granting_yolo = True
        try:self.mode.set_selected(2)
        finally:self.granting_yolo = False
        self.status.set_text('YOLO is on · continuing without approval prompts')

    def local_changed(self,*_):
        if self.worker and self.worker.is_alive():self.emergency()
        if self.local.get_active():self.browser.close()
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

    def choose_image(self):
        chooser=Gtk.FileDialog(title='Attach an image',modal=True)
        image_filter=Gtk.FileFilter();image_filter.set_name('Images');image_filter.add_mime_type('image/*')
        filters=Gio.ListStore.new(Gtk.FileFilter);filters.append(image_filter)
        chooser.set_filters(filters);chooser.set_default_filter(image_filter)
        def selected(dialog,result):
            try:
                file=dialog.open_finish(result)
                path=file.get_path()
                if path:self.attach_image_path(path)
            except GLib.Error as error:
                if not error.matches(Gtk.DialogError.quark(),Gtk.DialogError.DISMISSED):self.notice(str(error))
            except Exception as error:self.notice(str(error))
        chooser.open(self.window,None,selected)

    def attach_image_path(self,path):
        if len(self.pending_images)>=8:raise ValueError('Attach at most eight images to one request.')
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
        self.attachments.set_visible(bool(self.pending_images))

    def clear_attachments(self,delete=False):
        paths=list(self.pending_images);self.pending_images.clear()
        if delete:
            for path in paths:
                try:Path(path).unlink()
                except FileNotFoundError:pass
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
            f'“{title}” and its messages, checkpoints, and activity records will be permanently removed.\n\n'
            'Private screenshots and attachments used only here will also be deleted. Workspace files, memory, and provider settings will remain. This cannot be undone.')
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
        if not any(c.model for c in self.store.configs().values()):return self.providers_dialog()
        self.model_menu.popover.popup()
        return self.model_menu.popover

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
        self.transcript.append(self.stream_scroll)
        tasks=self.store.tasks(self.cid)
        self.task_usage={}
        if tasks:
            for record in self.store.audits(tasks[0]['id']):
                if record['event']=='usage':self.add_usage(json.loads(record['body']))
        else:self.usage.set_text('')
        recoverable = bool(tasks and tasks[0]['status'] in ('interrupted','cancelled','failed'))
        self.recovery_button.set_visible(recoverable)
        self.status.set_text('This task stopped before finishing. You can continue when ready.' if recoverable else 'Ready when you are')
        if tasks and tasks[0]['status']=='failed':
            errors=[json.loads(r['body']).get('error','') for r in self.store.audits(tasks[0]['id']) if r['event']=='task_error']
            if errors:self.append('ERROR',errors[-1])


    def append(self,who,text):
        if getattr(self,'welcome_card',None):
            self.transcript.remove(self.welcome_card);self.welcome_card=None
        text=self.redactor.text(text if isinstance(text,str) else json.dumps(text,ensure_ascii=False))
        card=message_card(who,text) if who in ('YOU','USER','ASSISTANT','BUDDY') else activity_card(who,text)
        adjustment=self.transcript_scroll.get_vadjustment()
        follow=adjustment.get_upper()-adjustment.get_page_size()-adjustment.get_value()<100
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

    def scroll_end(self):
        a=self.transcript_scroll.get_vadjustment();a.set_value(max(0,a.get_upper()-a.get_page_size()));return False

    def emit(self,name,data):GLib.idle_add(self.on_event,name,data)

    def on_event(self,name,data):
        if name=='status':self.status.set_text(self.redactor.text(str(data)))
        elif name=='stream':
            self.stream_scroll.set_visible(True)
            # Show the entire accumulated, redacted prefix, never individual raw chunks.
            self.stream_view.get_buffer().set_text(self.redactor.stream_text(str(data))[-8000:])
        elif name=='message':self.append('BUDDY',data['content']);self.stream_view.get_buffer().set_text('');self.stream_scroll.set_visible(False)
        elif name in ('tool','result','error'):self.append(name.upper(),data if isinstance(data,str) else json.dumps(data,indent=2,ensure_ascii=False))
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
            self.recovery_button.set_visible(data['status'] in ('failed','cancelled','interrupted'))
            self.status.set_text({'complete':'Finished · ready for your next message', 'failed':'Something needs attention. See the message above.', 'cancelled':'Stopped · your progress is saved'}.get(data['status'],'Ready when you are'))

        return False

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
        if not self.desktop:self.desktop=LazyBackend(lambda:Portal(self.window_handle))
        if not self.a11y:self.a11y=LazyBackend(Accessibility)

    def approve(self,name,detail,token):
        done=threading.Event();decision=[False];holder=[]
        def show():
            if token.event.is_set():done.set();return False
            dialog=Gtk.Window(title='Review action',transient_for=self.window,modal=True,default_width=620,application=self)
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
            for side in ('top','bottom','start','end'):getattr(box,'set_margin_'+side)(24)
            dialog.set_child(box)
            names={'shell.run':'Run a command','desktop.input':'Control the desktop','desktop.accessible_action':'Use an application control',
                'desktop.window':'Control a window','browser.navigate':'Open a web page','browser.action':'Interact with a web page',
                'app.launch':'Open an application','http.request':'Send an HTTP request','memory.set':'Save a memory',
                'comfyui.run':'Create images','comfyui.run_batch':'Create a batch of images','comfyui.cancel':'Cancel a ComfyUI job','Cloud data disclosure':'Send this task to a cloud model'}
            heading=Gtk.Label(label=names.get(name,name),xalign=0,wrap=True);heading.add_css_class('brand');box.append(heading)
            hints={'shell.run':'This command runs as your user. Inspect every argument before allowing it.',
                'Cloud data disclosure':'Conversation text, relevant tool results, and requested images will leave this computer for this task.',
                'comfyui.run':'This workflow can use GPU resources and create files through your local ComfyUI installation.',
                'desktop.input':'The input is sent locally. The target application may use its own network connection.',
                'desktop.accessible_action':'The action is invoked locally. The target application may use its own network connection.',
                'app.launch':'The application opens locally and may use its own network connection.'}
            hint=Gtk.Label(label=hints.get(name,'Desktop Buddy needs permission to perform this sensitive action.'),xalign=0,wrap=True);hint.add_css_class('muted');box.append(hint)
            try:arguments=json.loads(detail)
            except ValueError:arguments=None
            summary=detail
            if isinstance(arguments,dict):
                if name=='shell.run':
                    import shlex
                    summary='Run: '+shlex.join(arguments.get('argv',[]))
                elif name=='browser.navigate':summary='Open '+str(arguments.get('url',''))
                elif name=='desktop.input':
                    action=arguments.get('action','')
                    if action=='type':summary='Type: '+str(arguments.get('text',''))
                    elif action=='key':summary='Press '+str(arguments.get('keys',''))
                    else:summary='Computer action: '+action+' · '+', '.join(str(k)+' '+str(v) for k,v in arguments.items() if k!='action')
                else:summary='\n'.join(k.replace('_',' ').capitalize()+': '+(json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else str(v)) for k,v in arguments.items())
            readable=Gtk.Label(label=self.redactor.text(summary),xalign=0,wrap=True,selectable=True)
            readable.set_wrap_mode(__import__('gi.repository',fromlist=['Pango']).Pango.WrapMode.WORD_CHAR)
            box.append(scroll(readable,100))
            expander=Gtk.Expander(label='Technical details')
            view=text_view();view.set_monospace(True);view.get_buffer().set_text(self.redactor.text(detail));expander.set_child(scroll(view,160));expander.set_expanded(name=='shell.run');box.append(expander)
            note=Gtk.Label(label='This approval applies once. Text from websites, files, and tools cannot approve actions.',xalign=0,wrap=True);note.add_css_class('muted');box.append(note)
            row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=10,halign=Gtk.Align.END);box.append(row)
            def finish(yes):
                decision[0]=yes and not token.event.is_set();done.set();dialog.destroy()
            if self.agent and self.agent.token is token:
                def use_yolo(*_):
                    if token.event.is_set():finish(False);return
                    self.enable_yolo_for_task(token)
                    finish(True)
                yolo = button('Use YOLO for this and future tasks', use_yolo)
                yolo.set_tooltip_text('Allow this action and skip future Buddy approvals, including cloud requests. Stop everything remains available.')
                box.insert_child_after(yolo, note)
            row.append(button('Skip action',lambda *_:finish(False)))
            row.append(button('Allow once',lambda *_:finish(True),'suggested-action'))
            stop=button('Stop everything',lambda *_:self.emergency(),'destructive-action');stop.set_tooltip_text('Cancel all queued actions and revoke desktop control');row.append(stop)
            dialog.connect('close-request',lambda *_:finish(False) or False)
            holder.append(dialog);self.pending.append((done,dialog));dialog.present();return False
        GLib.idle_add(show)
        try:
            while not done.wait(.05):token.check()
            token.check();return decision[0]
        finally:
            if holder:GLib.idle_add(holder[0].destroy)
            self.pending[:]=[(e,d) for e,d in self.pending if e is not done]

    def send(self,prompt=None):
        prompt=prompt or content(self.input).strip()
        if not prompt:return
        if self.worker and self.worker.is_alive():return self.send_during_task(prompt)
        if self.emergency_latched:
            self.emergency_latched=False;self.status.set_text('New user request resets the stop latch; desktop permission must be reconnected.')
        self.save_preferences();config=self.store.configs()[self.selected_id()]
        if not config.model:self.providers_dialog();return
        if self.local.get_active() and not config.local:self.notice('Offline mode is on. Open Task options (the gear beside Send) and turn off Keep Buddy offline to use this online model.');return
        if self.pending_images and not config.use_vision:self.notice('Images are turned off for this model. Enable images in Advanced settings, choose another model, or remove the attachment.');return
        workspace=Path(self.workspace.get_text()).expanduser().resolve()
        if not workspace.is_dir():self.notice(f'The working folder {workspace} does not exist. Open Task options (the gear beside Send) and choose an existing folder under “Folder Buddy can work in”.');return
        images=list(self.pending_images)
        if not self.store.messages(self.cid):
            with self.store.lock,self.store.db:self.store.db.execute('UPDATE conversations SET title=? WHERE id=?',(self.redactor.text(' '.join(prompt.split()))[:80],self.cid))
            self.refresh_history()
        # Persist before credentials/backends initialize so a startup failure cannot
        # erase the user's message or leave an unreferenced attachment behind.
        message={'role':'user','content':prompt}
        if images:message['images']=images
        self.store.add_message(self.cid,message)
        self.input.get_buffer().set_text('');self.save_draft();self.clear_attachments(False)
        self.conversation_title.set_text(next(r['title'] for r in self.store.conversations() if r['id']==self.cid))
        self.append('YOU',prompt)
        for image in images:self.append_image(image)
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
                registry=Registry(policy,self.store,self.desktop,self.a11y,self.clipboard,self.browser)
                self.start_token.check()
                self.agent=Agent(self.store,registry,Provider(config,key,self.redactor),self.emit)
                self.agent.token=self.start_token
                if self.emergency_latched:self.agent.stop()
                self.agent.run(cid,prompt,images=images,user_saved=True)
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
        images=list(self.pending_images)
        if images and self.active_model and not self.active_model.use_vision:
            self.notice('The model running this task cannot see images. Remove the attachment or wait for the task to finish.');return
        agent.say(prompt,images)
        self.input.get_buffer().set_text('');self.save_draft();self.clear_attachments(False)
        self.append('YOU',prompt)
        for image in images:self.append_image(image)
        self.status.set_text('Message received · Buddy reads it before its next step (a running command is not interrupted; press Stop to cancel it).')

    def resume(self):
        tasks=self.store.tasks(self.cid)
        if not tasks:self.notice('No previous task to resume.');return
        previous=tasks[0]
        if previous['status']=='complete':self.notice('The last task completed. Send a new request to continue.');return
        self.send('Resume the previous interrupted task using the conversation and this checkpoint. First inspect current state; never repeat an uncertain effect without asking. Checkpoint: '+previous['checkpoint'])

    def cancel(self):
        self.start_token.stop()
        if self.agent:self.agent.stop()
        for token in self.aux_tokens:token.stop()
        for done,dialog in list(self.pending):done.set();dialog.destroy()
        self.status.set_text('Cancellation requested · waiting for active I/O to return')

    def emergency(self):
        self.emergency_latched=True;self.cancel()
        if self.desktop:threading.Thread(target=self.desktop.close,daemon=True).start()
        self.browser.close()
        self.status.set_text('EMERGENCY STOP · queued actions cancelled; desktop session revoked')

    def auxiliary(self,func):
        token=StopToken();self.aux_tokens.append(token)
        def run():
            try:func(token)
            except Cancelled:self.emit('status','Operation cancelled.')
            except Exception as e:self.emit('error',str(e))
            finally:self.aux_tokens.remove(token)
        threading.Thread(target=run,daemon=True).start()
        return token

    def connect_desktop(self):
        self.status.set_text('Accept the GNOME screen-sharing and remote-control dialog to connect.')
        def work(t):
            self.ensure_backends();result=self.desktop.connect(t);self.emit('result',result);self.emit('status','Desktop connected through GNOME consent')
        self.auxiliary(work)

    def observe(self):
        def work(t):
            self.ensure_backends();result=self.desktop.screenshot(self.store.root/'screenshots'/'observation.png',t)
            self.emit('screenshot',result['path']);self.emit('result',self.a11y.inspect(depth=1));self.emit('status','Desktop observed locally · no model call made')
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
        d=Gtk.Window(title=title,application=self,transient_for=self.window,modal=True,default_width=760,default_height=560)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10);d.set_child(box)
        view=text_view(editable);view.set_monospace(True);view.get_buffer().set_text(text);box.append(scroll(view))
        if on_save:
            def save(*_):
                try:on_save(content(view));d.destroy()
                except Exception as e:self.status.set_text(str(e))
            box.append(button('Save',save,'suggested-action'))
        box.append(button('Close',lambda *_:d.destroy()));d.present();return d

    def memory_dialog(self):
        dialog, box = self.conversation_dialog('Memory',
            'Preferences and workflow notes saved on this computer. Changes take effect when you save.')
        dialog.set_default_size(680, 560)
        entries = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(scroll(entries, 240))
        rows = []
        def add_row(name='', value=''):
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            group.add_css_class('settings-section')
            top = Gtk.Box(spacing=8)
            key = Gtk.Entry(text=name, placeholder_text='For example: How I like my answers')
            key.set_hexpand(True)
            top.append(key)
            editor = text_view(True)
            editor.get_buffer().set_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
            item = (key, editor, not isinstance(value, str))
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
        actions.append(button('Add memory', lambda *_: add_row()))
        def save(*_):
            try:
                values = {}
                for key, editor, structured in rows:
                    name = key.get_text().strip()
                    if not name:raise ValueError('Every memory needs a name.')
                    if name in values:raise ValueError('Memory names must be unique.')
                    value = content(editor)
                    values[name] = json.loads(value) if structured else value
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
        dialog, box = self.conversation_dialog('Activity log', 'The latest 200 recorded events. Expand an event to inspect its details.')
        dialog.set_default_size(760, 620)
        events = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for record in self.store.audits():
            card = activity_card('RESULT', record['body'])
            stamp = datetime.fromtimestamp(record['created']).strftime('%b %d · %H:%M:%S')
            caption = Gtk.Label(label=stamp+'  ·  '+record['event'], xalign=0)
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

    def providers_dialog(self,preselect=None):
        from .model_ui import ConnectionDialog
        dialog=ConnectionDialog(self,preselect)
        dialog.window.controller=dialog
        return dialog.window

    def close_requested(self,*_):
        if not self.window.is_maximized():
            self.store.set('window_width',self.window.get_width())
            self.store.set('window_height',self.window.get_height())
        self.save_draft();self.clear_attachments(True);self.emergency()
        self.quit();return False
