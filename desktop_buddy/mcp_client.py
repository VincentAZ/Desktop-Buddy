"""Persistent MCP configuration with lazy, cancellable SDK sessions.

Only the human settings UI can add servers. The agent can discover and invoke
their tools, never configure commands, credentials, or authorization policy.
"""
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import re
import threading
import time
from urllib.parse import urlsplit, parse_qsl

from . import __version__
from .mcp_auth import MCPTokenStorage, OAuthCallback, persistent_oauth_provider
from .mcp_catalog import host_matches, endpoint_matches, catalog_item
from .google_native import GoogleNative, GoogleAPIError, native_service
from .security import Cancelled, Denied, local_url


class MCPError(RuntimeError):pass


def sdk_available():
    try:
        from mcp import Client
        return True
    except ImportError:return False


@dataclass
class MCPConfig:
    id: str
    name: str
    transport: str = 'http'
    url: str = ''
    command: list[str] = field(default_factory=list)
    cwd: str = ''
    auth: str = 'oauth'
    enabled: bool = True
    domains: list[str] = field(default_factory=list)
    callback_port: int = 8765
    scope: str = ''

    def validate(self):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',self.id):raise ValueError('Use a server ID with letters, digits, hyphens or underscores.')
        if not self.name.strip() or len(self.name)>160:raise ValueError('Give this connection a short name.')
        if self.transport not in ('http','stdio'):raise ValueError('Choose HTTP or stdio transport.')
        if self.auth not in ('none','bearer','oauth'):raise ValueError('Unknown authentication method.')
        if not isinstance(self.enabled,bool):raise ValueError('Enabled must be true or false.')
        if not isinstance(self.callback_port,int) or not 1024<=self.callback_port<=65535:raise ValueError('OAuth callback port must be 1024–65535.')
        if not isinstance(self.domains,list) or any(not isinstance(d,str) or not re.fullmatch(r'[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*\.?',d) for d in self.domains):
            raise ValueError('Domains must be hostnames without paths or wildcards.')
        self.domains=[d.lower().rstrip('.') for d in self.domains]
        if self.transport=='http':
            p=urlsplit(self.url)
            if not p.hostname or p.username or p.password or p.fragment or (p.scheme!='https' and not local_url(self.url)):
                raise ValueError('MCP URL must use HTTPS (or numeric loopback HTTP), without credentials or fragments.')
            if p.port is not None and not 1<=p.port<=65535:
                raise ValueError('MCP server port must be between 1 and 65535.')
            if any(any(secret in key.lower() for secret in ('token','key','secret','password')) for key,_ in parse_qsl(p.query)):
                raise ValueError('Put credentials in the keyring fields, not the URL.')
        else:
            if not isinstance(self.command,list) or not self.command or any(not isinstance(a,str) or '\x00' in a or len(a)>8192 for a in self.command):
                raise ValueError('Enter a nonempty JSON command array. Use secret environment variables for credentials.')
            if self.auth!='none':raise ValueError('Stdio servers manage their own authentication; choose None and configure their secret environment.')
            if self.cwd and not Path(self.cwd).expanduser().is_dir():raise ValueError('Server working directory does not exist.')
        return self

    @property
    def secret_key(self):
        binding=json.dumps([self.id,self.transport,self.url,self.command,self.cwd,self.auth,self.callback_port,self.scope])
        return 'mcp:'+hashlib.sha256(binding.encode()).hexdigest()


def probe_server(url,timeout=12):
    """Ask a server address whether it wants a sign-in. Returns 'oauth' or 'none'.

    One unauthenticated initialize request, like any MCP client's first step.
    A 401/403 with a Bearer challenge means OAuth; anything else is treated as a
    public server, where the real connection attempt gives the final answer.
    """
    import requests
    session=requests.Session();session.trust_env=False
    body={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},
          'clientInfo':{'name':'desktop-buddy','version':__version__}}}
    try:
        response=session.post(url,json=body,timeout=timeout,allow_redirects=False,
            headers={'Accept':'application/json, text/event-stream','Content-Type':'application/json'})
    except requests.RequestException as error:
        raise MCPError(f'Could not reach the server: {type(error).__name__}.') from error
    finally:session.close()
    if response.status_code in (401,403) or 'www-authenticate' in {k.lower() for k in response.headers}:return 'oauth'
    if response.status_code>=500:raise MCPError(f'The server answered with an error (HTTP {response.status_code}).')
    return 'none'


