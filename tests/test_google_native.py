"""Buddy's own Gmail and Calendar tools, served on Google's regular APIs."""
import asyncio
import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from desktop_buddy import google_native
from desktop_buddy.google_native import GoogleNative, build_mime, html_to_text, native_service
from desktop_buddy.mcp_auth import MCPTokenStorage
from desktop_buddy.mcp_catalog import GOOGLE_MCP_ENDPOINTS
from desktop_buddy.mcp_client import MCPConfig, MCPManager, MCPError, sdk_available
from desktop_buddy.security import Redactor, StopToken, Policy
from desktop_buddy.state import Store
from desktop_buddy.tools import Registry
from test_mcp import Credentials


def b64(text):return base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')


class FakeGoogle:
    """Enough of Gmail and Calendar REST to exercise every tool."""
    def __init__(self,httpx):
        self.httpx=httpx;self.requests=[];self.fail=None
        self.thread={'id':'t1','messages':[
            {'id':'m1','threadId':'t1','labelIds':['INBOX','UNREAD'],'snippet':'Your licence key is &amp; enclosed',
             'payload':{'mimeType':'multipart/mixed','headers':[{'name':'From','value':'Blackmagic <noreply@blackmagicdesign.com>'},
                 {'name':'To','value':'vincent@example.com'},{'name':'Subject','value':'DaVinci Resolve Studio'},
                 {'name':'Date','value':'Mon, 27 Oct 2026 10:00:00 +0000'},{'name':'Message-ID','value':'<orig@bmd>'}],
                 'parts':[{'mimeType':'text/plain','body':{'data':b64('Your key is ABCD-1234.\n')}},
                          {'mimeType':'text/html','body':{'data':b64('<p>Your key is <b>ABCD-1234</b>.</p>')}},
                          {'mimeType':'application/pdf','filename':'invoice.pdf','body':{'size':1200,'attachmentId':'att1'}}]}},
            {'id':'m2','threadId':'t1','labelIds':['SENT'],'snippet':'Thanks',
             'payload':{'mimeType':'text/html','headers':[{'name':'From','value':'vincent@example.com'},{'name':'Subject','value':'Re: DaVinci Resolve Studio'},
                 {'name':'Date','value':'Mon, 27 Oct 2026 11:00:00 +0000'}],'body':{'data':b64('<html><head><style>p{}</style></head><body><p>Thanks &amp; regards</p></body></html>')}}}]}
        self.event={'id':'e1','summary':'Standup','start':{'dateTime':'2026-11-03T09:00:00+01:00'},'end':{'dateTime':'2026-11-03T09:15:00+01:00'},
                    'attendees':[{'email':'vincent@example.com','self':True,'responseStatus':'needsAction'},{'email':'ana@example.com','responseStatus':'accepted'}],
                    'htmlLink':'https://calendar.google.com/event?eid=e1','organizer':{'email':'ana@example.com'}}

    def handler(self,request):
        self.requests.append(request);url=str(request.url);path=urlsplit(url).path;query=parse_qs(urlsplit(url).query)
        R=self.httpx.Response
        if self.fail:return R(self.fail[0],json={'error':{'code':self.fail[0],'message':self.fail[1]}})
        if path.endswith('/users/me/threads') and request.method=='GET':
            assert query['q']==['from:blackmagicdesign.com'];assert query['maxResults']==['5']
            return R(200,json={'threads':[{'id':'t1','snippet':'Your licence key is &amp; enclosed'}],'resultSizeEstimate':1,'nextPageToken':'next'})
        if path.endswith('/users/me/threads/t1') and request.method=='GET':return R(200,json=self.thread)
        if path.endswith('/users/me/messages/m1') and request.method=='GET':return R(200,json=self.thread['messages'][0])
        if path.endswith('/users/me/labels'):return R(200,json={'labels':[{'id':'INBOX','name':'INBOX','type':'system'},{'id':'Label_7','name':'Invoices','type':'user'}]})
        if path.endswith('/users/me/drafts') and request.method=='POST':
            self.draft=json.loads(request.content);return R(200,json={'id':'d1','message':{'id':'m9','threadId':self.draft['message'].get('threadId','t9')}})
        if path.endswith('/users/me/messages/send'):
            self.sent=json.loads(request.content);return R(200,json={'id':'m10','threadId':'t10'})
        if path.endswith('/users/me/threads/t1/modify'):
            self.modified=json.loads(request.content);return R(200,json={'id':'t1','messages':[{'labelIds':['INBOX','STARRED']}]})
        if path.endswith('/users/me/threads/t1/trash'):return R(200,json={'id':'t1'})
        if path.endswith('/users/me/calendarList'):return R(200,json={'items':[{'id':'primary-id','summary':'Vincent','primary':True,'timeZone':'Europe/Amsterdam','accessRole':'owner'}]})
        if path.endswith('/calendars/primary/events') and request.method=='GET':
            assert query['singleEvents']==['true'] and query['orderBy']==['startTime'] and query['timeMin']==['2026-11-01T00:00:00Z']
            return R(200,json={'items':[self.event],'timeZone':'Europe/Amsterdam'})
        if path.endswith('/calendars/primary/events') and request.method=='POST':
            self.created=json.loads(request.content);assert query['sendUpdates']==['all']
            return R(200,json={**self.event,'id':'e2',**self.created})
        if path.endswith('/calendars/primary/events/e1') and request.method=='GET':return R(200,json=self.event)
        if path.endswith('/calendars/primary/events/e1') and request.method=='PATCH':
            self.patched=json.loads(request.content);return R(200,json={**self.event,**self.patched})
        if path.endswith('/calendars/primary/events/e1') and request.method=='DELETE':return R(204)
        raise AssertionError('Unexpected request: '+request.method+' '+url)


