"""Connect apps: one click for supported services, paste-a-key for the rest.

The person picks an app and signs in; Buddy remembers the connection. Only the
Advanced section exposes server addresses, commands or credential fields.
"""
import json
import shlex
import uuid
from urllib.parse import urlsplit
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk, GLib, Gio
from .mcp_catalog import search_catalog, consumer_available, consumer_name, catalog_item, availability_text, CATALOG_BY_ID
from .mcp_client import MCPConfig, error_text, sdk_available, probe_server
from .model_ui import entry, section, window
from .ui_widgets import label, button, scroller
from . import publisher


GROUP_ORDER=('Google','Work','Business','Developer','Reference')

# Plain steps for the one-time setup the Desktop Buddy team (or IT) does for
# services that only accept a pre-registered client.
CLIENT_GUIDES={
    'google':('Google',
        'https://console.cloud.google.com/apis/credentials',
        ['Open Google Cloud Console and create a project (or pick one).',
         'Under APIs & Services → Library, enable the Gmail, Calendar, Drive, Docs, Sheets and Slides APIs, and also their “MCP API” entries (Gmail MCP API, Calendar MCP API, Drive MCP API, Docs MCP API, Sheets MCP API, Slides MCP API).',
         'Enrol the project in the Google Workspace Developer Preview Program at developers.google.com/workspace/preview (Google confirms by email, usually within a few days). Until then Google’s MCP servers connect but refuse every action.',
         'Under OAuth consent screen, choose External, fill in the app name and your email, then add your users under Test users (or publish the app).',
         'Under Credentials → Create credentials → OAuth client ID, choose Desktop app.',
         'Copy the Client ID and Client secret into the boxes below.']),
    'slack':('Slack','https://api.slack.com/apps',
        ['Create a Slack app, add the MCP user scopes Slack documents, and add http://127.0.0.1:8765/oauth/callback as a redirect URL.',
         'Copy the Client ID and Client secret into the boxes below.']),
    'asana':('Asana','https://app.asana.com/0/my-apps',
        ['Create an app in Asana’s developer console with redirect URL http://127.0.0.1:8765/oauth/callback.',
         'Copy the Client ID and Client secret into the boxes below.']),
    'hubspot':('HubSpot','https://developers.hubspot.com/',
        ['Create a HubSpot app with redirect URL http://127.0.0.1:8765/oauth/callback and the MCP scopes HubSpot documents.',
         'Copy the Client ID and Client secret into the boxes below.']),
    'zendesk':('Zendesk','https://developer.zendesk.com/documentation/ai/mcp/',
        ['Register an OAuth client in Zendesk Admin Center with redirect URL http://127.0.0.1:8765/oauth/callback.',
         'Copy the Client ID and Client secret into the boxes below.']),
    'box':('Box','https://app.box.com/developers/console',
        ['Create a Box custom app (OAuth 2.0) with redirect URL http://127.0.0.1:8765/oauth/callback.',
         'Copy the Client ID and Client secret into the boxes below.']),
    'figma':('Figma','https://www.figma.com/developers/apps',
        ['Create a Figma app with redirect URL http://127.0.0.1:8765/oauth/callback and request the mcp:connect scope.',
         'Copy the Client ID and Client secret into the boxes below.']),
}


def open_link(uri):
    try:Gio.AppInfo.launch_default_for_uri(uri,None)
    except Exception:
        import webbrowser;webbrowser.open(uri)


