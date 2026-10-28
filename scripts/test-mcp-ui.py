#!/usr/bin/env python3
"""Native GTK MCP setup, connection, persistence and removal using a local peer."""
from pathlib import Path
import json
import sys
import tempfile
import threading
import time
import socket
from urllib.parse import parse_qs,urlsplit,urlencode
from unittest.mock import patch
import requests
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Graphene','1.0')
from gi.repository import Gtk,GLib,Graphene
from desktop_buddy.ui import BuddyApp
from desktop_buddy.mcp_client import MCPManager,sdk_available
from desktop_buddy.mcp_auth import MCPTokenStorage,OAuthCallback
from test_mcp import Credentials,LateOAuthFixture
from desktop_buddy.mcp_catalog import search_catalog, CATALOG

if not sdk_available():
    raise SystemExit('MCP support is a required dependency. This installation cannot pass the connected-app check.')

root=Path(__file__).resolve().parents[1]/'.test-data'/'mcp-ui';root.mkdir(parents=True,exist_ok=True)
profile=tempfile.TemporaryDirectory(dir=root)
server=ThreadingHTTPServer(('127.0.0.1',0),LateOAuthFixture)
server.grants=[];server.use_oauth=True
with socket.socket() as sock:sock.bind(('127.0.0.1',0));callback_port=sock.getsockname()[1]
def authorize(url):
    values=parse_qs(urlsplit(url).query)
    assert values['client_id']==['publisher-ui-fixture']
    assert values['scope']==['fixture.read']
    server.challenge=values['code_challenge'][0]
    requests.get(values['redirect_uri'][0]+'?'+urlencode({'code':'fixture-code','state':values['state'][0]}),timeout=3)
    return True
threading.Thread(target=server.serve_forever,daemon=True).start()
app=BuddyApp(Path(profile.name)/'state');app.credentials=Credentials(app.redactor);app.mcp=MCPManager(app.store,app.credentials)
app.store.set('local_only',False)
phase=0;started=time.monotonic();result={};dialog=None;editor=None;consumer_id=None
# This fixture covers a build that ships no Google key (IT pastes one); the release build ships one.
from desktop_buddy import publisher
publisher.CLIENTS.clear()
google_item=next(i for i in CATALOG if i['id']=='google-gmail')
google_original_url=google_item['url']

def capture(window,name):
    paintable=Gtk.WidgetPaintable.new(window);snapshot=Gtk.Snapshot.new()
    paintable.snapshot(snapshot,float(window.get_width()),float(window.get_height()))
    node=snapshot.to_node()
    if node is None:return False
    rect=Graphene.Rect();rect.init(0,0,window.get_width(),window.get_height())
    texture=window.get_renderer().render_texture(node,rect);texture.save_to_png(str(root/name));return True