@unittest.skipUnless(sdk_available(),'MCP SDK required')
class GoogleNativeTests(unittest.TestCase):
    def setUp(self):
        import httpx2
        self.httpx=httpx2;self.google=FakeGoogle(httpx2)

    def run_tool(self,service,name,arguments):
        async def work():
            async with self.httpx.AsyncClient(transport=self.httpx.MockTransport(self.google.handler)) as http:
                return await GoogleNative(service,http).operation('call',{'name':name,'arguments':arguments})
        result=asyncio.run(work())
        return result['isError'],json.loads(result['content'][0]['text']) if not result['isError'] else result['content'][0]['text']

    def test_only_gmail_and_calendar_are_served_natively(self):
        self.assertEqual(native_service(GOOGLE_MCP_ENDPOINTS['gmail']),'gmail')
        self.assertEqual(native_service(GOOGLE_MCP_ENDPOINTS['calendar']+'/'),'calendar')
        self.assertEqual(native_service(GOOGLE_MCP_ENDPOINTS['drive']),'')
        self.assertEqual(native_service('https://mcp.notion.com/mcp'),'')
        for service in ('gmail','calendar'):
            tools=asyncio.run(GoogleNative(service,None).operation('tools',{}))
            self.assertTrue(all(t['inputSchema']['additionalProperties'] is False for t in tools))
            self.assertEqual(len({t['name'] for t in tools}),len(tools))

    def test_search_and_read_mail_hand_over_text_not_markup(self):
        error,found=self.run_tool('gmail','search_threads',{'query':'from:blackmagicdesign.com','pageSize':5})
        self.assertFalse(error)
        self.assertEqual(found['threads'][0]['subject'],'DaVinci Resolve Studio')
        self.assertEqual(found['threads'][0]['messageCount'],2);self.assertEqual(found['nextPageToken'],'next')
        self.assertEqual(found['threads'][0]['snippet'],'Your licence key is & enclosed')
        error,thread=self.run_tool('gmail','get_thread',{'threadId':'t1'})
        first,second=thread['messages']
        self.assertEqual(first['text'],'Your key is ABCD-1234.')
        self.assertEqual(first['attachments'],[{'filename':'invoice.pdf','mimeType':'application/pdf','size':1200,'attachmentId':'att1'}])
        self.assertEqual(second['text'],'Thanks & regards')   # HTML-only message: tags and styles removed
        self.assertIn('mail.google.com',first['viewUrl'])
        error,message=self.run_tool('gmail','get_message',{'messageId':'m1'})
        self.assertEqual(message['from'],'Blackmagic <noreply@blackmagicdesign.com>')
        self.assertEqual(html_to_text('<div>a<br>b</div><script>x()</script>'),'a\nb')

    def test_drafts_and_replies_keep_the_thread(self):
        error,draft=self.run_tool('gmail','create_draft',{'to':['ana@example.com'],'subject':'','body':'Got it, thanks.','replyToMessageId':'m1'})
        self.assertFalse(error);self.assertEqual(draft['draftId'],'d1')
        sent=self.google.draft['message'];self.assertEqual(sent['threadId'],'t1')
        raw=base64.urlsafe_b64decode(sent['raw']+'==').decode()
        self.assertIn('In-Reply-To: <orig@bmd>',raw);self.assertIn('Subject: Re: DaVinci Resolve Studio',raw);self.assertIn('Got it, thanks.',raw)
        error,result=self.run_tool('gmail','send_message',{'to':['ana@example.com'],'cc':['bob@example.com'],'subject':'Hello','body':'Plain','htmlBody':'<b>Rich</b>'})
        self.assertEqual(result,{'messageId':'m10','threadId':'t10','sent':True})
        raw=base64.urlsafe_b64decode(self.google.sent['raw']+'==').decode()
        self.assertIn('Cc: bob@example.com',raw);self.assertIn('text/html',raw)
        self.assertIn('Subject: Hello',build_mime({'to':['x@example.com'],'subject':'Hello','body':'b'}) and raw)

    def test_labels_and_bin(self):
        error,labels=self.run_tool('gmail','list_labels',{})
        self.assertEqual(labels['labels'][1],{'id':'Label_7','name':'Invoices','type':'user'})
        error,changed=self.run_tool('gmail','modify_labels',{'threadId':'t1','addLabelIds':['STARRED'],'removeLabelIds':['UNREAD']})
        self.assertEqual(self.google.modified,{'addLabelIds':['STARRED'],'removeLabelIds':['UNREAD']})
        self.assertEqual(changed['labelIds'],['INBOX','STARRED'])
        error,binned=self.run_tool('gmail','trash_thread',{'threadId':'t1'})
        self.assertEqual(binned,{'threadId':'t1','trashed':True})

    def test_calendar_tools(self):
        error,calendars=self.run_tool('calendar','list_calendars',{})
        self.assertEqual(calendars['calendars'][0]['timeZone'],'Europe/Amsterdam')
        error,events=self.run_tool('calendar','list_events',{'timeMin':'2026-11-01T00:00:00Z','timeMax':'2026-11-08T00:00:00Z'})
        self.assertEqual(events['events'][0]['summary'],'Standup');self.assertTrue(events['events'][0]['attendees'][0]['self'])
        error,created=self.run_tool('calendar','create_event',{'summary':'Lunch','start':'2026-11-04','end':'2026-11-05','attendees':['ana@example.com']})
        self.assertEqual(self.google.created,{'summary':'Lunch','start':{'date':'2026-11-04'},'end':{'date':'2026-11-05'},'attendees':[{'email':'ana@example.com'}]})
        error,updated=self.run_tool('calendar','update_event',{'eventId':'e1','start':'2026-11-03T10:00:00','end':'2026-11-03T10:30:00','timeZone':'Europe/Amsterdam'})
        self.assertEqual(self.google.patched['start'],{'dateTime':'2026-11-03T10:00:00','timeZone':'Europe/Amsterdam'})
        error,answered=self.run_tool('calendar','respond_to_event',{'eventId':'e1','response':'accepted'})
        self.assertEqual(self.google.patched['attendees'][0]['responseStatus'],'accepted')
        self.assertEqual(self.google.patched['attendees'][1]['responseStatus'],'accepted')
        error,gone=self.run_tool('calendar','delete_event',{'eventId':'e1'})
        self.assertEqual(gone,{'eventId':'e1','deleted':True})
        error,event=self.run_tool('calendar','get_event',{'eventId':'e1'})
        self.assertEqual(event['htmlLink'],'https://calendar.google.com/event?eid=e1')

    def test_google_errors_become_plain_tool_errors(self):
        self.google.fail=(403,'Request had insufficient authentication scopes.')
        error,text=self.run_tool('gmail','list_labels',{})
        self.assertTrue(error);self.assertIn('Connect apps',text)
        self.google.fail=(429,'Quota exceeded')
        error,text=self.run_tool('gmail','list_labels',{})
        self.assertTrue(error);self.assertEqual(text,'Google answered: Quota exceeded')
        error,text=self.run_tool('gmail','no_such_tool',{})
        self.assertTrue(error)