def error_text(error, url=''):
    if isinstance(error,BaseExceptionGroup):return '; '.join(error_text(e,url) for e in error.exceptions)[:1000]
    text=str(error)[:1000] or type(error).__name__
    if text=='Server returned an error response' and url.endswith('.googleapis.com/mcp/v1'):
        # Google's MCP servers answer with HTTP 403 (and a JSON body) when the
        # Cloud project behind the sign-in key has not enabled that MCP API.
        return ('Google refused the request. The Google Cloud project behind the sign-in key must have this '
                'service\u2019s MCP API enabled (for example “Gmail MCP API”) and be enrolled in the Google Workspace '
                'Developer Preview Program. This is done by whoever created the sign-in key, not by you.')
    return text


class Connection:
    """One owner task keeps the SDK transport alive across operations."""
    def __init__(self, config, credentials, interactive=False):
        self.config=config;self.credentials=credentials;self.interactive=interactive
        self.ready=Future();self.requests=queue.Queue();self.stopped=threading.Event()
        self.loop=None;self.cancel_scope=None;self.failure=''
        self.auth=None;self.authenticated=config.auth!='oauth'
        # Let bounded SDK teardown finish when the UI exits, including killing stdio children.
        self.thread=threading.Thread(target=self._thread,daemon=False,name='mcp-'+config.id)
        self.thread.start()

    def _thread(self):
        try:asyncio.run(self._serve())
        except BaseException as error:
            self.failure=self.credentials.redactor.text(error_text(error,self.config.url))
        finally:
            self.stopped.set()
            if not self.ready.done():self.ready.set_exception(MCPError(self.failure or 'MCP connection closed.'))
            while True:
                try:future,*_=self.requests.get_nowait()
                except queue.Empty:break
                if not future.done():future.set_exception(MCPError('MCP connection closed; no operation was replayed.'))

    async def _serve(self):
        import anyio
        self.loop=asyncio.get_running_loop()
        with anyio.CancelScope() as scope:
            self.cancel_scope=scope
            if not self.stopped.is_set():await self._session()

    async def _session(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.types import Implementation
        import httpx2
        c=self.config
        storage=MCPTokenStorage(self.credentials,c.secret_key)
        manual=storage.read('manual') or {}
        async with AsyncExitStack() as stack:
            callback=None
            native=''
            if c.transport=='stdio':
                # No inherited API keys or shell invocation; SDK adds only its minimal defaults.
                env={k:os.environ[k] for k in ('PATH','HOME','LANG','XDG_DATA_HOME','XDG_CONFIG_HOME') if k in os.environ}
                env.update(manual.get('env',{}))
                errlog=stack.enter_context(open(os.devnull,'w'))
                transport=stdio_client(StdioServerParameters(command=c.command[0],args=c.command[1:],env=env,
                    cwd=str(Path(c.cwd).expanduser()) if c.cwd else None),errlog=errlog)
            else:
                headers=dict(manual.get('headers',{}));auth=None
                if c.auth=='bearer':
                    if not manual.get('bearer'):raise MCPError('This custom connection needs an administrator-provided credential. Ask IT to check its advanced settings.')
                    headers['Authorization']='Bearer '+manual['bearer']
                if c.auth=='oauth':
                    from mcp.shared.auth import OAuthClientMetadata
                    callback=OAuthCallback(c.callback_port,self.interactive)
                    stack.callback(callback.close)
                    auth=persistent_oauth_provider(c.url,OAuthClientMetadata(client_name='Desktop Buddy',
                        redirect_uris=[callback.uri],token_endpoint_auth_method='none',scope=c.scope or None),
                        storage,redirect_handler=callback.redirect,callback_handler=callback.wait)
                http=await stack.enter_async_context(httpx2.AsyncClient(headers=headers,auth=auth,trust_env=False,
                    timeout=httpx2.Timeout(30,read=120),follow_redirects=False))
                self.auth=auth
                if auth:await auth.ensure_google_authorization(http,self.interactive)
                native=native_service(c.url) if auth else ''
                if not native:transport=streamable_http_client(c.url,http_client=http,max_sse_event_size=2*1024*1024)
            if native:
                # Gmail and Calendar are served by Buddy's own code on Google's
                # regular APIs, with the token the sign-in above produced.
                client=GoogleNative(native,http)
            else:
                client=await stack.enter_async_context(Client(transport,read_timeout_seconds=240 if self.interactive else 120,
                    client_info=Implementation(name='desktop-buddy',version=__version__),cache=None))
            if self.auth:self.authenticated=self.auth.context.is_token_valid()
            self.ready.set_result(True)
            while not self.stopped.is_set():
                try:future,method,args,cancel=self.requests.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.03);continue
                if cancel.is_set():
                    future.set_exception(Cancelled('MCP operation cancelled before dispatch.'));continue
                operation=asyncio.create_task(self._operation(client,method,args))
                try:
                    deadline=time.monotonic()+(240 if self.interactive else 120)
                    while not operation.done():
                        if cancel.is_set() or self.stopped.is_set():raise Cancelled('MCP operation stopped. Its external outcome may be unknown; it was not replayed.')
                        if time.monotonic()>deadline:raise TimeoutError('MCP operation timed out; it was not replayed.')
                        await asyncio.sleep(.03)
                    result=operation.result()
                    if self.auth:self.authenticated=self.auth.context.is_token_valid()
                    future.set_result(result)
                except BaseException as error:
                    if not future.done():future.set_exception(MCPError(error_text(error,self.config.url)) if not isinstance(error,Cancelled) else error)
                    # A broken/cancelled session is discarded, never retried mid-call.
                    raise
                finally:
                    # Some servers challenge tools/list, not initialize. Keep the
                    # human-initiated sign-in open through the first operation.
                    if callback:callback.interactive=False;callback.close()
                    self.interactive=False
                    if not operation.done():operation.cancel()
                    await asyncio.gather(operation,return_exceptions=True)

    async def _operation(self,client,method,args):
        if isinstance(client,GoogleNative):
            try:return await client.operation(method,args)
            except GoogleAPIError as error:raise MCPError(str(error)) from error
        if method=='tools':
            cursor=None;seen=set();tools=[]
            for _ in range(20):
                page=await client.list_tools(cursor=cursor)
                tools.extend(tool.model_dump(mode='json',exclude_none=True,by_alias=True) for tool in page.tools)
                cursor=page.next_cursor
                if len(tools)>2000:raise MCPError('Server exposes more than 2,000 tools; narrow its configuration.')
                if not cursor:return tools
                if cursor in seen:raise MCPError('Server repeated a tool-list cursor.')
                seen.add(cursor)
            raise MCPError('Server tool list exceeded 20 pages.')
        if method=='call':
            result=await client.call_tool(args['name'],args['arguments'])
        elif method=='resources':result=await client.list_resources(cursor=args.get('cursor'))
        elif method=='read_resource':result=await client.read_resource(args['uri'])
        else:raise MCPError('Unknown MCP operation.')
        data=result.model_dump(mode='json',exclude_none=True,by_alias=True)
        if len(json.dumps(data))>8*1024*1024:raise MCPError('MCP result exceeds 8 MiB.')
        return data

    @staticmethod
    def wait(future, token, deadline, stopped=None):
        while True:
            token.check()
            if stopped and stopped.is_set() and not future.done():raise MCPError('MCP connection closed; no operation was replayed.')
            if time.monotonic()>deadline:raise TimeoutError('MCP connection timed out.')
            try:return future.result(timeout=.05)
            except FutureTimeout:
                if future.done():return future.result()

    def invoke(self, method, args, token):
        try:self.wait(self.ready,token,time.monotonic()+(240 if self.interactive else 45))
        except BaseException:
            self.close();raise
        if self.stopped.is_set():raise MCPError(self.failure or 'MCP connection closed.')
        future=Future();cancel=threading.Event()
        self.requests.put((future,method,args,cancel))
        try:return self.wait(future,token,time.monotonic()+(245 if self.interactive else 125),self.stopped)
        except BaseException:
            cancel.set();self.close();raise

    def close(self):
        self.stopped.set()
        if self.loop and self.cancel_scope and not self.loop.is_closed():
            try:self.loop.call_soon_threadsafe(self.cancel_scope.cancel)
            except RuntimeError:pass