def tick():
    global phase,dialog,editor,consumer_id
    try:
        if time.monotonic()-started>45:raise TimeoutError('MCP UI fixture timed out')
        if phase==0:
            dialog=app.mcp_dialog().controller
            dialog.search.set_text('gmail');dialog.fill_catalog()
            assert set(dialog.catalog_buttons)=={'google-gmail','google-workspace'}
            assert dialog.catalog_buttons['google-gmail'].get_label().startswith('Enter sign-in key'),dialog.catalog_buttons['google-gmail'].get_label()
            assert not dialog.catalog_buttons['google-workspace'].get_sensitive()
            assert not dialog.advanced.get_expanded()
            assert dialog.from_catalog(search_catalog('gmail')[0]) is None
            assert not app.mcp.configs()
            result['unavailable_google_does_not_open_technical_setup']=True
            server.expected_bearer='fixture-oauth-access'
            # Controlled endpoint: exercise publisher import through normal Connect,
            # without contacting Google or presenting a false live authorization.
            google_item['url']=f'http://127.0.0.1:{server.server_port}/mcp'
            bundle=Path(profile.name)/'publisher.json'
            bundle.write_text(json.dumps({'version':1,'registrations':[{
                'service':'google-gmail','scope':'fixture.read','callback_port':callback_port,
                'client':{'client_id':'publisher-ui-fixture','token_endpoint_auth_method':'none',
                          'redirect_uris':[f'http://127.0.0.1:{callback_port}/oauth/callback']}}]}))
            app.mcp.registrations.import_file(bundle)
            dialog.fill_catalog()
            assert dialog.catalog_buttons['google-gmail'].get_label()=='Connect',dialog.catalog_buttons['google-gmail'].get_label()
            assert not dialog.catalog_buttons['google-workspace'].get_sensitive()
            dialog.from_catalog(google_item);phase=10
        elif phase==10 and not dialog.busy:
            assert 'is connected' in dialog.status.get_text(),dialog.status.get_text()
            consumer_id=app.mcp.servers()[0]['id']
            assert app.mcp.configs()[consumer_id].url==f'http://127.0.0.1:{server.server_port}/mcp'
            saved=app.mcp.configs()[consumer_id]
            assert saved.scope=='fixture.read'
            assert MCPTokenStorage(app.credentials,saved.secret_key).read('client')['client_id']=='publisher-ui-fixture'
            assert MCPTokenStorage(app.credentials,saved.secret_key).read('tokens')['access_token']=='fixture-oauth-access'
            assert len(server.grants)==1
            result['late_sign_in_completed_before_connected_message']=True
            result['publisher_registration_used_by_normal_connect']=True
            result['consumer_connect_without_technical_fields']=True
            dialog.remove(consumer_id);phase=11
        elif phase==11 and not dialog.busy:
            assert not app.mcp.servers()
            google_item['url']=google_original_url
            server.use_oauth=False
            server.expected_bearer='fixture-bearer-123456'
            dialog.search.set_text('');dialog.fill_catalog();dialog.stack.set_visible_child_name('catalog')
            phase=12
        elif phase==12 and capture(dialog.window,'connected-apps.png'):
            editor=dialog.edit();editor.name.set_text('Local HTTP fixture')
            editor.url.set_text(f'http://127.0.0.1:{server.server_port}/mcp')
            editor.client_id.set_text('registered-fixture-client')
            editor.client_secret.set_text('registered-fixture-secret')
            editor.save_button.emit('clicked');phase=13
        elif phase==13 and not editor.busy:
            assert editor.status.get_text().startswith('Saved'),editor.status.get_text()
            assert not editor.client_id.get_text() and not editor.client_secret.get_text()
            storage=MCPTokenStorage(app.credentials,editor.original.secret_key)
            assert storage.read('client')['client_secret']=='registered-fixture-secret'
            storage.write('tokens',{'access_token':'saved-fixture-token'})
            editor.save_button.emit('clicked');phase=14
        elif phase==14 and not editor.busy:
            assert editor.status.get_text().startswith('Saved'),editor.status.get_text()
            storage=MCPTokenStorage(app.credentials,editor.original.secret_key)
            assert storage.read('client')['client_secret']=='registered-fixture-secret'
            assert storage.read('tokens')['access_token']=='saved-fixture-token'
            result['repeated_save_preserves_oauth_client_and_tokens']=True
            editor.auth.set_selected(1);editor.bearer.set_text('fixture-bearer-123456')
            phase=1
        elif phase==1 and capture(editor.window,'mcp-editor.png'):
            editor.connect_button.emit('clicked');phase=2
        elif phase==2 and not editor.busy:
            assert editor.status.get_text().startswith('Server reachable'),editor.status.get_text()
            assert editor.bearer.get_text()==''
            ident=editor.original.id;editor.close()
            app.mcp.close();app.mcp=MCPManager(app.store,app.credentials);dialog.manager=app.mcp;dialog.refresh()
            assert app.mcp.configs()[ident].name=='Local HTTP fixture'
            result['catalog_setup_connect_saved_credentials_and_reopen']=True
            editor=dialog.edit(app.mcp.configs()[ident]);editor.enabled.set_active(False)
            editor.save_button.emit('clicked');phase=3
        elif phase==3 and not editor.busy:
            assert not app.mcp.configs()[editor.original.id].enabled
            result['disable_persisted']=True
            editor.remove_button.emit('clicked');phase=4
        elif phase==4 and editor.closed:
            assert not app.mcp.servers()
            assert not any(value for key,value in app.credentials.keys.items() if not key.startswith('mcp-registration:'))
            result['remove_configuration_and_credentials']=True
            dialog.window.destroy();app.quit();return False
    except Exception as error:
        result['error']=str(error);app.quit();return False
    return True

GLib.timeout_add(120,tick)
try:
    with patch('desktop_buddy.mcp_client.OAuthCallback',side_effect=lambda p,i:OAuthCallback(p,i,authorize)):
        app.run([])
finally:
    app.mcp.close();app.store.close();server.shutdown();server.server_close();profile.cleanup()
(root/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
if 'error' in result:raise SystemExit(1)