class ConnectionsDialog:
    def __init__(self,app):
        self.app=app;self.manager=app.mcp;self.token=None;self.busy=False;self.closed=False
        self.window,outer=window(app,'Connect apps',width=760,height=760)
        self.window.connect('close-request',self.close)
        outer.append(label('Connect your apps','brand'))
        outer.append(label('Pick an app and choose Connect. If the app asks you to sign in, do that in your browser and come back. Buddy remembers the connection.','muted',wrap=True))
        self.stack=Gtk.Stack();switcher=Gtk.StackSwitcher(stack=self.stack);outer.append(switcher);outer.append(self.stack)
        self.stack.set_vexpand(True)
        saved=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        self.saved=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        saved.append(scroller(self.saved,minimum=160))
        saved.append(button('Connect another app',lambda *_:self.stack.set_visible_child_name('catalog'),'suggested-action'))
        self.stack.add_titled(saved,'saved','Your apps')
        catalog=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        self.search=Gtk.SearchEntry(placeholder_text='Find an app, such as Gmail, Notion or GitHub')
        self.search.connect('search-changed',lambda *_:self.fill_catalog())
        catalog.append(self.search)
        self.catalog=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        catalog.append(scroller(self.catalog,minimum=200))
        row=Gtk.Box(spacing=8)
        self.address=entry('Server address','');self.address.set_placeholder_text('Not listed? Paste the server address you were given (https://…) and choose Connect')
        self.address.set_tooltip_text('Buddy works out whether the server needs a sign-in. A local command also works here.')
        self.address.connect('activate',lambda *_:self.connect_address())
        row.append(self.address);row.append(button('Connect',lambda *_:self.connect_address(),'suggested-action'))
        catalog.append(row)
        self.stack.add_titled(catalog,'catalog','Add an app')
        self.status=label('','status',wrap=True);outer.append(self.status)
        self.advanced=Gtk.Expander(label='Advanced · for IT and developers')
        custom=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        custom.append(label('Local server commands, custom headers and sign-in keys for services that need them. Nobody needs this to connect a listed app.','muted',wrap=True))
        custom.append(button('Configure a custom server',lambda *_:self.edit()))
        custom.append(button('App sign-in keys (Google, Slack, …)',lambda *_:self.client_keys()))
        self.custom_saved=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6);custom.append(self.custom_saved)
        custom.append(Gtk.LinkButton(uri='https://registry.modelcontextprotocol.io/',label='Public MCP server registry'))
        self.advanced.set_child(custom);outer.append(self.advanced)
        outer.append(button('Close',lambda *_:self.close()))
        self.refresh();self.fill_catalog()
        self.stack.set_visible_child_name('saved' if self.manager.servers() else 'catalog')
        self.window.present()

    # ---- saved connections -------------------------------------------------
    def refresh(self):
        for box in (self.saved,self.custom_saved):
            while box.get_first_child():box.remove(box.get_first_child())
        for row in self.manager.servers():
            config=self.manager.configs()[row['id']];item=catalog_item(config)
            box=section(consumer_name(item) if item else config.name)
            unavailable=item is not None and not consumer_available(item,self.manager.registrations)
            state=(availability_text(item,self.manager.registrations) if unavailable else
                   'Turned off · choose Connect to use it again' if not config.enabled else
                   'Connected' if row['status']=='connected' else
                   'Sign-in needed · choose Connect' if row['status']=='sign-in needed' else 'Ready · connects when Buddy needs it')
            box.append(label(state,'muted',wrap=True))
            actions=Gtk.Box(spacing=8)
            if not unavailable:
                actions.append(button('Reconnect' if row['status']=='connected' else 'Connect',lambda _,c=config:self.connect_config(c),'suggested-action' if row['status']!='connected' else 'flat'))
                if config.enabled:actions.append(button('Turn off',lambda _,ident=config.id:self.disconnect(ident)))
            actions.append(button('Remove',lambda _,ident=config.id:self.remove(ident)))
            box.append(actions);self.saved.append(box)
            self.custom_saved.append(button('Edit '+config.name,lambda _,c=config:self.edit(c)))
        if not self.saved.get_first_child():self.saved.append(label('No apps connected yet. Choose Add an app to get started.',wrap=True))

    # ---- catalog -----------------------------------------------------------
    def fill_catalog(self):
        while self.catalog.get_first_child():self.catalog.remove(self.catalog.get_first_child())
        self.catalog_buttons={}
        items=search_catalog(self.search.get_text())
        connected={catalog_item(c)['id'] for c in self.manager.configs().values() if catalog_item(c)}
        for group in GROUP_ORDER:
            members=[i for i in items if i['group']==group]
            if not members:continue
            self.catalog.append(label(group.upper(),'eyebrow'))
            for item in sorted(members,key=lambda i:(not consumer_available(i,self.manager.registrations),i['name'].casefold())):
                self.catalog.append(self.card(item,item['id'] in connected))

    def card(self,item,connected):
        box=Gtk.Box(spacing=12);box.add_css_class('settings-section')
        words=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3,hexpand=True,valign=Gtk.Align.CENTER)
        words.append(label(consumer_name(item),'step-title',wrap=True))
        words.append(label(item['description'],'muted',wrap=True))
        available=consumer_available(item,self.manager.registrations)
        kind=item.get('sign_in','dcr')
        if not available:words.append(label(availability_text(item,self.manager.registrations),'status',wrap=True))
        box.append(words)
        actions=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4,valign=Gtk.Align.CENTER)
        if available:
            text='Connected' if connected else 'Connect with a token' if kind=='token' else 'Connect'
            connect=button(text,lambda _,item=item:self.from_catalog(item),'suggested-action' if not connected else 'flat')
            connect.set_sensitive(not self.busy)
        elif kind=='client':
            connect=button('Enter sign-in key (IT)…',lambda _,item=item:self.client_keys(publisher.group_of(item['id'])))
        else:
            connect=button('Not available yet',lambda *_:None);connect.set_sensitive(False)
        self.catalog_buttons[item['id']]=connect;actions.append(connect)
        if item.get('docs'):
            more=Gtk.LinkButton(uri=item['docs'],label='About');more.add_css_class('flat');more.set_halign(Gtk.Align.CENTER);actions.append(more)
        box.append(actions)
        return box

    def from_catalog(self,item):
        if not consumer_available(item,self.manager.registrations):
            self.status.set_text(availability_text(item,self.manager.registrations));return None
        config=next((c for c in self.manager.configs().values() if catalog_item(c)==item),None)
        if config is None:
            config=MCPConfig(id=item['id']+'-'+uuid.uuid4().hex[:8],name=consumer_name(item),
                url=item['url'],auth=item['auth'],domains=item['domains'],scope=item.get('scope','') if item.get('sign_in')=='client' else '')
            if item['command']:config=MCPConfig(id=item['id']+'-'+uuid.uuid4().hex[:8],name=consumer_name(item),transport='stdio',command=item['command'],auth='none',domains=item['domains'])
        if item.get('sign_in')=='token':return self.token_dialog(item,config)
        return self.connect_config(config)

    # ---- connecting --------------------------------------------------------
    def preflight(self,config):
        if self.busy:return False
        item=catalog_item(config)
        if item is not None and not consumer_available(item,self.manager.registrations):
            self.status.set_text(availability_text(item,self.manager.registrations));return False
        if self.app.local.get_active():
            self.status.set_text('Buddy is offline. Turn off Keep Buddy offline (in Settings) to connect an app.');return False
        if not sdk_available():
            self.status.set_text('App connections are missing from this installation. Reinstall Desktop Buddy.');return False
        return True

    def connect_config(self,config,manual=None):
        from dataclasses import replace
        if not self.preflight(config):return
        config=replace(config,enabled=True)
        self.status.set_text('Opening sign-in in your browser… approve access there, then come back here.' if config.auth=='oauth' else 'Connecting…')
        def work(token):
            token.check()
            prepared=self.manager.registrations.prepare(config,explicit=True)
            self.manager.save(prepared,manual)
            return self.manager.tools(config.id,token,interactive=True)
        def done(tools):
            self.status.set_text(f'{config.name} is connected. Buddy can use {len(tools)} things there and will remember this connection.')
            self.stack.set_visible_child_name('saved')
        self.run(work,done)

    def connect_address(self):
        text=self.address.get_text().strip()
        if not text:self.status.set_text('Paste a server address first.');return
        if self.busy:return
        if text.startswith(('http://','https://')):
            host=urlsplit(text).hostname or ''
            name=host.removeprefix('mcp.').removeprefix('www.').split('.')[0].capitalize() or 'Custom server'
            config=MCPConfig(id='custom-'+uuid.uuid4().hex[:8],name=name,url=text,auth='oauth')
            try:config.validate()
            except ValueError as error:self.status.set_text(str(error));return
            if not self.preflight(config):return
            self.status.set_text('Checking the server…')
            def work(token):
                token.check()
                kind=probe_server(text)
                from dataclasses import replace
                found=replace(config,auth='oauth' if kind=='oauth' else 'none',enabled=True)
                if kind=='oauth':GLib.idle_add(lambda:self.status.set_text('This server asks you to sign in. Finish that in your browser, then come back here.') or False)
                self.manager.save(found)
                return found,self.manager.tools(found.id,token,interactive=True)
            def done(value):
                found,tools=value
                self.address.set_text('')
                self.status.set_text(f'{found.name} is connected. Buddy can use {len(tools)} things there and will remember this connection.')
                self.stack.set_visible_child_name('saved')
            self.run(work,done)
        else:
            try:command=shlex.split(text)
            except ValueError as error:self.status.set_text('That does not look like an address or a command: '+str(error));return
            config=MCPConfig(id='custom-'+uuid.uuid4().hex[:8],name=command[0].rsplit('/',1)[-1] if command else 'Local server',transport='stdio',command=command,auth='none')
            try:config.validate()
            except ValueError as error:self.status.set_text(str(error));return
            self.status.set_text('Starting the local server…')
            self.connect_config(config)

    def token_dialog(self,item,config):
        dialog,outer=window(self.app,'Connect '+item['name'],width=560,height=420)
        dialog.set_transient_for(self.window)
        outer.append(label('Connect '+item['name'],'brand'))
        outer.append(label('1. Open '+item['name']+' and create a personal access token. Give it the access you want Buddy to have.','muted',wrap=True))
        if item.get('token_url'):outer.append(button('Open '+item['name']+' to create a token',lambda *_:open_link(item['token_url']),''))
        outer.append(label('2. Copy the token and paste it here. Buddy keeps it in your computer’s keyring.','muted',wrap=True))
        field=Gtk.PasswordEntry(show_peek_icon=True);field.set_property('placeholder-text','Paste the token');outer.append(field)
        status=label('','status',wrap=True);outer.append(status)
        row=Gtk.Box(spacing=8)
        def go(*_):
            value=field.get_text().strip()
            if not value:status.set_text('Paste the token first.');return
            dialog.destroy();self.connect_config(config,{'bearer':value})
        field.connect('activate',go)
        row.append(button('Connect',go,'suggested-action'));row.append(button('Cancel',lambda *_:dialog.destroy()))
        outer.append(row);dialog.present();return dialog

    def client_keys(self,group=None):
        dialog=ClientKeysDialog(self,group);dialog.window.controller=dialog;return dialog

    def run(self,work,done):
        if self.busy:return
        self.busy=True
        for widget in (self.saved,self.catalog,self.advanced):widget.set_sensitive(False)
        def finish(value,error):
            if self.closed:return False
            self.busy=False
            for widget in (self.saved,self.catalog,self.advanced):widget.set_sensitive(True)
            if error:
                self.app.store.audit('','connection_error',{'error':self.app.redactor.text(error)})
                self.status.set_text(connection_error(error))
            else:done(value)
            self.refresh();self.fill_catalog();return False
        def worker(token):
            try:GLib.idle_add(finish,work(token),None)
            except Exception as error:GLib.idle_add(finish,None,error_text(error))
        self.token=self.app.auxiliary(worker)

    def disconnect(self,ident):
        from dataclasses import replace
        config=self.manager.configs()[ident]
        self.run(lambda t:self.manager.save(replace(config,enabled=False)),lambda _:self.status.set_text('Turned off. Your sign-in is kept, so Connect works without signing in again.'))

    def remove(self,ident):
        self.run(lambda t:self.manager.remove(ident),lambda _:self.status.set_text('Removed from Buddy. To take back the app’s permission completely, also remove Desktop Buddy in that app’s account settings.'))

    def edit(self,config=None,new=False):
        editor=ConnectionEditor(self,config,new or config is None)
        editor.window.controller=editor
        return editor

    def close(self,*_):
        self.closed=True
        if self.token:self.token.stop()
        self.window.destroy();return True