@unittest.skipUnless(sdk_available(),'MCP SDK required')
class GmailConnectionTests(unittest.TestCase):
    """A saved Gmail connection is served by Buddy with the signed-in token."""
    def setUp(self):
        import httpx2
        self.httpx=httpx2;self.google=FakeGoogle(httpx2);self.temp=tempfile.TemporaryDirectory()
        self.redactor=Redactor();self.credentials=Credentials(self.redactor)
        self.store=Store(Path(self.temp.name)/'state',self.redactor);self.manager=MCPManager(self.store,self.credentials)
        self.config=self.manager.registrations.prepare(MCPConfig('google-gmail-x','Google Gmail',url=GOOGLE_MCP_ENDPOINTS['gmail'],
            scope='https://www.googleapis.com/auth/gmail.modify'),explicit=False)
        storage=MCPTokenStorage(self.credentials,self.config.secret_key)
        storage.write('client',{'client_id':'shipped','client_secret':'private-value','token_endpoint_auth_method':'client_secret_post','redirect_uris':['http://127.0.0.1:8765/oauth/callback']})
        storage.write('tokens',{'access_token':'live-access-token','refresh_token':'live-refresh','token_type':'Bearer','_expires_at':time.time()+3600})
        storage.write('discovery',{'metadata':{'issuer':'https://accounts.google.com','authorization_endpoint':'https://accounts.google.com/o/oauth2/v2/auth',
            'token_endpoint':'https://oauth2.googleapis.com/token','response_types_supported':['code']},'auth_server_url':'https://accounts.google.com'})
        self.manager.save(self.config)
        original=httpx2.AsyncClient
        def client(*args,**kwargs):return original(*args,transport=httpx2.MockTransport(self.google.handler),**kwargs)
        self.patch=patch('httpx2.AsyncClient',side_effect=client);self.patch.start()

    def tearDown(self):
        self.patch.stop();connections=list(self.manager.connections.values());self.manager.close()
        for c in connections:c.thread.join(5)
        self.store.close();self.temp.cleanup()

    def test_tools_are_buddys_own_and_calls_carry_the_signed_in_token(self):
        token=StopToken()
        names={t['name'] for t in self.manager.tools('google-gmail-x',token)}
        self.assertEqual(names,{t['name'] for t in google_native.GMAIL_TOOLS})
        self.assertEqual(self.manager.servers()[0]['status'],'connected')
        self.assertFalse(self.google.requests,'listing tools touches no Google service')
        result=self.manager.call('google-gmail-x','search_threads',{'query':'from:blackmagicdesign.com','pageSize':5},token)
        self.assertFalse(result['isError']);self.assertIn('DaVinci Resolve Studio',result['content'][0]['text'])
        for request in self.google.requests:
            self.assertEqual(request.headers['Authorization'],'Bearer live-access-token')
            self.assertTrue(str(request.url).startswith('https://gmail.googleapis.com/gmail/v1/users/me/'))
        with self.assertRaises(Exception):self.manager.call('google-gmail-x','search_threads',{'query':'x','pageSize':999},token)
        with self.assertRaises(MCPError):self.manager.read_resource('google-gmail-x','gmail://x',token)

    def test_sending_mail_is_an_action_that_asks_before_acting(self):
        asked=[]
        def approve(name,detail,token):asked.append(detail);return True
        registry=Registry(Policy(Path(self.temp.name),'execute',False,approve),self.store,mcp=self.manager)
        result=registry.execute('mcp.call',{'server':'google-gmail-x','name':'send_message','arguments':{'to':['ana@example.com'],'subject':'Hi','body':'Hello'}},StopToken())
        self.assertEqual(json.loads(result['content'][0]['text'])['sent'],True)
        self.assertTrue(asked and 'send_message' in json.dumps(asked))
        plan=Registry(Policy(Path(self.temp.name),'plan',False,approve),self.store,mcp=self.manager)
        from desktop_buddy.security import Denied
        with self.assertRaises(Denied):plan.execute('mcp.call',{'server':'google-gmail-x','name':'list_labels','arguments':{}},StopToken())


if __name__=='__main__':unittest.main()
