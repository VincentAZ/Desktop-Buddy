import asyncio
import io
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode, urlsplit, parse_qs
import requests

from desktop_buddy.mcp_auth import MCPTokenStorage, OAuthCallback, MCPAuthRequired, persistent_oauth_provider
from desktop_buddy.mcp_catalog import CATALOG, search_catalog, GOOGLE_MCP_ENDPOINTS, consumer_available, catalog_item, availability_text
from desktop_buddy.mcp_client import MCPConfig, MCPManager, MCPError, sdk_available
from desktop_buddy.security import Redactor, StopToken, Policy, Denied, Cancelled
from desktop_buddy.state import Store
from desktop_buddy.tools import Registry


class Credentials:
    def __init__(self,redactor,keys=None):self.redactor=redactor;self.keys=keys if keys is not None else {}
    def get(self,key):return self.keys.get(key,'')
    def set(self,key,value):self.keys[key]=value


class HTTPFixture(BaseHTTPRequestHandler):
    """Independent JSON-RPC peer exercises actual Streamable HTTP wire handling."""
    calls=[]
    def log_message(self,*_):pass
    def reply(self,status,data=None):
        body=json.dumps(data).encode() if data is not None else b''
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        if data and data.get('result',{}).get('serverInfo'):self.send_header('Mcp-Session-Id','buddy-fixture-session')
        self.end_headers();self.wfile.write(body)
    def do_GET(self):self.reply(405)
    def do_DELETE(self):self.reply(200)
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.calls.append((dict(self.headers),data))
        expected=getattr(self.server,'expected_bearer','fixture-bearer-123456')
        if expected is not None and self.headers.get('Authorization')!='Bearer '+expected:self.reply(401);return
        method=data['method']
        if 'id' not in data:self.reply(202);return
        if method=='initialize':
            result={'protocolVersion':'2025-11-25','capabilities':{'tools':{},'resources':{}},'serverInfo':{'name':'fixture','version':'1'}}
        elif method=='tools/list':
            second=data.get('params',{}).get('cursor')=='second'
            result={'tools':[{'name':'second' if second else 'echo','description':'Fixture tool','inputSchema':{'type':'object','properties':{'message':{'type':'string'}},'required':['message'],'additionalProperties':False}}]}
            if not second:result['nextCursor']='second'
        elif method=='tools/call':result={'content':[{'type':'text','text':data['params']['arguments']['message']}],'isError':False}
        elif method=='resources/list':result={'resources':[{'uri':'fixture://hello','name':'Hello'}]}
        elif method=='resources/read':result={'contents':[{'uri':'fixture://hello','text':'Hello resource'}]}
        elif method=='ping':result={}
        else:
            self.reply(200,{'jsonrpc':'2.0','id':data['id'],'error':{'code':-32601,'message':'Method not found'}});return
        self.reply(200,{'jsonrpc':'2.0','id':data['id'],'result':result})