def connection_error(error):
    value=error.lower()
    if 'public tool discovery' in value or 'sign-in was not completed' in value:
        return 'The app answered, but you are not signed in to it yet. Choose Connect and finish the sign-in in your browser.'
    if 'declined' in value or 'sign in from' in value:
        return 'Sign-in was not finished. Choose Connect when you want to try again.'
    if 'timed out' in value or 'timeout' in value:
        return 'That took too long. Check your internet connection and choose Connect again.'
    if 'invalid_client' in value or 'unauthorized_client' in value or 'registration' in value:
        return 'This app did not accept Buddy’s sign-in request. The app may need a sign-in key from the Desktop Buddy team (Advanced → App sign-in keys).'
    if '401' in value or '403' in value or 'unauthorized' in value or 'forbidden' in value:
        return 'The app refused access. Check the token or sign in again with Connect.'
    if 'keyring' in value:
        return 'Buddy could not save your sign-in. Unlock your computer’s password keyring and try again.'
    if 'no such file' in value or 'not found' in value and 'command' in value:
        return 'The program for this app is not installed on this computer.'
    if 'name or service not known' in value or 'connecterror' in value or 'connection refused' in value or 'nodename' in value:
        return 'Buddy could not reach that address. Check it and your internet connection.'
    return 'Could not connect. Details are in Recent activity. You can try again with Connect.'


