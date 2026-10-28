"""Buddy's own Gmail and Calendar tools on Google's regular, generally available APIs.

Google's official MCP servers for Workspace are a developer preview that Google
only lets enrolled organisations use with their own people. Mail and calendar
are the two things nearly every customer asks for, so Buddy serves them itself:
the person signs in once through the normal Connect button (the same OAuth
client and the same token), and the assistant sees a small, fixed set of tools
with the same names and shapes it would see on Google's MCP server.

Everything here is plain HTTPS against gmail.googleapis.com and
www.googleapis.com/calendar; the HTTP client carries the OAuth token and
refreshes it. Results are untrusted data for the assistant, exactly like any
other connected app.
"""
import asyncio
import base64
from email.message import EmailMessage
from email.utils import parseaddr
import html
from html.parser import HTMLParser
import json
import re

GMAIL_API = 'https://gmail.googleapis.com/gmail/v1/users/me'
CALENDAR_API = 'https://www.googleapis.com/calendar/v3'
NATIVE_SERVICES = ('gmail', 'calendar')
BODY_LIMIT = 20000      # characters of message text handed to the assistant
PAGE_LIMIT = 25         # threads or events per call


def native_service(url):
    """'gmail' or 'calendar' when Buddy serves this connection itself, else ''."""
    from .mcp_catalog import GOOGLE_MCP_ENDPOINTS
    for service in NATIVE_SERVICES:
        if url and url.rstrip('/') == GOOGLE_MCP_ENDPOINTS[service].rstrip('/'):
            return service
    return ''


def _schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


S = {'type': 'string'}
ADDRESSES = {'type': 'array', 'items': S, 'description': 'Email addresses.'}
MESSAGE_FIELDS = {
    'to': ADDRESSES, 'cc': ADDRESSES, 'bcc': ADDRESSES,
    'subject': S,
    'body': {'type': 'string', 'description': 'Plain text body. Do not use Markdown.'},
    'htmlBody': {'type': 'string', 'description': 'Optional HTML version of the body.'},
    'replyToMessageId': {'type': 'string', 'description': 'ID of the message being replied to; keeps the thread and subject.'},
}

GMAIL_TOOLS = [
    {'name': 'search_threads',
     'description': 'Search the mailbox with Gmail search syntax (for example from:, subject:, newer_than:7d, has:attachment, in:anywhere). Returns conversations with subject, sender, date and snippet. Use get_thread to read one.',
     'inputSchema': _schema({'query': S, 'pageSize': {'type': 'integer', 'minimum': 1, 'maximum': PAGE_LIMIT},
                             'pageToken': S}, ['query'])},
    {'name': 'get_thread',
     'description': 'Read a whole conversation: every message with sender, recipients, date, subject, text and attachment names.',
     'inputSchema': _schema({'threadId': S}, ['threadId'])},
    {'name': 'get_message',
     'description': 'Read one message by its ID, including its text and attachment names.',
     'inputSchema': _schema({'messageId': S}, ['messageId'])},
    {'name': 'list_labels', 'description': 'List the mailbox labels (folders) with their IDs.', 'inputSchema': _schema({})},
    {'name': 'create_draft',
     'description': 'Create a draft email (not sent). Returns the draft ID and a link to open it in Gmail.',
     'inputSchema': _schema(MESSAGE_FIELDS, ['to', 'subject', 'body'])},
    {'name': 'send_message',
     'description': 'Send an email now. Returns the sent message ID and thread ID.',
     'inputSchema': _schema(MESSAGE_FIELDS, ['to', 'subject', 'body'])},
    {'name': 'modify_labels',
     'description': 'Add or remove labels on a conversation. Use label IDs from list_labels (for example UNREAD, STARRED, INBOX).',
     'inputSchema': _schema({'threadId': S, 'addLabelIds': {'type': 'array', 'items': S},
                             'removeLabelIds': {'type': 'array', 'items': S}}, ['threadId'])},
    {'name': 'trash_thread', 'description': 'Move a conversation to the bin.', 'inputSchema': _schema({'threadId': S}, ['threadId'])},
]

