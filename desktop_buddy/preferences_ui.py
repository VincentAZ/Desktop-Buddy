"""Everyday settings, separated from administrator/developer configuration."""
from pathlib import Path
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk
from .model_ui import window,section
from .ui_widgets import label,button,scroller
from .state import PROVIDERS


class PreferencesDialog:
    def __init__(self,app):
        self.app=app;self.handlers=[]
        self.window,outer=window(app,'Settings',width=640,height=720)
        self.window.connect('close-request',self.close)
        self.heading=label('Make Buddy work for you','brand');outer.append(self.heading)
        self.stack=Gtk.Stack(vexpand=True);outer.append(self.stack);self.it_setup=False
        body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14)
        self.stack.add_named(scroller(body,minimum=240),'everyday')
        assistant=section('Your assistant');self.assistant_status=label('','muted',wrap=True);assistant.append(self.assistant_status)
        self.refresh_assistant();body.append(assistant)
        privacy=section('Internet access')
        self.offline=Gtk.CheckButton(label='Keep Buddy offline')
        self.offline.set_active(app.local.get_active())
        self.offline.connect('toggled',lambda w:app.local.set_active(w.get_active()))
        self.handlers.append((app.local,app.local.connect('toggled',lambda w:self.offline.set_active(w.get_active()))))
        privacy.append(self.offline)
        privacy.append(label('Buddy can use files and look at your screen with an assistant already set up on this computer. Connected apps, online requests, opening apps, typing and clicking are turned off. Changing this setting while Buddy is working stops all tasks.','muted',wrap=True))
        body.append(privacy)
        browsing=section('Browsing the web')
        self.browser_visible=Gtk.CheckButton(label='Show the browser window while Buddy browses')
        self.browser_visible.set_active(app.store.get('browser_visible',True))
        self.browser_visible.connect('toggled',self.browser_visibility_changed)
        browsing.append(self.browser_visible)
        browsing.append(label('Buddy uses its own browser window, separate from yours. Turn this off to let conversations browse in parallel in the background. Visible browser tasks take turns using the computer.','muted',wrap=True))
        body.append(browsing)
        folder=section('Where Buddy saves your work')
        self.folder=label('',wrap=True);self.refresh_folder();folder.append(self.folder)
        self.handlers.append((app.workspace,app.workspace.connect('changed',lambda *_:self.refresh_folder())))
        folder.append(button('Choose a different folder…',lambda *_:app.choose_workspace()))
        folder.append(label('Buddy asks before working with files outside this folder.','muted',wrap=True));body.append(folder)
        personal=section('Your apps and preferences')
        personal.append(button('Help using Buddy',lambda *_:self.show_help()))
        personal.append(button('Connected apps',lambda *_:app.mcp_dialog()))
        personal.append(button('Things to remember',lambda *_:app.memory_dialog()))
        body.append(personal)
        advanced=section('Assistants and vendors')
        advanced.append(label('For the IT manager setting up this installation. Choose a saved assistant or configure a different vendor. Changes apply to the next task; a running task keeps its current assistant.','muted',wrap=True))
        advanced.append(app.model_menu.button);self.assistant_controls=advanced
        advanced.append(button('Add or configure a vendor…',lambda *_:app.providers_dialog()))
        keys=section('App sign-in keys')
        keys.append(label('Google, Slack, Asana, HubSpot, Zendesk, Box and Figma only accept sign-in through a client registered by the Desktop Buddy team. Paste it once here and everyone on this computer can simply choose Connect.','muted',wrap=True))
        keys.append(button('Enter or check app sign-in keys…',lambda *_:self.client_keys()))
        keys.append(button('Import a registration file…',lambda *_:self.import_registrations()))
        self.registration_status=label('','status',wrap=True);keys.append(self.registration_status)
        advanced.append(keys)
        advanced.append(button('Technical activity details',lambda *_:app.audit_dialog()))
        self.stack.add_named(scroller(advanced,minimum=240),'it')
        controls=Gtk.Box(spacing=10)
        self.it_button=button('IT setup…',lambda *_:self.show_it(not self.it_setup))
        controls.append(self.it_button);controls.append(Gtk.Box(hexpand=True))
        from . import __version__
        controls.append(label('Desktop Buddy '+__version__,'muted'))
        controls.append(button('Done',lambda *_:self.close(),'suggested-action'));outer.append(controls)
        self.window.present()

    def browser_visibility_changed(self,widget):
        visible=widget.get_active()
        self.app.store.set('browser_visible',visible)
        for session in self.app.sessions.values():session.browser.headless=not visible
        # The next page Buddy opens uses the new setting; close the current window so it applies.
        for session in self.app.sessions.values():session.browser.close()

    def client_keys(self,group=None):
        from .mcp_ui import ClientKeysDialog
        dialog=ClientKeysDialog(self,group);dialog.window.controller=dialog;return dialog

    # ClientKeysDialog expects a parent with a window, refresh and fill_catalog.
    def refresh(self):self.registration_status.set_text('Saved. Open Connect apps to connect.')
    def fill_catalog(self):pass

    @property
    def manager(self):return self.app.mcp

    def show_it(self,enabled=True):
        self.it_setup=enabled
        self.stack.set_visible_child_name('it' if enabled else 'everyday')
        self.heading.set_text('IT setup' if enabled else 'Make Buddy work for you')
        self.it_button.set_label('Back to settings' if enabled else 'IT setup…')

    def import_registrations(self):
        chooser=Gtk.FileDialog(title='Import publisher app registrations',modal=True)
        def selected(dialog,result):
            try:file=dialog.open_finish(result)
            except Exception:return  # Closing the chooser changes nothing.
            path=file.get_path()
            if not path:
                self.registration_status.set_text('Choose a local registration bundle.');return
            self.registration_status.set_text('Saving app registrations securely…')
            from gi.repository import GLib
            def finished(message):
                self.registration_status.set_text(message);return False
            def work(token):
                try:
                    token.check()
                    services=self.app.mcp.registrations.import_file(path)
                    self.app.store.audit('','app_registrations_imported',{'services':services})
                    message='Imported. Open Connected apps and choose Connect to sign in. Account access has not been tested yet.'
                except ValueError as error:message=str(error)
                except Exception:
                    message='Could not save app registrations. Check that your password keyring is unlocked, then try again.'
                GLib.idle_add(finished,message)
            self.app.auxiliary(work)
        chooser.open(self.window,None,selected)

    def refresh_assistant(self):
        config=self.app.store.configs()[self.app.selected_id()]
        if config.model:
            self.assistant_status.set_text('Your assistant is configured to use '+PROVIDERS[config.id][0]+'. '+
                ('Your conversations are processed on this computer.' if config.local else 'Your conversation is sent to this service when you allow it.'))
        else:
            self.assistant_status.set_text('Your IT team has not finished assistant setup. Ask them to open IT setup below and choose an assistant. You do not need to find connection keys yourself.')

    def refresh_folder(self):
        path=Path(self.app.workspace.get_text()).expanduser()
        from .paths import default_workspace
        self.folder.set_text('Buddy’s work folder' if path==default_workspace(self.app.store.root) else path.name or str(path))
        self.folder.set_tooltip_text(str(path))

    def show_help(self):
        from .help import help_topics
        dialog,outer=window(self.app,'Help using Buddy',height=720)
        dialog.set_transient_for(self.window)
        body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=14)
        for topic in help_topics():
            box=section(topic['title']);box.append(label(topic['instructions'],wrap=True));body.append(box)
        outer.append(scroller(body,minimum=240))
        outer.append(button('Done',lambda *_:dialog.destroy(),'suggested-action'))
        dialog.present()
        return dialog

    def close(self,*_):
        for widget,ident in self.handlers:widget.disconnect(ident)
        self.handlers=[]
        if self.app.model_menu.button.get_parent() is self.assistant_controls:
            self.assistant_controls.remove(self.app.model_menu.button)
        self.app.preferences_window=None
        self.window.destroy();return True