class ClientKeysDialog:
    """IT pastes the client a provider issued to the Desktop Buddy team."""
    def __init__(self,parent,group=None):
        self.parent=parent;self.app=parent.app;self.manager=parent.manager
        self.window,outer=window(self.app,'App sign-in keys',width=640,height=700)
        self.window.set_transient_for(parent.window)
        outer.append(label('App sign-in keys','brand'))
        outer.append(label('A few services only accept sign-in through a client registered by the Desktop Buddy team. Paste that client here once; everyone using this installation then simply chooses Connect.','muted',wrap=True))
        groups=list(CLIENT_GUIDES)
        self.group=Gtk.DropDown.new_from_strings([CLIENT_GUIDES[g][0] for g in groups]);self.groups=groups
        self.group.set_selected(groups.index(group) if group in groups else 0)
        outer.append(label('Service'));outer.append(self.group)
        self.state=label('','muted',wrap=True);outer.append(self.state)
        self.steps=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6);outer.append(scroller(self.steps,minimum=120))
        self.open=button('Open the provider’s developer page',lambda *_:open_link(CLIENT_GUIDES[self.selected()][1]));outer.append(self.open)
        self.client_id=entry('Client ID');outer.append(label('Client ID'));outer.append(self.client_id)
        self.client_secret=Gtk.PasswordEntry(show_peek_icon=True);outer.append(label('Client secret (leave empty if the provider did not give one)'));outer.append(self.client_secret)
        self.status=label('','status',wrap=True);outer.append(self.status)
        row=Gtk.Box(spacing=8)
        row.append(button('Save',lambda *_:self.save(),'suggested-action'))
        self.forget_button=button('Forget this key',lambda *_:self.forget());row.append(self.forget_button)
        row.append(button('Close',lambda *_:self.window.destroy()))
        outer.append(row)
        self.group.connect('notify::selected',lambda *_:self.refresh())
        self.refresh();self.window.present()

    def selected(self):return self.groups[self.group.get_selected()]

    def refresh(self):
        group=self.selected()
        while self.steps.get_first_child():self.steps.remove(self.steps.get_first_child())
        for index,step in enumerate(CLIENT_GUIDES[group][2],1):self.steps.append(label(f'{index}. {step}',wrap=True))
        services=self.manager.registrations.services_in_group(group)
        sources={self.manager.registrations.source(CATALOG_BY_ID[s]) for s in services}
        if 'installation' in sources:text='A key is saved on this installation. Connect is available for '+CLIENT_GUIDES[group][0]+'.'
        elif 'shipped' in sources:text='A key came with this version of Desktop Buddy. Connect is available for '+CLIENT_GUIDES[group][0]+'.'
        else:text='No key yet. '+CLIENT_GUIDES[group][0]+' shows “needs a one-time setup” until one is saved.'
        self.state.set_text(text);self.forget_button.set_sensitive('installation' in sources)

    def save(self):
        group=self.selected();client_id=self.client_id.get_text();secret=self.client_secret.get_text()
        self.status.set_text('Saving in your computer’s keyring…')
        def work(token):
            token.check()
            services=self.manager.registrations.install_client(group,client_id,secret)
            self.app.store.audit('','app_registrations_imported',{'services':services})
        def done(_):
            self.client_id.set_text('');self.client_secret.set_text('')
            self.status.set_text('Saved. '+CLIENT_GUIDES[group][0]+' now shows Connect in Add an app.')
            self.refresh();self.parent.refresh();self.parent.fill_catalog()
        self.run(work,done)

    def forget(self):
        group=self.selected()
        def work(token):self.manager.registrations.forget(group)
        def done(_):
            self.status.set_text('Removed. Existing connections to '+CLIENT_GUIDES[group][0]+' will need a new key before they can sign in again.')
            self.refresh();self.parent.refresh();self.parent.fill_catalog()
        self.run(work,done)

    def run(self,work,done):
        def finish(value,error):
            if error:self.status.set_text(self.app.redactor.text(error))
            else:done(value)
            return False
        def worker(token):
            try:GLib.idle_add(finish,work(token),None)
            except ValueError as error:GLib.idle_add(finish,None,str(error))
            except Exception as error:GLib.idle_add(finish,None,'Could not save the key. Check that your password keyring is unlocked. '+error_text(error))
        self.app.auxiliary(worker)


