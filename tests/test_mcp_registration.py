from dataclasses import replace
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, urlencode
import requests

from desktop_buddy.mcp_auth import MCPTokenStorage, OAuthCallback
from desktop_buddy.mcp_catalog import CATALOG, catalog_item, consumer_available
from desktop_buddy.mcp_client import MCPManager, MCPConfig, sdk_available
from desktop_buddy.security import Redactor, StopToken
from desktop_buddy.state import Store
from test_mcp import Credentials, OAuthFixture


@unittest.skipUnless(sdk_available(),'MCP SDK required')
class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.redactor=Redactor();self.store=Store(self.root,self.redactor)
        self.credentials=Credentials(self.redactor);self.manager=MCPManager(self.store,self.credentials)
        self.item=next(i for i in CATALOG if i['id']=='google-gmail')
        self.config=MCPConfig('google-gmail-example','Gmail',url=self.item['url'])

    def tearDown(self):
        connections=list(self.manager.connections.values());self.manager.close()
        for c in connections:c.thread.join(5)
        self.store.close();self.temp.cleanup()

    def bundle(self,client='publisher-client',port=8765):
        return {'version':1,'registrations':[{'service':'google-gmail','callback_port':port,
                'scope':'https://www.googleapis.com/auth/gmail.readonly',
                'client':{'client_id':client,'client_secret':'publisher-private-value',
                    'token_endpoint_auth_method':'client_secret_post',
                    'redirect_uris':[f'http://127.0.0.1:{port}/oauth/callback']}}]}

    def test_import_and_availability_never_put_client_credentials_in_sqlite_or_read_keyring(self):
        file=self.root/'publisher.json';file.write_text(json.dumps(self.bundle()))
        self.manager.registrations.import_file(file)
        with patch.object(self.credentials,'get',side_effect=AssertionError('Must not read keyring')):
            self.assertTrue(consumer_available(self.item,self.manager.registrations))
        settings=[r[0] for r in self.store.db.execute('SELECT value FROM settings')]
        self.assertNotIn('publisher-client',' '.join(settings))
        self.assertNotIn('publisher-private-value',' '.join(settings))
        self.assertIn('[REDACTED]',self.redactor.text('publisher-private-value'))

    def test_bad_bundle_changes_nothing_and_does_not_echo_secret(self):
        for mutate in (lambda r:r['client'].update(redirect_uris=['https://foreign.example/callback']),
                       lambda r:r.update(service='google-workspace'),
                       lambda r:r.update(callback_port=True),
                       lambda r:r['client'].update(client_id={'secret':'publisher-private-value'}),
                       lambda r:r['client'].update(token_endpoint_auth_method='private_key_jwt'),
                       lambda r:r['client'].update(registration_access_token='hidden-token')):
            bundle=self.bundle();mutate(bundle['registrations'][0])
            with self.assertRaises(ValueError) as failure:self.manager.registrations.install(bundle)
            self.assertNotIn('publisher-private-value',str(failure.exception))
            self.assertFalse(self.credentials.keys);self.assertFalse(self.manager.registrations.records())
        with patch.object(self.credentials,'set',side_effect=RuntimeError('keyring unavailable')):
            with self.assertRaises(RuntimeError):self.manager.registrations.install(self.bundle())
        self.assertFalse(self.manager.registrations.records())

    def test_modified_presets_never_inherit_registration(self):
        self.manager.registrations.install(self.bundle())
        for config in (replace(self.config,url='https://attacker.example/mcp'),
                       replace(self.config,url=self.item['url']+'?account=changed'),
                       replace(self.config,auth='bearer'),
                       replace(self.config,transport='stdio',command=['some-command'],auth='none')):
            self.assertIsNone(catalog_item(config))
            with patch.object(self.credentials,'get',side_effect=AssertionError('No product secrets')):
                self.assertEqual(self.manager.registrations.prepare(config,explicit=True),config)
        self.assertFalse(self.manager.configs())

    def test_new_endpoint_registration_does_not_transfer_to_a_legacy_endpoint(self):
        bundle=self.bundle();bundle['registrations'][0]['service']='atlassian'
        bundle['registrations'][0]['scope']='read'
        self.manager.registrations.install(bundle)
        old=MCPConfig('atlassian-old','Atlassian',url='https://mcp.atlassian.com/v1/mcp')
        self.manager.save(old)
        self.assertFalse(self.manager.registrations.available(catalog_item(old)))
        with patch.object(self.credentials,'get',side_effect=AssertionError('Do not read a different endpoint registration')):
            self.assertEqual(self.manager.registrations.prepare(old,explicit=True),old)

    def test_restart_preserves_tokens_rotation_updates_only_bound_connections(self):
        self.manager.registrations.install(self.bundle())
        config=self.manager.registrations.prepare(self.config,explicit=True)
        storage=MCPTokenStorage(self.credentials,config.secret_key)
        storage.write('tokens',{'access_token':'existing-token','refresh_token':'existing-refresh'})
        self.store.close();self.store=Store(self.root,self.redactor)
        self.manager=MCPManager(self.store,self.credentials)
        self.assertEqual(self.manager.registrations.prepare(config),config)
        self.assertEqual(storage.read('tokens')['refresh_token'],'existing-refresh')
        live=Mock();self.manager.connections[config.id]=live
        self.manager.registrations.install(self.bundle('replacement-client'))
        live.close.assert_called_once()
        config=self.manager.registrations.prepare(config)
        self.assertEqual(storage.read('client')['client_id'],'replacement-client')
        self.assertIsNone(storage.read('tokens'))
        changed=replace(config,url='https://custom.example/mcp')
        self.manager.save(changed)
        self.manager.registrations.install(self.bundle('third-client'))
        with patch.object(self.credentials,'get',side_effect=AssertionError('No product secrets')):
            self.assertEqual(self.manager.registrations.prepare(changed),changed)

    def test_real_oauth_uses_supplied_client_and_refreshes_after_restart(self):
        class RegisteredPeer(OAuthFixture):
            def do_POST(peer):
                if peer.path=='/register':
                    peer.reply(400,{'error':'dynamic registration must not be called'});return
                super().do_POST()
        server=ThreadingHTTPServer(('127.0.0.1',0),RegisteredPeer)
        server.expected_bearer='registered-access-token';server.grants=[]
        threading.Thread(target=server.serve_forever,daemon=True).start()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        opened=[]
        def authorize(url):
            values=parse_qs(urlsplit(url).query);opened.append(values)
            server.challenge=values['code_challenge'][0]
            self.assertEqual(values['client_id'],['publisher-client'])
            self.assertEqual(values['code_challenge_method'],['S256'])
            requests.get(values['redirect_uri'][0]+'?'+urlencode({'code':'fixture-code','state':values['state'][0]}),timeout=3)
            return True
        try:
            # Replace only this test's catalog entry with a controlled HTTP peer.
            with patch.dict(self.item,url=f'http://127.0.0.1:{server.server_port}/mcp'), patch(
                    'desktop_buddy.mcp_client.OAuthCallback',side_effect=lambda p,i:OAuthCallback(p,i,authorize)):
                self.manager.registrations.install(self.bundle(port=port))
                config=self.manager.registrations.prepare(replace(self.config,url=self.item['url']),explicit=True)
                token=StopToken()
                self.assertTrue(self.manager.tools(config.id,token,interactive=True))
                old=self.manager.connections[config.id];self.manager.close();old.thread.join(5)
                storage=MCPTokenStorage(self.credentials,config.secret_key)
                expired=storage.read('tokens');expired['_expires_at']=time.time()-60;storage.write('tokens',expired)
                self.store.close();self.store=Store(self.root,self.redactor)
                self.manager=MCPManager(self.store,Credentials(self.redactor,self.credentials.keys))
                result=self.manager.call(config.id,'echo',{'message':'remembered'},token)
                self.assertEqual(result['content'][0]['text'],'remembered')
                self.assertEqual(len(opened),1)
                self.assertEqual([g['grant_type'][0] for g in server.grants],['authorization_code','refresh_token'])
                for grant in server.grants:
                    self.assertEqual(grant['client_id'],['publisher-client'])
                    self.assertEqual(grant['client_secret'],['publisher-private-value'])
        finally:server.shutdown();server.server_close()