class MCPManager:
    def __init__(self, store, credentials, parent=None):
        import weakref
        self.store=store;self.credentials=credentials;self.connections={}
        self.lock=parent.lock if parent else threading.RLock()
        self.peers=parent.peers if parent else weakref.WeakSet()
        with self.lock:self.peers.add(self)
        from .mcp_registration import MCPRegistrations
        self.registrations=MCPRegistrations(self)
        # SDK failures reach our redacted UI/audit boundary instead of raw stderr.
        logger=logging.getLogger('mcp');logger.addHandler(logging.NullHandler());logger.propagate=False

    def fork(self):
        """Each task owns its transport; settings and explicit disconnects are shared."""
        return MCPManager(self.store,self.credentials,parent=self)

    def configs(self):
        return {key:MCPConfig(**value).validate() for key,value in self.store.get('mcp_servers',{}).items()}

    def save(self, config, manual=None, client_info=None):
        config.validate()
        # Validation happens before any keyring/config mutation.
        if manual is not None:
            if not isinstance(manual,dict) or set(manual)-{'bearer','env','headers'}:raise ValueError('Secrets accept bearer, env and headers only.')
            if 'bearer' in manual and not isinstance(manual['bearer'],str):raise ValueError('Bearer token must be text.')
            if any(c in manual.get('bearer','') for c in ('\n','\r','\x00')):raise ValueError('Bearer token must be single-line text.')
            for key in ('env','headers'):
                values=manual.get(key,{})
                if not isinstance(values,dict) or any(not isinstance(k,str) or not isinstance(v,str) or '\x00' in k+v or '\n' in k+v or '\r' in k+v for k,v in values.items()):
                    raise ValueError('Secret environment and headers must be objects with single-line string values.')
            if any(k.lower() in {'host','mcp-session-id','mcp-protocol-version','content-type','accept'} for k in manual.get('headers',{})):
                raise ValueError('Do not override MCP transport headers.')
        if client_info is not None:
            from mcp.shared.auth import OAuthClientInformationFull
            client_info=OAuthClientInformationFull.model_validate(client_info).model_dump(mode='json',exclude_none=True)
        with self.lock:
            old=self.configs().get(config.id)
            if manual is not None:
                for value in [manual.get('bearer'),*manual.get('env',{}).values(),*manual.get('headers',{}).values()]:
                    self.credentials.redactor.register(value)
            if client_info is not None:self.credentials.redactor.register(client_info.get('client_secret'))
            values=self.store.get('mcp_servers',{});values[config.id]=asdict(config)
            if self.store.redactor.clean(values)!=values:
                raise ValueError('A connection setting contains a registered secret. Keep credentials out of names, URLs and commands; use secret fields only.')
            self.disconnect(config.id)
            storage=MCPTokenStorage(self.credentials,config.secret_key)
            if manual is not None:
                storage.write('manual',manual)
            if client_info is not None:
                if storage.read('client')!=client_info:
                    for kind in ('tokens','discovery'):self.credentials.set(config.secret_key+':'+kind,'')
                storage.write('client',client_info)
            self.store.set('mcp_servers',values)
            if manual is not None or client_info is not None or old and old.secret_key!=config.secret_key:
                bindings=self.store.get('mcp_managed_connections',{})
                if config.id in bindings:
                    bindings.pop(config.id);self.store.set('mcp_managed_connections',bindings)
            if old and old.secret_key!=config.secret_key:
                for kind in ('manual','tokens','client','discovery'):self.credentials.set(old.secret_key+':'+kind,'')
        return config

    def remove(self, ident):
        with self.lock:
            config=self.configs()[ident];self.disconnect(ident)
            for kind in ('manual','tokens','client','discovery'):self.credentials.set(config.secret_key+':'+kind,'')
            values=self.store.get('mcp_servers',{});values.pop(ident,None);self.store.set('mcp_servers',values)
            bindings=self.store.get('mcp_managed_connections',{})
            bindings.pop(ident,None);self.store.set('mcp_managed_connections',bindings)

    def disconnect(self, ident):
        with self.lock:
            for manager in list(self.peers):manager._disconnect_local(ident)

    def _disconnect_local(self, ident):
        with self.lock:connection=self.connections.pop(ident,None)
        if connection:connection.close()

    def disconnect_service(self, service):
        """Stop live sessions bound to a catalog service (its client changed)."""
        with self.lock:
            for ident,binding in list(self.store.get('mcp_managed_connections',{}).items()):
                if binding.get('service')==service:self.disconnect(ident)

    def close(self):
        with self.lock:
            connections=list(self.connections.values());self.connections.clear()
        for connection in connections:connection.close()

    def close_all(self):
        with self.lock:
            for manager in list(self.peers):manager.close()

    def servers(self):
        with self.lock:
            return [{'id':c.id,'name':c.name,'transport':c.transport,'enabled':c.enabled,
                     'status':('connected' if self.connections[c.id].authenticated else 'sign-in needed') if c.id in self.connections and self.connections[c.id].ready.done() and not self.connections[c.id].stopped.is_set() else 'saved · reconnects on use'}
                    for c in self.configs().values()]

    def _get(self, ident, interactive=False):
        if not sdk_available():raise MCPError('Connected-app support is missing from this installation. Ask IT to repair or reinstall Desktop Buddy.')
        with self.lock:
            config=self.configs().get(ident)
            if not config or not config.enabled:raise MCPError('Open Connect apps and reconnect this saved connection first.')
            config=self.registrations.prepare(config)
            connection=self.connections.get(ident)
            if interactive:
                self.disconnect(ident);connection=None
            elif connection and connection.stopped.is_set():
                self._disconnect_local(ident);connection=None
            if connection is None:
                connection=Connection(config,self.credentials,interactive);self.connections[ident]=connection
            return connection

    def tools(self, ident, token, interactive=False):
        token.check()
        connection=self._get(ident,interactive)
        result=connection.invoke('tools',{},token)
        if interactive and connection.config.auth=='oauth' and not connection.authenticated:
            self.disconnect(ident)
            raise MCPError('The service is reachable, but account sign-in was not completed. Public tool discovery is not account authorization.')
        if len(json.dumps(result))>2*1024*1024:raise MCPError('MCP tool catalog exceeds 2 MiB.')
        return self.store.redactor.clean(result)

    def call(self, ident, name, arguments, token):
        token.check()
        import jsonschema
        from referencing import Registry as SchemaRegistry
        connection=self._get(ident)
        definitions=connection.invoke('tools',{},token)
        definition=next((t for t in definitions if t['name']==name),None)
        if definition is None:raise MCPError('Tool not found on this MCP server. Refresh its tool list.')
        spec=definition.get('inputSchema',{})
        # Never fetch schema references from the network or local filesystem.
        def references(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key in ('$ref','$dynamicRef') and (not isinstance(item,str) or not item.startswith('#')):
                        raise MCPError('External JSON schema references are not supported.')
                    references(item)
            elif isinstance(value,list):
                for item in value:references(item)
        references(spec)
        validator=jsonschema.validators.validator_for(spec);validator.check_schema(spec)
        validator(spec,registry=SchemaRegistry()).validate(arguments)
        return self.store.redactor.clean(connection.invoke('call',{'name':name,'arguments':arguments},token))

    def resources(self,ident,token,cursor=None):
        token.check()
        return self.store.redactor.clean(self._get(ident).invoke('resources',{'cursor':cursor},token))

    def read_resource(self,ident,uri,token):
        token.check()
        return self.store.redactor.clean(self._get(ident).invoke('read_resource',{'uri':uri},token))

    def browser_alternative(self,url):
        """Names of saved, enabled connections that already cover this website.

        MCP comes first: when the person has connected an app, its website is
        reached through that connection, not the browser. A site with no saved
        connection stays open to the browser; an unconnected catalog entry is
        not a reason to refuse the only route that works.
        """
        host=(urlsplit(url).hostname or '').lower().rstrip('.')
        matches=[]
        for c in self.configs().values():
            if not c.enabled:continue
            item=catalog_item(c)
            domains=list(c.domains)+(list(item['domains']) if item else [])
            if (any(host_matches(host,d) for d in domains) or
                    c.transport=='http' and endpoint_matches(url,c.url)):
                matches.append(c.name)
        return sorted(set(matches))

    def require_browser_fallback(self,url):
        matches=self.browser_alternative(url)
        if matches:raise Denied('This website is already connected as an app: '+', '.join(matches)+'. Use that connection (mcp.tools and mcp.call) instead of the browser.')