class ConnectionEditor:
    def __init__(self,parent,config,new):
        self.parent=parent;self.app=parent.app;self.manager=parent.manager;self.new=new
        self.original=config or MCPConfig(uuid.uuid4().hex,'')
        self.token=None;self.closed=False;self.busy=False
        self.window,outer=window(self.app,'Advanced connection settings',width=680,height=740)
        self.window.connect('close-request',self.close)
        body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        outer.append(scroller(body,minimum=320))
        self.name=entry('Connection name',self.original.name);body.append(label('Connection name'));body.append(self.name)
        self.transport=Gtk.DropDown.new_from_strings(['Remote HTTP','Local command (stdio)'])
        self.transport.set_selected(0 if self.original.transport=='http' else 1)
        body.append(label('Connection type'));body.append(self.transport)
        self.url=entry('MCP server URL',self.original.url);body.append(label('MCP server URL'));body.append(self.url)
        self.command=entry('Server command',shlex.join(self.original.command))
        body.append(label('Local server command (no shell expansion)'));body.append(self.command)
        body.append(label('A local server runs as your user. Install it separately and review its command before connecting.','muted',wrap=True))
        self.auth=Gtk.DropDown.new_from_strings(['OAuth sign-in','Bearer token','None / server-managed'])
        self.auth.set_selected(['oauth','bearer','none'].index(self.original.auth))
        body.append(label('Authentication'));body.append(self.auth)
        self.bearer=Gtk.PasswordEntry(show_peek_icon=True)
        body.append(label('Bearer token · leave blank to keep saved token'));body.append(self.bearer)
        self.enabled=Gtk.CheckButton(label='Enabled · reconnect when needed');self.enabled.set_active(self.original.enabled);body.append(self.enabled)
        advanced=Gtk.Expander(label='Advanced · environment, headers and OAuth app')
        fields=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8);advanced.set_child(fields);body.append(advanced)
        self.cwd=entry('Working directory',self.original.cwd)
        self.domains=entry('Service domains',', '.join(self.original.domains))
        self.port=entry('OAuth callback port',self.original.callback_port)
        self.scope=entry('OAuth scopes',self.original.scope)
        self.client_id=entry('Registered OAuth client ID')
        self.client_secret=Gtk.PasswordEntry(show_peek_icon=True)
        self.env=Gtk.PasswordEntry(show_peek_icon=True)
        self.headers=Gtk.PasswordEntry(show_peek_icon=True)
        for title,widget in [('Working directory (stdio)',self.cwd),('Service website domains · comma separated',self.domains),
                ('Secret environment · JSON object; blank keeps saved values',self.env),
                ('Secret HTTP headers · JSON object; blank keeps saved values',self.headers),
                ('OAuth callback port',self.port),('OAuth scopes · space separated',self.scope),
                ('Registered OAuth client ID · blank keeps saved client',self.client_id),('OAuth client secret · enter with a replacement client ID',self.client_secret)]:
            fields.append(label(title,'muted',wrap=True));fields.append(widget)
        fields.append(label('OAuth uses http://127.0.0.1:<port>/oauth/callback. Register this URI if the provider requires your own OAuth app. Tokens and secrets are stored in GNOME Keyring.','status',wrap=True))
        self.status=label('Save settings, then connect to verify tools. Sign-in opens your system browser.','status',wrap=True);outer.append(self.status)
        controls=Gtk.Box(spacing=8)
        self.save_button=button('Save',lambda *_:self.save())
        self.connect_button=button('Save & connect',lambda *_:self.save(True),'suggested-action')
        controls.append(self.save_button);controls.append(self.connect_button)
        self.remove_button=button('Remove',lambda *_:self.remove())
        self.remove_button.set_visible(not new);controls.append(self.remove_button)
        controls.append(button('Close',lambda *_:self.close()))
        outer.append(controls)
        self.editable=[self.name,self.transport,self.url,self.command,self.auth,self.bearer,self.enabled,self.cwd,self.domains,self.port,self.scope,self.client_id,self.client_secret,self.env,self.headers,self.save_button,self.connect_button,self.remove_button]
        self.window.present()

    def config(self):
        return MCPConfig(id=self.original.id,name=self.name.get_text().strip(),transport=('http','stdio')[self.transport.get_selected()],
            url=self.url.get_text().strip(),command=shlex.split(self.command.get_text()),cwd=self.cwd.get_text().strip(),
            auth=('oauth','bearer','none')[self.auth.get_selected()],enabled=self.enabled.get_active(),
            domains=[d.strip() for d in self.domains.get_text().split(',') if d.strip()],callback_port=int(self.port.get_text()),scope=self.scope.get_text().strip()).validate()

    def run(self,work,done):
        if self.busy:return
        self.busy=True
        for w in self.editable:w.set_sensitive(False)
        def finish(value=None,error=None):
            if self.closed:return False
            self.busy=False
            for w in self.editable:w.set_sensitive(True)
            if error:self.status.set_text(self.app.redactor.text(error))
            else:done(value)
            self.parent.refresh();return False
        def worker(token):
            try:GLib.idle_add(finish,work(token),None)
            except Exception as error:GLib.idle_add(finish,None,error_text(error))
        self.token=self.app.auxiliary(worker)

    def save(self,connect=False):
        if self.busy:return
        try:
            config=self.config()
            if connect and self.app.local.get_active():raise ValueError('Turn off Keep Buddy offline before connecting to MCP services.')
            bearer=self.bearer.get_text();env=self.env.get_text();headers=self.headers.get_text()
            values={}
            if bearer:values['bearer']=bearer
            if env:values['env']=json.loads(env)
            if headers:values['headers']=json.loads(headers)
            client_info=None
            if self.client_secret.get_text() and not self.client_id.get_text().strip():
                raise ValueError('Enter the client ID with its replacement secret.')
            if self.client_id.get_text().strip():
                client_info={'client_id':self.client_id.get_text().strip(),'redirect_uris':[f'http://127.0.0.1:{config.callback_port}/oauth/callback'],
                    'token_endpoint_auth_method':'client_secret_post' if self.client_secret.get_text() else 'none'}
                if self.client_secret.get_text():client_info['client_secret']=self.client_secret.get_text()
        except Exception as error:self.status.set_text(str(error));return
        self.status.set_text('Connecting… complete sign-in in your browser if asked.' if connect else 'Saving connection…')
        def work(token):
            from .mcp_auth import MCPTokenStorage
            manual=None
            if values:
                manual=MCPTokenStorage(self.manager.credentials,config.secret_key).read('manual') or {}
                manual.update(values)
            token.check();self.manager.save(config,manual,client_info)
            return self.manager.tools(config.id,token,interactive=True) if connect else None
        def done(tools):
            self.original=config;self.new=False;self.remove_button.set_visible(True)
            # Treat ID and secret as one replacement. Leaving the ID visible after
            # clearing its secret turned the next Save into a secretless client.
            for w in (self.bearer,self.env,self.headers,self.client_id,self.client_secret):w.set_text('')
            self.status.set_text(f'Server reachable · {len(tools)} tools found.' if tools is not None else 'Saved · Buddy will reconnect when needed. Use Save & connect for first sign-in.')
        self.run(work,done)

    def remove(self):
        self.status.set_text('Removing this connection and its saved credentials…')
        self.run(lambda token:self.manager.remove(self.original.id),lambda _:self.close())

    def close(self,*_):
        self.closed=True
        if self.token:self.token.stop()
        self.window.destroy();return True