EVENT_FIELDS = {
    'calendarId': {'type': 'string', 'description': 'Calendar ID from list_calendars; "primary" is the person’s main calendar.'},
    'summary': {'type': 'string', 'description': 'Event title.'},
    'description': S, 'location': S,
    'start': {'type': 'string', 'description': 'RFC 3339 date-time (2026-11-03T14:00:00+01:00) or a date (2026-11-03) for an all-day event.'},
    'end': {'type': 'string', 'description': 'Same form as start. All-day end dates are exclusive.'},
    'timeZone': {'type': 'string', 'description': 'IANA zone such as Europe/Amsterdam when start/end carry no offset.'},
    'attendees': {'type': 'array', 'items': S, 'description': 'Attendee email addresses.'},
}

CALENDAR_TOOLS = [
    {'name': 'list_calendars', 'description': 'List the person’s calendars with their IDs and time zones.', 'inputSchema': _schema({})},
    {'name': 'list_events',
     'description': 'List events in a time range, oldest first, with recurring events expanded. Optional text search.',
     'inputSchema': _schema({'calendarId': EVENT_FIELDS['calendarId'],
                             'timeMin': {'type': 'string', 'description': 'RFC 3339 lower bound (inclusive).'},
                             'timeMax': {'type': 'string', 'description': 'RFC 3339 upper bound (exclusive).'},
                             'query': S, 'maxResults': {'type': 'integer', 'minimum': 1, 'maximum': PAGE_LIMIT},
                             'pageToken': S}, ['timeMin', 'timeMax'])},
    {'name': 'get_event', 'description': 'Read one event.', 'inputSchema': _schema({'calendarId': EVENT_FIELDS['calendarId'], 'eventId': S}, ['eventId'])},
    {'name': 'create_event',
     'description': 'Create an event and invite attendees (they receive the invitation email).',
     'inputSchema': _schema(EVENT_FIELDS, ['summary', 'start', 'end'])},
    {'name': 'update_event',
     'description': 'Change an existing event. Only the given fields change.',
     'inputSchema': _schema({'eventId': S, **EVENT_FIELDS}, ['eventId'])},
    {'name': 'delete_event', 'description': 'Delete an event and notify attendees.',
     'inputSchema': _schema({'calendarId': EVENT_FIELDS['calendarId'], 'eventId': S}, ['eventId'])},
    {'name': 'respond_to_event',
     'description': 'Accept, decline or tentatively accept an invitation on behalf of the person.',
     'inputSchema': _schema({'calendarId': EVENT_FIELDS['calendarId'], 'eventId': S,
                             'response': {'type': 'string', 'enum': ['accepted', 'declined', 'tentative']}}, ['eventId', 'response'])},
]

TOOLS = {'gmail': GMAIL_TOOLS, 'calendar': CALENDAR_TOOLS}


class GoogleAPIError(Exception):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','head'):self.skip+=1
        elif tag in ('br','p','div','tr','li','h1','h2','h3','h4','table'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','head') and self.skip:self.skip-=1
        elif tag in ('p','div','tr','li','h1','h2','h3','h4'):self.parts.append('\n')
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)


def html_to_text(markup):
    parser=_TextExtractor()
    try:parser.feed(markup);parser.close()
    except Exception:return html.unescape(re.sub(r'<[^>]+>',' ',markup))
    text=''.join(parser.parts)
    return re.sub(r'\n\s*\n+','\n\n',re.sub(r'[ \t]+',' ',text)).strip()


def _b64url_decode(data):
    return base64.urlsafe_b64decode(data+'='*(-len(data)%4))


