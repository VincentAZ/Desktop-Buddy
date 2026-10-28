"""Keyring-backed MCP OAuth storage and a temporary loopback callback listener."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit
import webbrowser


class MCPAuthRequired(RuntimeError):
    pass


class MCPTokenStorage:
    def __init__(self, credentials, key):
        self.credentials=credentials;self.key=key

    def read(self, kind):
        raw=self.credentials.get(self.key+':'+kind)
        value=json.loads(raw) if raw else None
        if value:self.register(value)
        return value

    def register(self,value):
        for field in ('access_token','refresh_token','client_secret','bearer'):
            self.credentials.redactor.register(value.get(field))
        for field in ('env','headers'):
            for secret in value.get(field,{}).values():
                self.credentials.redactor.register(secret)

    def write(self, kind, value):
        # Register individual secrets too; SDK errors must not reveal tokens.
        self.register(value)
        self.credentials.set(self.key+':'+kind,json.dumps(value))

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        value=self.read('tokens')
        if not value:return None
        expires=value.pop('_expires_at',None)
        if expires is not None:value['expires_in']=max(0,int(expires-time.time()))
        return OAuthToken.model_validate(value)

    async def set_tokens(self, tokens):
        value=tokens.model_dump(mode='json',exclude_none=True)
        # Some providers omit a refresh token when rotating only the access token.
        if not value.get('refresh_token'):
            previous=self.read('tokens') or {}
            if previous.get('refresh_token'):value['refresh_token']=previous['refresh_token']
        if tokens.expires_in is not None:value['_expires_at']=time.time()+tokens.expires_in
        self.write('tokens',value)

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        value=self.read('client')
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, client_info):
        self.write('client',client_info.model_dump(mode='json',exclude_none=True))


class OAuthCallback:
    def __init__(self, port, interactive, open_url=webbrowser.open):
        self.port=port;self.interactive=interactive;self.open_url=open_url
        self.server=None;self.thread=None;self.result=None;self.expected_state=None

    @property
    def uri(self):return f'http://127.0.0.1:{self.port}/oauth/callback'

    async def redirect(self, url):
        if not self.interactive:
            raise MCPAuthRequired('Sign in from Connect apps before using this connection.')
        parsed=urlsplit(url)
        if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname=='127.0.0.1'):
            raise MCPAuthRequired('OAuth authorization must use HTTPS or loopback HTTP.')
        self.expected_state=parse_qs(parsed.query).get('state',[''])[0]
        if not self.expected_state:raise MCPAuthRequired('OAuth request has no state.')
        self.result=None
        if self.server is None:
            callback=self
            class Handler(BaseHTTPRequestHandler):
                def setup(self):
                    super().setup();self.connection.settimeout(5)
                def log_message(self,*_):pass
                def do_GET(self):
                    url=urlsplit(self.path);values=parse_qs(url.query)
                    state=values.get('state',[''])[0]
                    if url.path!='/oauth/callback' or not secrets.compare_digest(state,callback.expected_state or ''):
                        self.send_error(400,'Invalid OAuth callback');return
                    callback.result=values
                    body=b'Authorization received. You can close this tab and return to Desktop Buddy.'
                    self.send_response(200);self.send_header('Content-Type','text/plain');self.send_header('Content-Length',str(len(body)))
                    self.end_headers();self.wfile.write(body)
            self.server=ThreadingHTTPServer(('127.0.0.1',self.port),Handler)
            self.server.timeout=.2
            self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
            self.thread.start()
        if not await asyncio.to_thread(self.open_url,url):
            raise MCPAuthRequired('Could not open the system browser for sign-in.')

    async def wait(self):
        from mcp.shared.auth import AuthorizationCodeResult
        deadline=time.monotonic()+180
        while self.result is None:
            if time.monotonic()>deadline:raise MCPAuthRequired('Sign-in timed out. Connect again to retry.')
            await asyncio.sleep(.05)
        if self.result.get('error') or not self.result.get('code'):
            raise MCPAuthRequired('Sign-in was declined or did not return an authorization code.')
        return AuthorizationCodeResult(code=self.result['code'][0],state=self.result['state'][0],iss=self.result.get('iss',[None])[0])

    def close(self):
        if self.server:
            self.server.shutdown();self.server.server_close();self.server=None
            self.thread.join(2)


def persistent_oauth_provider(server_url,metadata,storage,**handlers):
    """Restore expiry and discovery context omitted by SDK 2.3's base storage API.

    The SDK still owns discovery validation, PKCE, issuer binding and refresh.
    Pinning its version and an end-to-end restart test protect these overrides.
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthMetadata,ProtectedResourceMetadata
    from .mcp_catalog import GOOGLE_MCP_ENDPOINTS
    google=server_url in GOOGLE_MCP_ENDPOINTS.values()
    requested_scope=metadata.scope
    redirect=handlers.get('redirect_handler')
    if redirect and google:
        async def google_redirect(url):
            parsed=urlsplit(url)
            if (parsed.scheme=='https' and parsed.netloc=='accounts.google.com'
                    and parsed.path in ('/o/oauth2/v2/auth','/o/oauth2/auth')):
                # Google does not issue refresh tokens for offline_access alone.
                # This runs only for interactive authorization, never refresh.
                # Consent also obtains a refresh token for previously granted accounts.
                params=[(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True)
                        if k not in ('access_type','prompt')]
                params.extend((('access_type','offline'),('prompt','consent')))
                url=urlunsplit(parsed._replace(query=urlencode(params)))
            await redirect(url)
        handlers['redirect_handler']=google_redirect
    class PersistentOAuth(OAuthClientProvider):
        @staticmethod
        def _google_metadata(value):
            # This built-in integration trusts Google's published issuer only.
            # Never send its registered client or refresh token to a substituted
            # discovery endpoint, including one retained from an old session.
            parsed=OAuthMetadata.model_validate(value)
            if (str(parsed.issuer).rstrip('/')!='https://accounts.google.com'
                    or str(parsed.authorization_endpoint)!='https://accounts.google.com/o/oauth2/v2/auth'
                    or str(parsed.token_endpoint)!='https://oauth2.googleapis.com/token'):
                raise MCPAuthRequired('Google returned unexpected sign-in settings. The connection was stopped.')
            return parsed

        async def ensure_google_authorization(self,http,interactive):
            """Google's initialize/tools-list are public, so initiate consent here.

            Use the registered native client and Google's OIDC discovery. The SDK
            still creates/checks PKCE, state, issuer and the token exchange. This
            runs before MCP requests and never fabricates an HTTP 401 challenge.
            """
            if not google:return
            await self._initialize()
            if not interactive and self.context.is_token_valid():return
            if not interactive and self.context.can_refresh_token():
                request=await self._refresh_token()
                response=await http.send(request,auth=None)
                try:
                    if await self._handle_refresh_response(response):return
                finally:await response.aclose()
            if not interactive:
                raise MCPAuthRequired('Sign in from Connect apps before using this connection.')
            if not self.context.client_info or not requested_scope:
                raise MCPAuthRequired('Google sign-in needs the publisher’s registered client and approved permissions. Ask IT to check this installation.')
            response=await http.get('https://accounts.google.com/.well-known/openid-configuration',auth=None)
            try:
                response.raise_for_status()
                self.context.oauth_metadata=self._google_metadata(response.json())
            finally:await response.aclose()
            self.context.auth_server_url='https://accounts.google.com'
            # Direct native authorization is not triggered by an MCP challenge.
            self.context.protected_resource_metadata=None
            self.context.protocol_version=None
            request=await self._perform_authorization()
            response=await http.send(request,auth=None)
            try:await self._handle_token_response(response)
            finally:await response.aclose()
            if not self.context.is_token_valid():
                raise MCPAuthRequired('Google sign-in did not return a usable authorization.')

        async def _perform_authorization_code_grant(self):
            if google:
                if not requested_scope:raise MCPAuthRequired('The Google registration is missing its approved permissions.')
                self._google_metadata(self.context.oauth_metadata.model_dump(mode='json'))
            # SDK discovery can list all supported alternatives. Preserve any
            # explicit IT scope rather than silently broadening permissions.
            if requested_scope:self.context.client_metadata.scope=requested_scope
            # A fresh grant may select a different account. Never attach the old
            # account's refresh token if this grant omits one. Refresh responses
            # themselves retain their prior refresh token through the SDK.
            storage.credentials.set(storage.key+':tokens','')
            self.context.clear_tokens()
            return await super()._perform_authorization_code_grant()

        async def _initialize(self):
            await super()._initialize()
            saved=storage.read('discovery')
            if saved:
                self.context.oauth_metadata=(self._google_metadata(saved['metadata']) if google else
                                             OAuthMetadata.model_validate(saved['metadata']))
                self.context.auth_server_url=saved.get('auth_server_url')
                if saved.get('resource'):
                    self.context.protected_resource_metadata=ProtectedResourceMetadata.model_validate(saved['resource'])
            if self.context.current_tokens:
                if not saved:
                    # Never guess a token endpoint and send a saved refresh token there.
                    self.context.clear_tokens()
                else:self.context.update_token_expiry(self.context.current_tokens)

        async def _handle_token_response(self,response):
            await super()._handle_token_response(response)
            context=self.context
            if context.oauth_metadata:
                storage.write('discovery',{'metadata':context.oauth_metadata.model_dump(mode='json',exclude_none=True),
                    'auth_server_url':context.auth_server_url,
                    'resource':context.protected_resource_metadata.model_dump(mode='json',exclude_none=True) if context.protected_resource_metadata else None})
    return PersistentOAuth(server_url,metadata,storage,**handlers)
