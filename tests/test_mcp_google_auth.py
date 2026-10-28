import asyncio
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from desktop_buddy.mcp_auth import MCPTokenStorage, MCPAuthRequired, persistent_oauth_provider
from desktop_buddy.mcp_catalog import GOOGLE_MCP_ENDPOINTS
from desktop_buddy.mcp_client import MCPConfig, MCPManager, sdk_available
from desktop_buddy.security import Redactor, StopToken
from desktop_buddy.state import Store
from test_mcp import Credentials


@unittest.skipUnless(sdk_available(),'MCP SDK required')
class GoogleAuthorizationTests(unittest.TestCase):
    def setUp(self):
        import httpx2
        self.httpx=httpx2;self.temp=tempfile.TemporaryDirectory()
        self.credentials=Credentials(Redactor());self.storage=MCPTokenStorage(self.credentials,'google-fixture')
        self.scope='https://www.googleapis.com/auth/gmail.readonly'
        self.client={'client_id':'registered-google-fixture','client_secret':'private-fixture-value',
                     'token_endpoint_auth_method':'client_secret_post',
                     'redirect_uris':['http://127.0.0.1:8765/oauth/callback']}
        self.storage.write('client',self.client)
        self.requests=[];self.opened=[];self.grants=[];self.mismatch=False;self.omit_refresh=False;self.reject_refresh=False
        self.metadata={'issuer':'https://accounts.google.com',
                       'authorization_endpoint':'https://accounts.google.com/o/oauth2/v2/auth',
                       'token_endpoint':'https://oauth2.googleapis.com/token',
                       'response_types_supported':['code'],'code_challenge_methods_supported':['S256']}

    def tearDown(self):self.temp.cleanup()

    async def redirect(self,url):self.opened.append(parse_qs(urlsplit(url).query))

    async def callback(self):
        from mcp.shared.auth import AuthorizationCodeResult
        return AuthorizationCodeResult(code='fixture-code',state=self.opened[-1]['state'][0],iss='https://accounts.google.com')

    def handler(self,request):
        self.requests.append(request)
        url=str(request.url)
        if url=='https://accounts.google.com/.well-known/openid-configuration':
            metadata=dict(self.metadata)
            if self.mismatch:metadata['token_endpoint']='https://foreign.invalid/token'
            return self.httpx.Response(200,json=metadata)
        if url=='https://oauth2.googleapis.com/token':
            values=parse_qs(request.content.decode());self.grants.append(values)
            self.assertEqual(values['client_id'],[self.client['client_id']])
            self.assertEqual(values['client_secret'],[self.client['client_secret']])
            if values['grant_type']==['authorization_code']:
                challenge=base64.urlsafe_b64encode(hashlib.sha256(values['code_verifier'][0].encode()).digest()).decode().rstrip('=')
                self.assertEqual(challenge,self.opened[-1]['code_challenge'][0])
            elif self.reject_refresh:return self.httpx.Response(400,json={'error':'invalid_grant'})
            data={'access_token':'fixture-access','token_type':'Bearer','expires_in':3600}
            if not self.omit_refresh:data['refresh_token']='fixture-refresh'
            return self.httpx.Response(200,json=data)
        if url in GOOGLE_MCP_ENDPOINTS.values():
            # Like Google's real endpoint, discovery is public. The test verifies
            # that Buddy has authorized first, not that discovery demands it.
            if request.method!='POST':return self.httpx.Response(405)
            data=json.loads(request.content)
            if 'id' not in data:return self.httpx.Response(202)
            if data['method']=='initialize':result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'Public discovery fixture','version':'1'}}
            elif data['method']=='tools/list':result={'tools':[{'name':'list_labels','inputSchema':{'type':'object'}}]}
            elif data['method']=='ping':result={}
            else:
                self.assertNotEqual(data['method'],'tools/call','Connect must not read account data')
                return self.httpx.Response(200,json={'jsonrpc':'2.0','id':data['id'],'error':{'code':-32601,'message':'Method not found'}})
            return self.httpx.Response(200,json={'jsonrpc':'2.0','id':data['id'],'result':result})
        raise AssertionError('Unexpected destination: '+url)

    def provider(self,storage=None):
        from mcp.shared.auth import OAuthClientMetadata
        return persistent_oauth_provider(GOOGLE_MCP_ENDPOINTS['gmail'],
            OAuthClientMetadata(redirect_uris=self.client['redirect_uris'],scope=self.scope),
            storage or self.storage,redirect_handler=self.redirect,callback_handler=self.callback)

    def test_explicit_sign_in_precedes_public_discovery_and_is_remembered(self):
        store=Store(Path(self.temp.name)/'state',self.credentials.redactor)
        manager=MCPManager(store,self.credentials);test=self
        class Callback:
            uri='http://127.0.0.1:8765/oauth/callback'
            def __init__(self,_port,interactive):self.interactive=interactive
            async def redirect(self,url):
                test.assertTrue(self.interactive);await test.redirect(url)
            async def wait(self):return await test.callback()
            def close(self):pass
        config=MCPConfig('google-drive-fixture','Drive',url=GOOGLE_MCP_ENDPOINTS['drive'],scope=self.scope)
        manager.save(config,client_info=self.client)
        original=self.httpx.AsyncClient
        def client(*args,**kwargs):return original(*args,transport=self.httpx.MockTransport(self.handler),**kwargs)
        try:
            with patch('httpx2.AsyncClient',side_effect=client),patch('desktop_buddy.mcp_client.OAuthCallback',Callback):
                self.assertTrue(manager.tools(config.id,StopToken(),interactive=True))
                self.assertEqual(manager.servers()[0]['status'],'connected')
                self.assertEqual(self.opened[0]['scope'],[self.scope])
                self.assertEqual(self.opened[0]['access_type'],['offline'])
                self.assertEqual(self.opened[0]['prompt'],['consent'])
                self.assertEqual(self.opened[0]['code_challenge_method'],['S256'])
                old=manager.connections[config.id];manager.close();old.thread.join(5)
                storage=MCPTokenStorage(self.credentials,config.secret_key)
                expired=storage.read('tokens');expired['_expires_at']=time.time()-60;storage.write('tokens',expired)
                store.close();store=Store(Path(self.temp.name)/'state',self.credentials.redactor)
                manager=MCPManager(store,Credentials(self.credentials.redactor,self.credentials.keys))
                self.omit_refresh=True
                self.assertTrue(manager.tools(config.id,StopToken()))
                self.assertEqual(len(self.opened),1)
                self.assertEqual(storage.read('tokens')['refresh_token'],'fixture-refresh')
                self.assertEqual([v['grant_type'][0] for v in self.grants],['authorization_code','refresh_token'])
                authorized=[r for r in self.requests if str(r.url)==config.url]
                self.assertTrue(authorized)
                for request in authorized:self.assertEqual(request.headers['Authorization'],'Bearer fixture-access')
        finally:
            connections=list(manager.connections.values());manager.close()
            for c in connections:c.thread.join(5)
            store.close()

    def test_noninteractive_use_cannot_turn_public_discovery_into_account_access(self):
        async def work():
            provider=self.provider()
            async with self.httpx.AsyncClient(transport=self.httpx.MockTransport(self.handler)) as http:
                with self.assertRaises(MCPAuthRequired):await provider.ensure_google_authorization(http,False)
            self.assertFalse(self.requests);self.assertFalse(self.opened)
        asyncio.run(work())

    def test_substituted_metadata_is_rejected_before_browser_or_secret_exchange(self):
        async def work():
            self.mismatch=True;provider=self.provider()
            async with self.httpx.AsyncClient(transport=self.httpx.MockTransport(self.handler)) as http:
                with self.assertRaises(MCPAuthRequired):await provider.ensure_google_authorization(http,True)
            self.assertFalse(self.opened);self.assertFalse(self.grants)
            self.assertEqual(len(self.requests),1)
        asyncio.run(work())

    def test_explicit_reconnect_opens_sign_in_even_with_a_cached_unexpired_token(self):
        async def work():
            async with self.httpx.AsyncClient(transport=self.httpx.MockTransport(self.handler)) as http:
                provider=self.provider()
                await provider.ensure_google_authorization(http,True)
                await self.provider().ensure_google_authorization(http,False)
                self.assertEqual(len(self.opened),1)
                await self.provider().ensure_google_authorization(http,True)
                self.assertEqual(len(self.opened),2)
        asyncio.run(work())

    def test_discovered_scope_alternatives_cannot_broaden_it_permissions(self):
        async def work():
            from mcp.shared.auth import OAuthMetadata
            provider=self.provider();await provider._initialize()
            provider.context.oauth_metadata=OAuthMetadata.model_validate(self.metadata)
            provider.context.client_metadata.scope='https://mail.google.com/'
            await provider._perform_authorization_code_grant()
            self.assertEqual(self.opened[-1]['scope'],[self.scope])
        asyncio.run(work())

    def test_reauthorization_does_not_reuse_a_different_accounts_refresh_token(self):
        async def work():
            self.storage.write('discovery',{'metadata':self.metadata,'auth_server_url':'https://accounts.google.com'})
            self.storage.write('tokens',{'access_token':'old-account-access','refresh_token':'old-account-refresh','token_type':'Bearer','_expires_at':time.time()-60})
            self.omit_refresh=True;self.reject_refresh=True
            async with self.httpx.AsyncClient(transport=self.httpx.MockTransport(self.handler)) as http:
                await self.provider().ensure_google_authorization(http,True)
            self.assertNotIn('refresh_token',self.storage.read('tokens'))
            self.assertEqual(self.storage.read('tokens')['access_token'],'fixture-access')
        asyncio.run(work())