def _decode_part(part):
    data=part.get('body',{}).get('data')
    if not data:return ''
    charset='utf-8'
    for header in part.get('headers',[]):
        if header['name'].lower()=='content-type':
            match=re.search(r'charset="?([\w.-]+)"?',header['value'],re.I)
            if match:charset=match.group(1)
    try:return _b64url_decode(data).decode(charset,errors='replace')
    except LookupError:return _b64url_decode(data).decode('utf-8',errors='replace')


def message_text(payload):
    """Plain text of a Gmail message payload, preferring text/plain, plus attachment names."""
    plain=[];rich=[];attachments=[]
    def walk(part):
        mime=part.get('mimeType','')
        filename=part.get('filename')
        if filename:
            body=part.get('body',{})
            attachments.append({'filename':filename,'mimeType':mime,'size':body.get('size',0),'attachmentId':body.get('attachmentId','')})
            return
        if mime=='text/plain':plain.append(_decode_part(part))
        elif mime=='text/html':rich.append(_decode_part(part))
        for child in part.get('parts',[]):walk(child)
    walk(payload)
    text=('\n'.join(p for p in plain if p.strip()) or html_to_text('\n'.join(rich))).strip()
    truncated=len(text)>BODY_LIMIT
    return (text[:BODY_LIMIT]+'\n[… message text shortened]' if truncated else text),attachments


def _headers(payload):
    return {h['name'].lower():h['value'] for h in payload.get('headers',[])}


def summarize_message(message):
    headers=_headers(message.get('payload',{}))
    text,attachments=message_text(message.get('payload',{}))
    return {'id':message.get('id'),'threadId':message.get('threadId'),'from':headers.get('from',''),
            'to':headers.get('to',''),'cc':headers.get('cc',''),'date':headers.get('date',''),
            'subject':headers.get('subject',''),'labelIds':message.get('labelIds',[]),
            'snippet':html.unescape(message.get('snippet','')),'text':text,'attachments':attachments,
            'viewUrl':'https://mail.google.com/mail/u/0/#all/'+str(message.get('id',''))}


def build_mime(arguments, original=None):
    message=EmailMessage()
    for field in ('to','cc','bcc'):
        if arguments.get(field):message[field.capitalize() if field!='cc' else 'Cc']=', '.join(arguments[field])
    subject=arguments.get('subject','')
    if original:
        headers=_headers(original.get('payload',{}))
        if headers.get('message-id'):
            message['In-Reply-To']=headers['message-id']
            message['References']=(headers.get('references','')+' '+headers['message-id']).strip()
        if not subject and headers.get('subject'):
            subject=headers['subject'] if headers['subject'].lower().startswith('re:') else 'Re: '+headers['subject']
    message['Subject']=subject
    message.set_content(arguments.get('body',''))
    if arguments.get('htmlBody'):message.add_alternative(arguments['htmlBody'],subtype='html')
    return base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip('=')


