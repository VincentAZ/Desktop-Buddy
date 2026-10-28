"""Publisher/IT supplied OAuth clients; ordinary users only see Connect.

A client can come from two places: shipped with the app (``publisher.CLIENTS``)
or pasted by IT on this installation, which stores it in GNOME Keyring. The
listing never reads the keyring; only Connect does.
"""
from dataclasses import replace
import json
from pathlib import Path
import uuid

from .mcp_auth import MCPTokenStorage
from .mcp_catalog import CATALOG, CATALOG_BY_ID, catalog_item
from . import publisher

BUILTIN_REVISION = 'builtin'


class MCPRegistrations:
    def __init__(self, manager):
        self.manager=manager

    def records(self):
        return self.manager.store.get('mcp_registrations',{})

    @staticmethod
    def eligible(item):
        return bool(item['url'] and item['auth']=='oauth' and not item['command'])

    def record(self,item):
        """Nonsecret registration metadata for this catalog item, or None."""
        if not self.eligible(item):return None
        saved=self.records().get(item['id'],{})
        if saved.get('url')==item['url'] and saved.get('revision'):return saved
        shipped=publisher.builtin_client(item['id'])
        if shipped and item['url']==CATALOG_BY_ID[item['id']]['url']:
            return {'url':item['url'],'callback_port':shipped.get('callback_port',publisher.CALLBACK_PORT),
                    'scope':shipped.get('scope') or item.get('scope',''),'revision':BUILTIN_REVISION}
        return None

    def available(self,item):
        return self.record(item) is not None

    def source(self,item):
        """'installation' when IT pasted the client here, 'shipped' when it came with the app."""
        record=self.record(item)
        if record is None:return ''
        return 'shipped' if record['revision']==BUILTIN_REVISION else 'installation'

    def client(self,record,item):
        if record['revision']==BUILTIN_REVISION:
            shipped=publisher.builtin_client(item['id']) or {}
            return publisher.client_record(shipped['client_id'],shipped.get('client_secret',''),record['callback_port'])
        return MCPTokenStorage(self.manager.credentials,'mcp-registration:'+record['revision']).read('client')

    @staticmethod
    def validate(bundle):
        from mcp.shared.auth import OAuthClientInformationFull
        if not isinstance(bundle,dict) or set(bundle)!={'version','registrations'} or bundle['version']!=1:
            raise ValueError('Expected a version 1 Buddy app-registration bundle.')
        entries=bundle['registrations']
        if not isinstance(entries,list) or not 1<=len(entries)<=len(CATALOG):
            raise ValueError('The bundle must contain at least one app registration.')
        checked={}
        for row in entries:
            if not isinstance(row,dict) or set(row)-{'service','client','callback_port','scope'}:
                raise ValueError('An app registration contains unsupported fields.')
            service=row.get('service')
            item=CATALOG_BY_ID.get(service)
            if not item or not MCPRegistrations.eligible(item):
                raise ValueError('Registrations require an official catalog OAuth endpoint.')
            if service in checked:raise ValueError('The bundle contains a duplicate app registration.')
            port=row.get('callback_port',publisher.CALLBACK_PORT);scope=row.get('scope',item.get('scope',''))
            if type(port) is not int or not 1024<=port<=65535:
                raise ValueError('The callback port must be between 1024 and 65535.')
            if not isinstance(scope,str) or len(scope)>4096 or any(ord(c)<32 or ord(c)>126 for c in scope):
                raise ValueError('Scopes must be space-separated ASCII text.')
            raw=row.get('client')
            if not isinstance(raw,dict) or set(raw)-{'client_id','client_secret','redirect_uris',
                    'token_endpoint_auth_method','grant_types','response_types','scope','client_name'}:
                raise ValueError('The bundle contains unsupported OAuth client fields.')
            # Do not echo a Pydantic validation error: it may contain the secret.
            try:client=OAuthClientInformationFull.model_validate(raw).model_dump(mode='json',exclude_none=True)
            except Exception:raise ValueError('The registered OAuth client information is invalid.') from None
            if not client.get('client_id') or client.get('token_endpoint_auth_method','none') not in ('none','client_secret_post','client_secret_basic'):
                raise ValueError('Use a client ID and a supported OAuth token authentication method.')
            method=client.get('token_endpoint_auth_method','none')
            if method!='none' and not client.get('client_secret'):
                raise ValueError('This OAuth client authentication method requires a client secret.')
            callback=f'http://127.0.0.1:{port}/oauth/callback'
            if callback not in client.get('redirect_uris',[]):
                raise ValueError('The registered client must allow Buddy’s exact loopback callback URL.')
            if client.get('scope') and client['scope']!=scope:
                raise ValueError('The client scope must match the registration scope.')
            checked[service]=({'url':item['url'],'callback_port':port,'scope':scope},client)
        return checked

    def import_file(self,path):
        # Bounded read. The source file belongs to IT; never copy it into app data.
        with Path(path).open('rb') as source:raw=source.read(256*1024+1)
        if len(raw)>256*1024:raise ValueError('The registration bundle is too large.')
        try:bundle=json.loads(raw)
        except (ValueError,UnicodeError):raise ValueError('The registration bundle is not valid JSON.') from None
        return self.install(bundle)

    @staticmethod
    def services_in_group(group):
        return [item['id'] for item in CATALOG if publisher.group_of(item['id'])==group
                and item.get('sign_in')=='client' and MCPRegistrations.eligible(item)]

    def install_client(self,group,client_id,client_secret=''):
        """IT pasted a client for one service, or for every Google service at once."""
        client_id=(client_id or '').strip();client_secret=(client_secret or '').strip()
        if not client_id:raise ValueError('Paste the client ID.')
        services=self.services_in_group(group)
        if not services:raise ValueError('This service does not take a pasted client.')
        client=publisher.client_record(client_id,client_secret)
        bundle={'version':1,'registrations':[{'service':service,'client':client,
                 'scope':CATALOG_BY_ID[service].get('scope','')} for service in services]}
        return self.install(bundle)

    def forget(self,group):
        """Remove IT-pasted clients for a service group. Shipped clients stay."""
        manager=self.manager
        with manager.lock:
            records=self.records();removed=[]
            for service in self.services_in_group(group):
                record=records.pop(service,None)
                if record:
                    removed.append(record['revision'])
                    manager.disconnect_service(service)
            manager.store.set('mcp_registrations',records)
            for revision in removed:manager.credentials.set('mcp-registration:'+revision+':client','')
        return removed

    def install(self,bundle):
        checked=self.validate(bundle);manager=self.manager
        with manager.lock:
            records=self.records();old=[];written=[]
            try:
                for service,(record,client) in checked.items():
                    revision=uuid.uuid4().hex
                    key='mcp-registration:'+revision
                    MCPTokenStorage(manager.credentials,key).write('client',client);written.append(key)
                    if service in records:old.append(records[service]['revision'])
                    records[service]={**record,'revision':revision}
                manager.store.set('mcp_registrations',records)
            except Exception:
                for key in written:manager.credentials.set(key+':client','')
                raise
            # Every live managed session must stop using the superseded client.
            configs=manager.configs()
            for ident,binding in manager.store.get('mcp_managed_connections',{}).items():
                if binding['service'] in checked and ident in configs and configs[ident].secret_key==binding['key']:
                    manager.disconnect(ident)
            for revision in old:manager.credentials.set('mcp-registration:'+revision+':client','')
        return list(checked)

    def prepare(self,config,*,explicit=False):
        """Bind a user Connect action, or refresh an already managed connection.

        Advanced edits break the binding; they never inherit a product client.
        """
        manager=self.manager
        with manager.lock:
            item=catalog_item(config)
            bindings=manager.store.get('mcp_managed_connections',{})
            previous=bindings.get(config.id,{})
            bound=previous.get('key')==config.secret_key
            record=self.record(item) if item else None
            if not item or record is None or not (explicit or bound):return config
            if bound and previous.get('revision')==record['revision']:return config
            client=self.client(record,item)
            if not client:raise RuntimeError('The app sign-in key is missing from the keyring. Ask IT to enter it again under IT setup.')
            config=replace(config,callback_port=record['callback_port'],scope=record['scope'])
            # Product connections never inherit custom Authorization headers.
            manager.save(config,manual={},client_info=client)
            bindings[config.id]={'service':item['id'],'revision':record['revision'],'key':config.secret_key}
            manager.store.set('mcp_managed_connections',bindings)
            return config