class OAuthFixture(HTTPFixture):
    def do_GET(self):
        base=f'http://127.0.0.1:{self.server.server_port}'
        if self.path.startswith('/.well-known/oauth-protected-resource'):
            self.reply(200,{'resource':base+'/mcp','authorization_servers':[base]})
        elif self.path.startswith('/.well-known/oauth-authorization-server'):
            self.reply(200,{'issuer':base,'authorization_endpoint':base+'/authorize','token_endpoint':base+'/token',
                'registration_endpoint':base+'/register','response_types_supported':['code'],
                'grant_types_supported':['authorization_code','refresh_token'],'code_challenge_methods_supported':['S256'],
                'token_endpoint_auth_methods_supported':['none']})
        else:super().do_GET()
    def do_POST(self):
        base=f'http://127.0.0.1:{self.server.server_port}'
        if self.path=='/register':
            values=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.reply(201,{**values,'client_id':'buddy-oauth-fixture'});return
        if self.path=='/token':
            values=parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
            self.server.grants.append(values)
            if values['grant_type']==['authorization_code']:
                import hashlib,base64
                challenge=base64.urlsafe_b64encode(hashlib.sha256(values['code_verifier'][0].encode()).digest()).decode().rstrip('=')
                if values.get('code')!=['fixture-code'] or challenge!=self.server.challenge:
                    self.reply(400,{'error':'invalid_grant'});return
            elif values.get('refresh_token')!=['fixture-refresh-token']:
                self.reply(400,{'error':'invalid_grant'});return
            self.reply(200,{'access_token':self.server.expected_bearer,'token_type':'Bearer','expires_in':3600,'refresh_token':'fixture-refresh-token'});return
        if self.headers.get('Authorization')!='Bearer '+self.server.expected_bearer:
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(401);self.send_header('WWW-Authenticate',f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"')
            self.send_header('Content-Length','0');self.end_headers();return
        super().do_POST()


class LateOAuthFixture(OAuthFixture):
    """Public handshake, followed by a tools/list authorization challenge."""
    def do_POST(self):
        if not getattr(self.server,'use_oauth',True):return HTTPFixture.do_POST(self)
        if self.path=='/mcp':
            raw=self.rfile.read(int(self.headers['Content-Length']));self.rfile=io.BytesIO(raw)
            data=json.loads(raw)
            if 'id' not in data:self.reply(202);return
            if data['method']=='server/discover':
                self.reply(200,{'jsonrpc':'2.0','id':data['id'],'error':{'code':-32601,'message':'Method not found'}});return
            if data['method']=='initialize':
                self.reply(200,{'jsonrpc':'2.0','id':data['id'],'result':{
                    'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'Public handshake fixture','version':'1'}}});return
        return super().do_POST()


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.redactor=Redactor();self.store=Store(self.root/'state',self.redactor)
        self.credentials=Credentials(self.redactor);self.manager=MCPManager(self.store,self.credentials);self.token=StopToken()
    def tearDown(self):
        connections=list(self.manager.connections.values());self.manager.close()
        for connection in connections:connection.thread.join(5)
        self.store.close();self.temp.cleanup()
    def stdio(self):
        return MCPConfig('fixture','Fixture',transport='stdio',auth='none',
            command=[sys.executable,str(Path(__file__).with_name('mcp_fixture.py'))])
    def registry(self,mode='execute',offline=False,approve=lambda *_:True,browser=None):
        return Registry(Policy(self.root,mode,offline,approve),self.store,browser=browser,mcp=self.manager)

    def test_catalog_common_services_and_custom_configuration(self):
        for query in ('gmail','calendar','vercel','github','slack','notion','linear','figma'):
            self.assertTrue(search_catalog(query),query)
        self.assertEqual(len({i['id'] for i in CATALOG}),len(CATALOG))
        self.manager.save(self.stdio());self.assertEqual(self.manager.servers()[0]['id'],'fixture')

    def test_official_google_services_have_separate_endpoints_without_claiming_readiness(self):
        for service,url in GOOGLE_MCP_ENDPOINTS.items():
            item=next(i for i in CATALOG if i['id']=='google-'+service)
            self.assertEqual(item['url'],url)
            self.assertEqual(item['auth'],'oauth')
            self.assertEqual(item['publisher'],'Official')
            self.assertFalse(consumer_available(item))
        # Existing community configurations remain recognizable and unchanged.
        legacy=next(i for i in CATALOG if i['id']=='google-workspace')
        self.assertEqual(legacy['command'],['workspace-mcp'])

    @unittest.skipUnless(sdk_available(),'MCP SDK required')
    def test_google_authorization_requests_refresh_token_without_changing_pkce_or_state(self):
        from mcp.shared.auth import OAuthClientMetadata
        opened=[]
        async def capture(url):opened.append(url)
        async def work():
            params={'state':'opaque+state','code_challenge':'challenge/with+symbols',
                    'code_challenge_method':'S256','redirect_uri':'http://127.0.0.1:8765/oauth/callback',
                    'client_id':'registered-client','scope':'openid email','access_type':'online','prompt':'none'}
            url='https://accounts.google.com/o/oauth2/v2/auth?'+urlencode(params)
            for endpoint in GOOGLE_MCP_ENDPOINTS.values():
                provider=persistent_oauth_provider(endpoint,OAuthClientMetadata(redirect_uris=[params['redirect_uri']]),
                    MCPTokenStorage(self.credentials,'test-google'),redirect_handler=capture)
                await provider.context.redirect_handler(url)
                actual=parse_qs(urlsplit(opened[-1]).query)
                self.assertEqual(actual,{k:[v] for k,v in {**params,'access_type':'offline','prompt':'consent'}.items()})
                foreign=url.replace('accounts.google.com','accounts.google.com.evil.example')
                await provider.context.redirect_handler(foreign)
                self.assertEqual(opened[-1],foreign)
            other=persistent_oauth_provider('https://another.example/mcp',
                OAuthClientMetadata(redirect_uris=[params['redirect_uri']]),
                MCPTokenStorage(self.credentials,'test-other'),redirect_handler=capture)
            await other.context.redirect_handler(url)
            self.assertEqual(opened[-1],url)
        asyncio.run(work())

    def test_config_rejects_insecure_or_embedded_credentials(self):
        for url in ('http://example.com/mcp','https://user:secret@example.com/mcp','https://example.com/?access_%74oken=x','https://example.com/#secret'):
            with self.subTest(url=url),self.assertRaises(ValueError):MCPConfig('x','X',url=url).validate()
        MCPConfig('x','X',url='http://127.0.0.1:12345/mcp').validate()
        with self.assertRaises(ValueError):replace(self.stdio(),auth='oauth').validate()
        with self.assertRaises(ValueError):
            self.manager.save(replace(self.stdio(),command=['example','secret-in-command-123']),{'env':{'TOKEN':'secret-in-command-123'}})
        self.assertFalse(self.manager.configs());self.assertFalse(self.credentials.keys)

    def test_configuration_persists_but_secrets_are_not_in_sqlite(self):
        c=MCPConfig('x','Saved server',url='https://example.com/mcp',auth='bearer')
        self.manager.save(c,{'bearer':'fixture-private-secret-12345','headers':{'X-Token':'header-private-secret-56789'}})
        self.manager.close();self.store.close();self.store=Store(self.root/'state',self.redactor)
        self.manager=MCPManager(self.store,self.credentials)
        self.assertEqual(self.manager.configs()['x'],c)
        self.assertEqual(MCPTokenStorage(self.credentials,c.secret_key).read('manual')['bearer'],'fixture-private-secret-12345')
        for file in self.store.root.glob('*.sqlite3*'):
            self.assertNotIn(b'fixture-private-secret-12345',file.read_bytes())
            self.assertNotIn(b'header-private-secret-56789',file.read_bytes())
        self.manager.remove('x');self.assertFalse(self.manager.servers());self.assertFalse(any(self.credentials.keys.values()))

    def test_changed_endpoint_cannot_reuse_credentials(self):
        c=MCPConfig('x','Saved',url='https://example.com/mcp',auth='bearer')
        self.manager.save(c,{'bearer':'fixture-private-secret-12345'})
        changed=replace(c,url='https://another.example/mcp');self.manager.save(changed)
        self.assertNotEqual(changed.secret_key,c.secret_key)
        self.assertIsNone(MCPTokenStorage(self.credentials,changed.secret_key).read('manual'))
        self.assertIsNone(MCPTokenStorage(self.credentials,c.secret_key).read('manual'))

    def test_host_policy_blocks_offline_plan_and_rejected_calls_before_connection(self):
        self.manager._get=Mock(side_effect=AssertionError('Must not connect'))
        call={'server':'fixture','name':'echo','arguments':{'message':'x'}}
        for registry,name,args in [(self.registry(mode='plan'),'mcp.call',call),(self.registry(offline=True),'mcp.tools',{'server':'fixture'}),(self.registry(approve=lambda *_:False),'mcp.call',call)]:
            with self.assertRaises(Denied):registry.execute(name,args,self.token)
        self.manager._get.assert_not_called()

    def test_browser_is_refused_only_for_websites_of_connected_apps(self):
        browser=Mock();r=self.registry(browser=browser)
        browser.navigate.return_value={'url':'https://mail.google.com/'}
        # Nothing connected: the browser is the only route, so catalog websites stay open.
        for host in ('mail.google.com','calendar.google.com','vercel.com','github.com','mcp.notion.com'):
            browser.navigate.return_value={'url':'https://'+host+'/'}
            r.execute('browser.navigate',{'url':'https://'+host},self.token)
        self.manager.save(replace(self.stdio(),domains=['custom.example']))
        with self.assertRaises(Denied):self.manager.require_browser_fallback('https://app.custom.example/')
        self.manager.require_browser_fallback('https://custom.example.evil.test/')
        # A connected catalog app reserves its website; turning it off releases it.
        notion=next(i for i in CATALOG if i['id']=='notion')
        self.manager.save(MCPConfig('notion-saved','Notion',url=notion['url']))
        with self.assertRaises(Denied):self.manager.require_browser_fallback('https://www.notion.so/page')
        with self.assertRaises(Denied):self.manager.require_browser_fallback(notion['url'])
        self.manager.save(MCPConfig('notion-saved','Notion',url=notion['url'],enabled=False))
        self.manager.require_browser_fallback('https://www.notion.so/page')
        browser.navigate.return_value={'url':'https://example.com'}
        self.assertEqual(r.execute('browser.navigate',{'url':'https://example.com'},self.token)['url'],'https://example.com')

    def test_catalog_describes_how_each_app_connects(self):
        kinds={i['sign_in'] for i in CATALOG}
        self.assertEqual(kinds,{'dcr','none','token','client','install'})
        for item in CATALOG:
            with self.subTest(service=item['id']):
                if item['sign_in']=='client':
                    self.assertFalse(consumer_available(item),'needs a publisher client')
                    self.assertTrue(availability_text(item))
                elif item['sign_in'] in ('dcr','none','token'):
                    self.assertTrue(consumer_available(item));self.assertEqual(availability_text(item),'')
                if item['sign_in']=='token':self.assertEqual(item['auth'],'bearer');self.assertTrue(item['token_url'])
                if item['sign_in']=='none':self.assertEqual(item['auth'],'none')
        self.assertTrue(consumer_available(next(i for i in CATALOG if i['id']=='vercel')))
        self.assertFalse(consumer_available(next(i for i in CATALOG if i['id']=='figma')))

    def test_shipped_publisher_client_makes_google_connectable_without_keyring(self):
        from desktop_buddy import publisher
        gmail=next(i for i in CATALOG if i['id']=='google-gmail')
        with patch.dict(publisher.CLIENTS,{},clear=True):
            self.assertFalse(consumer_available(gmail,self.manager.registrations))
        with patch.dict(publisher.CLIENTS,{'google':{'client_id':'shipped-client','client_secret':'shipped-private-secret'}}):
            with patch.object(self.credentials,'get',side_effect=AssertionError('Listing must not read the keyring')):
                self.assertTrue(consumer_available(gmail,self.manager.registrations))
                self.assertEqual(self.manager.registrations.source(gmail),'shipped')
            config=MCPConfig('google-gmail-x','Gmail',url=gmail['url'])
            prepared=self.manager.registrations.prepare(config,explicit=True)
            self.assertEqual(prepared.scope,gmail['scope'])
            self.assertEqual(MCPTokenStorage(self.credentials,prepared.secret_key).read('client')['client_id'],'shipped-client')
            self.assertNotIn('shipped-private-secret',' '.join(r[0] for r in self.store.db.execute('SELECT value FROM settings')))
        with patch.dict(publisher.CLIENTS,{},clear=True):
            self.assertFalse(consumer_available(gmail,self.manager.registrations))
        # The release ships a real Google client, so Gmail is connectable out of the box.
        self.assertTrue(consumer_available(gmail,self.manager.registrations))
        self.assertEqual(self.manager.registrations.source(gmail),'shipped')

    def test_it_pasted_client_covers_every_google_service_and_can_be_forgotten(self):
        registrations=self.manager.registrations
        services=registrations.install_client('google','pasted-client','pasted-private-secret')
        self.assertEqual(set(services),{'google-'+s for s in GOOGLE_MCP_ENDPOINTS})
        for service in services:
            item=next(i for i in CATALOG if i['id']==service)
            self.assertTrue(consumer_available(item,registrations));self.assertEqual(registrations.source(item),'installation')
        self.assertIn('[REDACTED]',self.redactor.text('pasted-private-secret'))
        with self.assertRaises(ValueError):registrations.install_client('google','')
        with self.assertRaises(ValueError):registrations.install_client('notion','x')
        registrations.forget('google')
        self.assertFalse(registrations.records())
        self.assertFalse(any(self.credentials.keys.values()))

    def test_probe_server_detects_sign_in_and_public_servers(self):
        from desktop_buddy.mcp_client import probe_server,MCPError
        server=ThreadingHTTPServer(('127.0.0.1',0),HTTPFixture);server.expected_bearer='secret-bearer-value'
        threading.Thread(target=server.serve_forever,daemon=True).start()
        public=ThreadingHTTPServer(('127.0.0.1',0),HTTPFixture);public.expected_bearer=None
        threading.Thread(target=public.serve_forever,daemon=True).start()
        try:
            self.assertEqual(probe_server(f'http://127.0.0.1:{server.server_port}/mcp'),'oauth')
            self.assertEqual(probe_server(f'http://127.0.0.1:{public.server_port}/mcp'),'none')
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));closed=sock.getsockname()[1]
            with self.assertRaises(MCPError):probe_server(f'http://127.0.0.1:{closed}/mcp')
        finally:
            for s in (server,public):s.shutdown();s.server_close()

    def test_new_atlassian_preset_keeps_existing_v1_connections_at_their_saved_url(self):
        item=next(i for i in CATALOG if i['id']=='atlassian')
        self.assertEqual(item['url'],'https://mcp.atlassian.com/v2/mcp')
        old=MCPConfig('atlassian-saved','Atlassian',url='https://mcp.atlassian.com/v1/mcp')
        self.manager.save(old)
        recognized=catalog_item(self.manager.configs()[old.id])
        self.assertEqual(recognized['url'],old.url)
        self.assertTrue(consumer_available(recognized))
        self.assertNotEqual(recognized,item)
        with self.assertRaises(Denied):self.manager.require_browser_fallback(old.url)
        self.assertIsNone(catalog_item(replace(old,url=old.url+'/other')))

    def test_custom_endpoint_is_reserved_after_restart(self):
        config=MCPConfig('custom','Private app',url='https://apps.example:8443/mcp',auth='none')
        self.manager.save(config)
        # New manager reads persisted settings; this never connects or reads keys.
        reopened=MCPManager(self.store,self.credentials)
        try:
            for url in ('https://apps.example:8443/', 'https://APPS.EXAMPLE.:8443/other',
                        'http://apps.example:8443/'):
                with self.assertRaises(Denied):reopened.require_browser_fallback(url)
            reopened.require_browser_fallback('https://apps.example:9443/')
            reopened.require_browser_fallback('https://apps.example.evil.test:8443/')
        finally:reopened.close()

    def test_loopback_endpoint_aliases_cannot_bypass_mcp_routing(self):
        self.manager.save(MCPConfig('local','Local app',url='http://127.0.0.1:8123/mcp',auth='none'))
        for host in ('127.0.0.1','localhost','test.localhost','[::1]','[::ffff:127.0.0.1]'):
            with self.subTest(host=host):
                with self.assertRaises(Denied):self.manager.require_browser_fallback('http://'+host+':8123/page')
                self.manager.require_browser_fallback('http://'+host+':8124/page')

    def test_unicode_and_ascii_endpoint_spellings_match(self):
        self.manager.save(MCPConfig('international','International app',url='https://bücher.example/mcp',auth='none'))
        for host in ('bücher.example','xn--bcher-kva.example','XN--BCHER-KVA.EXAMPLE.'):
            with self.assertRaises(Denied):self.manager.require_browser_fallback('https://'+host+'/')
        self.manager.require_browser_fallback('https://bücher.example.evil.test/')

    @unittest.skipUnless(sdk_available(),'MCP SDK required')
    def test_public_discovery_does_not_report_an_oauth_account_connected(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),HTTPFixture);server.expected_bearer=None
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            self.manager.save(MCPConfig('public','Public discovery',url=f'http://127.0.0.1:{server.server_port}/mcp'))
            with self.assertRaisesRegex(MCPError,'Public tool discovery'):
                self.manager.tools('public',self.token,interactive=True)
            self.assertNotEqual(self.manager.servers()[0]['status'],'connected')
        finally:server.shutdown();server.server_close()

    @unittest.skipUnless(sdk_available(),'MCP SDK required')
    def test_sign_in_can_begin_after_public_initialization(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),LateOAuthFixture)
        server.expected_bearer='late-fixture-access';server.grants=[]
        threading.Thread(target=server.serve_forever,daemon=True).start()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        opened=[]
        def authorize(url):
            values=parse_qs(urlsplit(url).query);opened.append(values)
            server.challenge=values['code_challenge'][0]
            self.assertEqual(values['scope'],['fixture.read'])
            requests.get(values['redirect_uri'][0]+'?'+urlencode({'code':'fixture-code','state':values['state'][0]}),timeout=3)
            return True
        try:
            self.manager.save(MCPConfig('late','Late sign-in',url=f'http://127.0.0.1:{server.server_port}/mcp',callback_port=port,scope='fixture.read'))
            with patch('desktop_buddy.mcp_client.OAuthCallback',side_effect=lambda p,i:OAuthCallback(p,i,authorize)):
                self.assertTrue(self.manager.tools('late',self.token,interactive=True))
            self.assertEqual(len(opened),1)
            self.assertEqual(self.manager.servers()[0]['status'],'connected')
        finally:
            connections=list(self.manager.connections.values());self.manager.close()
            for c in connections:c.thread.join(5)
            server.shutdown();server.server_close()

    @unittest.skipUnless(sdk_available(),'Run scripts/setup-mcp.sh for real MCP integration tests')
    def test_real_stdio_discovery_call_resource_and_reconnection(self):
        self.manager.save(self.stdio())
        names={t['name'] for t in self.manager.tools('fixture',self.token)}
        self.assertIn('echo',names)
        result=self.registry().execute('mcp.call',{'server':'fixture','name':'echo','arguments':{'message':'hello MCP'}},self.token)
        self.assertEqual(result['content'][0]['text'],'hello MCP')
        self.assertEqual(self.manager.resources('fixture',self.token)['resources'][0]['uri'],'fixture://hello')
        self.assertEqual(self.manager.read_resource('fixture','fixture://hello',self.token)['contents'][0]['text'],'Hello resource')
        connection=self.manager.connections['fixture'];self.manager.close();connection.thread.join(5)
        self.assertFalse(connection.thread.is_alive())
        self.store.close();self.store=Store(self.root/'state',self.redactor);self.manager=MCPManager(self.store,self.credentials)
        self.assertEqual(self.manager.call('fixture','echo',{'message':'after restart'},self.token)['content'][0]['text'],'after restart')

    @unittest.skipUnless(sdk_available(),'MCP SDK required')
    def test_stdio_environment_isolated_and_secrets_redacted(self):
        self.manager.save(self.stdio(),{'env':{'BUDDY_TEST_SECRET':'secret-fixture-value-123456'}})
        with patch.dict('os.environ',{'BUDDY_PARENT_SECRET':'ambient-credential-must-not-leak'}):
            result=self.manager.call('fixture','environment',{},self.token)
        self.assertEqual(result['content'][0]['text'],'[REDACTED]|absent')

    @unittest.skipUnless(sdk_available(),'MCP SDK required')
    def test_http_transport_pagination_persistent_auth_schema_and_session(self):
        HTTPFixture.calls=[];server=ThreadingHTTPServer(('127.0.0.1',0),HTTPFixture)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            c=MCPConfig('http','HTTP fixture',url=f'http://127.0.0.1:{server.server_port}/mcp',auth='bearer')
            self.manager.save(c,{'bearer':'fixture-bearer-123456'})
            self.assertEqual([v['name'] for v in self.manager.tools('http',self.token)],['echo','second'])
            self.assertEqual(self.manager.call('http','echo',{'message':'over HTTP'},self.token)['content'][0]['text'],'over HTTP')
            before=len([d for _,d in HTTPFixture.calls if d['method']=='tools/call'])
            with self.assertRaises(Exception):self.manager.call('http','echo',{'message':42},self.token)
            self.assertEqual(len([d for _,d in HTTPFixture.calls if d['method']=='tools/call']),before)
            self.assertTrue(any(h.get('mcp-session-id')=='buddy-fixture-session' for h,_ in HTTPFixture.calls))
            self.manager.close();self.manager=MCPManager(self.store,Credentials(Redactor(),self.credentials.keys))
            self.assertEqual(self.manager.call('http','echo',{'message':'reconnected'},self.token)['content'][0]['text'],'reconnected')
        finally:
            connections=list(self.manager.connections.values());self.manager.close()
            for c in connections:c.thread.join(5)
            server.shutdown();server.server_close()

    @unittest.skipUnless(sdk_available(),'MCP SDK required')
    def test_cancellation_stops_session_without_replaying_call(self):
        self.manager.save(self.stdio());self.manager.tools('fixture',self.token)
        errors=[]
        def work():
            try:self.manager.call('fixture','slow',{},self.token)
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=work);thread.start();time.sleep(.3);self.token.stop();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertTrue(errors)
        connection=self.manager.connections['fixture'];connection.thread.join(5)
        self.assertFalse(connection.thread.is_alive())
        self.assertIsInstance(errors[0],Cancelled)

    @unittest.skipUnless(sdk_available(),'MCP SDK required')
    def test_disabled_servers_do_not_launch(self):
        self.manager.save(replace(self.stdio(),enabled=False))
        with self.assertRaises(MCPError):self.manager.tools('fixture',self.token)
        self.assertFalse(self.manager.connections)

    @unittest.skipUnless(sdk_available(),'MCP SDK required')
    def test_oauth_persistence_refresh_rotation_and_redaction(self):
        from mcp.shared.auth import OAuthToken, OAuthClientInformationFull
        storage=MCPTokenStorage(self.credentials,'test-oauth')
        async def work():
            await storage.set_tokens(OAuthToken(access_token='access-fixture-123',refresh_token='refresh-fixture-456',token_type='Bearer',expires_in=3600))
            await storage.set_client_info(OAuthClientInformationFull(client_id='fixture',client_secret='client-secret-fixture'))
            reopened=MCPTokenStorage(Credentials(Redactor(),self.credentials.keys),'test-oauth')
            self.assertEqual((await reopened.get_tokens()).refresh_token,'refresh-fixture-456')
            self.assertEqual((await reopened.get_client_info()).client_id,'fixture')
            self.assertEqual(reopened.credentials.redactor.text('access-fixture-123 refresh-fixture-456'),'[REDACTED] [REDACTED]')
            await reopened.set_tokens(OAuthToken(access_token='new-access-fixture',token_type='Bearer',expires_in=1800))
            saved=await reopened.get_tokens();self.assertEqual(saved.refresh_token,'refresh-fixture-456');self.assertLessEqual(saved.expires_in,1800)
        asyncio.run(work())

    @unittest.skipUnless(sdk_available(),'MCP SDK required')
    def test_oauth_callback_state_check_and_noninteractive_reauth(self):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        callback=OAuthCallback(port,True,lambda _:True)
        async def work():
            await callback.redirect('https://auth.example/authorize?state=fixture-state')
            bad=await asyncio.to_thread(requests.get,callback.uri+'?'+urlencode({'state':'wrong','code':'bad'}),timeout=2)
            self.assertEqual(bad.status_code,400);self.assertIsNone(callback.result)
            good=await asyncio.to_thread(requests.get,callback.uri+'?'+urlencode({'state':'fixture-state','code':'authorized-code'}),timeout=2)
            self.assertEqual(good.status_code,200);self.assertEqual((await callback.wait()).code,'authorized-code')
            callback.interactive=False
            with self.assertRaises(MCPAuthRequired):await callback.redirect('https://auth.example/authorize?state=fixture-state')
        try:asyncio.run(work())
        finally:callback.close()

    @unittest.skipUnless(sdk_available(),'MCP SDK required')
    def test_full_oauth_pkce_login_and_refresh_after_restart(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),OAuthFixture)
        server.expected_bearer='fixture-access-token-first';server.grants=[]
        threading.Thread(target=server.serve_forever,daemon=True).start()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        opened=[]
        def authorize(url):
            params=parse_qs(urlsplit(url).query);opened.append(params)
            server.challenge=params['code_challenge'][0]
            self.assertEqual(params['code_challenge_method'],['S256'])
            requests.get(params['redirect_uri'][0]+'?'+urlencode({'code':'fixture-code','state':params['state'][0]}),timeout=3)
            return True
        try:
            config=MCPConfig('oauth','OAuth fixture',url=f'http://127.0.0.1:{server.server_port}/mcp',callback_port=port)
            self.manager.save(config)
            with patch('desktop_buddy.mcp_client.OAuthCallback',side_effect=lambda port,interactive:OAuthCallback(port,interactive,authorize)):
                self.assertTrue(self.manager.tools('oauth',self.token,interactive=True))
                connection=self.manager.connections['oauth'];self.manager.close();connection.thread.join(5)
                storage=MCPTokenStorage(self.credentials,config.secret_key)
                expired=storage.read('tokens');expired['_expires_at']=time.time()-60;storage.write('tokens',expired)
                self.store.close();self.store=Store(self.root/'state',self.redactor)
                self.manager=MCPManager(self.store,Credentials(self.redactor,self.credentials.keys))
                server.expected_bearer='fixture-access-token-refreshed'
                result=self.manager.call('oauth','echo',{'message':'still connected'},self.token)
                self.assertEqual(result['content'][0]['text'],'still connected')
            self.assertEqual(len(opened),1)
            self.assertEqual([g['grant_type'][0] for g in server.grants],['authorization_code','refresh_token'])
        finally:
            connections=list(self.manager.connections.values());self.manager.close()
            for c in connections:c.thread.join(5)
            server.shutdown();server.server_close()