class GoogleNative:
    """Serves Gmail or Calendar tool calls through Buddy's own code."""
    def __init__(self, service, http):
        self.service=service;self.http=http

    async def operation(self, method, args):
        if method=='tools':return [dict(t) for t in TOOLS[self.service]]
        if method=='resources':return {'resources':[]}
        if method=='read_resource':raise GoogleAPIError('This connection has no readable resources; use its tools.')
        if method!='call':raise GoogleAPIError('Unknown MCP operation.')
        try:
            handler=getattr(self,self.service+'_'+args['name'],None)
            if handler is None:raise GoogleAPIError('Tool not found on this connection. Refresh its tool list.')
            result=await handler(dict(args.get('arguments',{})))
            return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}],'isError':False}
        except GoogleAPIError as error:
            return {'content':[{'type':'text','text':str(error)}],'isError':True}

    async def request(self, method, url, **kwargs):
        response=await self.http.request(method,url,**kwargs)
        try:
            if response.status_code==204:return {}
            body=None
            try:body=response.json()
            except ValueError:pass
            if response.status_code>=400:
                detail=(body or {}).get('error',{}) if isinstance(body,dict) else {}
                text=detail.get('message','') if isinstance(detail,dict) else str(detail)
                if response.status_code in (401,403) and ('insufficient' in text.lower() or 'credential' in text.lower() or response.status_code==401):
                    raise GoogleAPIError('Google no longer accepts this sign-in. Open Connect apps and choose Connect on this app to sign in again.')
                raise GoogleAPIError('Google answered: '+(text or f'HTTP {response.status_code}'))
            return body if body is not None else {}
        finally:await response.aclose()

    # ---- Gmail -------------------------------------------------------------
    async def gmail_search_threads(self, a):
        size=min(int(a.get('pageSize',10)),PAGE_LIMIT)
        params={'q':a['query'],'maxResults':size}
        if a.get('pageToken'):params['pageToken']=a['pageToken']
        listing=await self.request('GET',GMAIL_API+'/threads',params=params)
        async def summary(thread):
            full=await self.request('GET',GMAIL_API+'/threads/'+thread['id'],
                params={'format':'metadata','metadataHeaders':['From','To','Subject','Date']})
            messages=full.get('messages',[])
            first=_headers(messages[0].get('payload',{})) if messages else {}
            last=_headers(messages[-1].get('payload',{})) if messages else {}
            return {'threadId':thread['id'],'subject':first.get('subject',''),'from':first.get('from',''),
                    'lastFrom':last.get('from',''),'date':last.get('date',''),'messageCount':len(messages),
                    'snippet':html.unescape(thread.get('snippet','')),'labelIds':messages[-1].get('labelIds',[]) if messages else [],
                    'viewUrl':'https://mail.google.com/mail/u/0/#all/'+thread['id']}
        threads=await asyncio.gather(*(summary(t) for t in listing.get('threads',[])))
        return {'threads':list(threads),'nextPageToken':listing.get('nextPageToken'),
                'resultSizeEstimate':listing.get('resultSizeEstimate')}

    async def gmail_get_thread(self, a):
        full=await self.request('GET',GMAIL_API+'/threads/'+a['threadId'],params={'format':'full'})
        return {'threadId':full.get('id'),'messages':[summarize_message(m) for m in full.get('messages',[])]}

    async def gmail_get_message(self, a):
        return summarize_message(await self.request('GET',GMAIL_API+'/messages/'+a['messageId'],params={'format':'full'}))

    async def gmail_list_labels(self, a):
        labels=await self.request('GET',GMAIL_API+'/labels')
        return {'labels':[{'id':l['id'],'name':l['name'],'type':l.get('type','')} for l in labels.get('labels',[])]}

    async def _outgoing(self, a):
        original=None
        if a.get('replyToMessageId'):
            original=await self.request('GET',GMAIL_API+'/messages/'+a['replyToMessageId'],params={'format':'metadata','metadataHeaders':['Subject','Message-ID','References']})
        payload={'raw':build_mime(a,original)}
        if original:payload['threadId']=original.get('threadId')
        return payload

    async def gmail_create_draft(self, a):
        draft=await self.request('POST',GMAIL_API+'/drafts',json={'message':await self._outgoing(a)})
        return {'draftId':draft.get('id'),'messageId':draft.get('message',{}).get('id'),
                'viewUrl':'https://mail.google.com/mail/u/0/#drafts/'+str(draft.get('message',{}).get('id',''))}

    async def gmail_send_message(self, a):
        sent=await self.request('POST',GMAIL_API+'/messages/send',json=await self._outgoing(a))
        return {'messageId':sent.get('id'),'threadId':sent.get('threadId'),'sent':True}

    async def gmail_modify_labels(self, a):
        result=await self.request('POST',GMAIL_API+'/threads/'+a['threadId']+'/modify',
            json={'addLabelIds':a.get('addLabelIds',[]),'removeLabelIds':a.get('removeLabelIds',[])})
        return {'threadId':result.get('id'),'labelIds':sorted({l for m in result.get('messages',[]) for l in m.get('labelIds',[])})}

    async def gmail_trash_thread(self, a):
        result=await self.request('POST',GMAIL_API+'/threads/'+a['threadId']+'/trash')
        return {'threadId':result.get('id'),'trashed':True}

    # ---- Calendar ----------------------------------------------------------
    @staticmethod
    def _when(value, zone):
        if value is None:return None
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):return {'date':value}
        when={'dateTime':value}
        if zone:when['timeZone']=zone
        return when

    @staticmethod
    def _event(event):
        return {'id':event.get('id'),'summary':event.get('summary',''),'description':event.get('description',''),
                'location':event.get('location',''),'start':event.get('start',{}),'end':event.get('end',{}),
                'status':event.get('status',''),'organizer':event.get('organizer',{}).get('email',''),
                'attendees':[{'email':p.get('email',''),'responseStatus':p.get('responseStatus',''),'self':p.get('self',False)} for p in event.get('attendees',[])],
                'htmlLink':event.get('htmlLink',''),'hangoutLink':event.get('hangoutLink','')}

    def _calendar(self, a):
        return CALENDAR_API+'/calendars/'+(a.get('calendarId') or 'primary')+'/events'

    async def calendar_list_calendars(self, a):
        listing=await self.request('GET',CALENDAR_API+'/users/me/calendarList')
        return {'calendars':[{'id':c['id'],'summary':c.get('summary',''),'primary':c.get('primary',False),
                              'timeZone':c.get('timeZone',''),'accessRole':c.get('accessRole','')} for c in listing.get('items',[])]}

    async def calendar_list_events(self, a):
        params={'timeMin':a['timeMin'],'timeMax':a['timeMax'],'singleEvents':'true','orderBy':'startTime',
                'maxResults':min(int(a.get('maxResults',PAGE_LIMIT)),PAGE_LIMIT)}
        if a.get('query'):params['q']=a['query']
        if a.get('pageToken'):params['pageToken']=a['pageToken']
        listing=await self.request('GET',self._calendar(a),params=params)
        return {'events':[self._event(e) for e in listing.get('items',[])],'nextPageToken':listing.get('nextPageToken'),
                'timeZone':listing.get('timeZone','')}

    async def calendar_get_event(self, a):
        return self._event(await self.request('GET',self._calendar(a)+'/'+a['eventId']))

    def _event_body(self, a):
        body={}
        for field in ('summary','description','location'):
            if field in a:body[field]=a[field]
        zone=a.get('timeZone')
        if 'start' in a:body['start']=self._when(a['start'],zone)
        if 'end' in a:body['end']=self._when(a['end'],zone)
        if 'attendees' in a:body['attendees']=[{'email':e} for e in a['attendees']]
        return body

    async def calendar_create_event(self, a):
        created=await self.request('POST',self._calendar(a),params={'sendUpdates':'all'},json=self._event_body(a))
        return self._event(created)

    async def calendar_update_event(self, a):
        updated=await self.request('PATCH',self._calendar(a)+'/'+a['eventId'],params={'sendUpdates':'all'},json=self._event_body(a))
        return self._event(updated)

    async def calendar_delete_event(self, a):
        await self.request('DELETE',self._calendar(a)+'/'+a['eventId'],params={'sendUpdates':'all'})
        return {'eventId':a['eventId'],'deleted':True}

    async def calendar_respond_to_event(self, a):
        event=await self.request('GET',self._calendar(a)+'/'+a['eventId'])
        attendees=event.get('attendees',[])
        mine=[p for p in attendees if p.get('self')]
        if not mine:raise GoogleAPIError('The person is not an invited attendee of this event, so there is nothing to respond to.')
        for p in mine:p['responseStatus']=a['response']
        updated=await self.request('PATCH',self._calendar(a)+'/'+a['eventId'],params={'sendUpdates':'all'},json={'attendees':attendees})
        return self._event(updated)
