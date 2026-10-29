"""Small, explicit adapters for four documented wire protocols; seven provider presets."""
from __future__ import annotations
import base64
import copy
import json
import time
import uuid
from pathlib import Path
import requests
from .security import Cancelled, Denied, local_url
from . import __version__

class ProviderError(Exception):
    pass


class OutputLimitReached(ProviderError):
    """The model hit the configured response limit mid-reply. Partial text and
    tool calls were discarded, so nothing ran; the agent can retry in smaller steps."""
    def __init__(self,limit):
        self.limit=limit
        super().__init__(f'The reply was cut off at the response limit of {limit:,} tokens; nothing was executed.')


def sse(lines):
    """SSE framing: comments, multiline data, UTF-8, and final unterminated event."""
    data=[]
    for line in lines:
        if isinstance(line,bytes):
            line=line.decode('utf-8')
        if not line:
            if data:
                yield '\n'.join(data)
                data=[]
        elif line.startswith('data:'):
            data.append(line[5:].lstrip(' '))
    if data:
        yield '\n'.join(data)


def image_data(path):
    p=Path(path)
    if p.stat().st_size > 12*1024*1024:
        raise ProviderError('Image exceeds the 12 MiB input limit.')
    return base64.b64encode(p.read_bytes()).decode('ascii')


def function_tools(tools):
    return [{'type':'function','function':t} for t in tools]


def parse_arguments(raw):
    try:
        value=json.loads(raw) if isinstance(raw,str) else raw
        if not isinstance(value,dict):
            raise ValueError('Tool arguments must be an object.')
        return value
    except (ValueError,TypeError) as e:
        raise ProviderError('Model returned invalid/incomplete tool JSON; no action was executed.') from e

class Provider:
    def __init__(self,config,key='',redactor=None):
        self.config=config.validate()
        self.key=key
        self.redactor=redactor
        if redactor:
            redactor.register(key)
        self.session=requests.Session()
        # Never inherit ambient proxies, .netrc credentials, or redirect authorization.
        self.session.trust_env=False

    def check(self,local_only=False):
        if local_only and (not self.config.local or not local_url(self.config.base_url)):
            raise Denied('Local-only mode forbids this provider. Select LM Studio or Ollama on loopback.')
        # Authentication belongs to the configured endpoint. Local servers and
        # IT-managed gateways may accept requests without an application key.

    def headers(self):
        h={'Content-Type':'application/json','User-Agent':'DesktopBuddy/'+__version__}
        if self.config.protocol=='anthropic':
            h['anthropic-version']=self.config.api_version
            if self.config.workspace_id:h['anthropic-workspace-id']=self.config.workspace_id
        if self.key:
            h['Authorization']='Bearer '+self.key
        return h

    def request(self,path,token,body=None,stream=False,local_only=False):
        self.check(local_only)
        url=self.config.base_url.rstrip('/')+'/'+path.lstrip('/')
        for attempt in range(3):
            token.check()
            try:
                r=self.session.request('POST' if body is not None else 'GET',url,json=body,
                    headers=self.headers(),timeout=(5,30),stream=True,allow_redirects=False)
            except requests.RequestException as e:
                # POST may have been processed: never blindly retry ambiguous inference requests.
                raise ProviderError(f'Cannot reach {self.config.id}. Check its server, URL and network ({type(e).__name__}).') from e
            retryable=(429,502,503,504,529) if self.config.protocol=='anthropic' else (429,502,503,504)
            if r.status_code in retryable and attempt<2:
                delay=r.headers.get('Retry-After','')
                r.close()
                token.wait(min(float(delay),10) if delay.replace('.','',1).isdigit() else 2**attempt)
                continue
            if not 200 <= r.status_code < 300:
                code=r.status_code
                detail = ''
                try:
                    raw = next(r.iter_content(4096), b'')
                    error = json.loads(raw).get('error', {})
                    detail = error.get('message', '') if isinstance(error, dict) else str(error)
                    if self.redactor:detail = self.redactor.text(detail)
                    else:
                        from .security import Redactor
                        redactor = Redactor();redactor.register(self.key);detail = redactor.text(detail)
                except (ValueError, requests.RequestException):pass
                finally:r.close()
                guidance={401:'This model service requires valid credentials. IT can configure them in Settings → IT setup.',403:'Check model/account access.',404:'Check the base URL and model identifier.',400:'Check model capabilities and reasoning settings.',429:'Rate limit reached; try again later.'}.get(code,'The provider rejected the request.')
                raise ProviderError(f'{self.config.id}: HTTP {code}. {guidance}' + (' '+detail[:600] if detail else ''))
            try:token.check()
            except Cancelled:
                r.close()
                raise
            if stream:
                return r
            try:
                chunks=[]; total=0
                for chunk in r.iter_content(16384):
                    token.check();total+=len(chunk)
                    if total>8*1024*1024:
                        raise ProviderError('Provider response exceeded 8 MiB.')
                    chunks.append(chunk)
                return json.loads(b''.join(chunks))
            finally:
                r.close()
        raise ProviderError('Retry limit reached.')

    def models(self,token,local_only=False):
        self.check(local_only)
        from .inference import isolated_operation
        return isolated_operation(self.config,self.key,'models',token,local_only)

    def _models_http(self,token,local_only=False):
        if self.config.protocol=='ollama':
            data=self.request('/api/tags',token,local_only=local_only)
            return [{'id':m['name']} for m in data.get('models',[])]
        result=[]; after=None
        for _ in range(20):
            path='/models'+('?limit=1000'+('&after_id='+requests.utils.quote(after,safe='') if after else '') if self.config.protocol=='anthropic' else '')
            data=self.request(path,token,local_only=local_only)
            result.extend(data.get('data',[]))
            if not data.get('has_more'):
                break
            after=data.get('last_id')
            if not after:
                break
        return result

    def verify_local_model(self,token):
        if self.config.id=='ollama':
            if 'cloud' in self.config.model.lower():
                raise Denied('Ollama cloud models are forbidden in local-only mode.')
            from .inference import isolated_operation
            info=isolated_operation(self.config,self.key,'local_info',token,True)
            if info.get('remote_host') or info.get('remote_model') or info.get('details',{}).get('remote_host'):
                raise Denied('This Ollama model delegates inference remotely.')

    def payload(self,messages,tools):
        c=self.config
        meta_key=c.id+':'+c.model
        if c.protocol=='responses':
            inputs=[]
            for m in messages:
                if m['role']=='tool':
                    inputs.append({'type':'function_call_output','call_id':m['tool_call_id'],'output':m['content']})
                    continue
                native=m.get('native',{}).get(meta_key)
                if m['role']=='assistant' and isinstance(native,list) and native and all(isinstance(x,dict) and x.get('type') in ('message','reasoning','function_call') for x in native):
                    inputs.extend(native)
                    continue
                if m.get('content') or m.get('images'):
                    parts=[{'type':'output_text' if m['role']=='assistant' else 'input_text','text':m.get('content','')}]
                    parts.extend({'type':'input_image','image_url':'data:image/png;base64,'+image_data(p)} for p in m.get('images',[]))
                    inputs.append({'role':m['role'],'content':parts})
                for call in m.get('tool_calls',[]):
                    inputs.append({'type':'function_call','call_id':call['id'],'name':call['name'],'arguments':json.dumps(call['arguments'])})
            body={'model':c.model,'input':inputs,'stream':True,'store':False,'max_output_tokens':c.max_output}
            if tools:
                body['tools']=[{'type':'function',**t,'strict':False} for t in tools]
            if c.reasoning:
                body['reasoning']={'effort':c.reasoning}
            if c.id in ('openai','xai'):
                body['include']=['reasoning.encrypted_content']
            return '/responses',body
        if c.protocol=='anthropic':
            system='\n'.join(m['content'] for m in messages if m['role']=='system')
            result=[]
            for m in messages:
                if m['role']=='system':continue
                if m['role']=='tool':
                    role='user';blocks=[{'type':'tool_result','tool_use_id':m['tool_call_id'],'content':m['content'],'is_error':m.get('error',False)}]
                else:
                    role=m['role'];blocks=copy.deepcopy(m.get('native',{}).get(meta_key,[]))
                    if not isinstance(blocks,list):blocks=[]
                    if not blocks:
                        if m.get('content'):blocks.append({'type':'text','text':m['content']})
                        blocks.extend({'type':'image','source':{'type':'base64','media_type':'image/png','data':image_data(p)}} for p in m.get('images',[]))
                        blocks.extend({'type':'tool_use','id':a['id'],'name':a['name'],'input':a['arguments']} for a in m.get('tool_calls',[]))
                    if not blocks:blocks=[{'type':'text','text':'(empty response)'}]
                if result and result[-1]['role']==role:
                    result[-1]['content'].extend(blocks)
                else:result.append({'role':role,'content':blocks})
            body={'model':c.model,'system':system,'messages':result,'max_tokens':c.max_output,'stream':True}
            if tools:body['tools']=[{'name':t['name'],'description':t['description'],'input_schema':t['parameters']} for t in tools]
            if c.reasoning:
                body['thinking']={'type':'adaptive'} if c.reasoning=='adaptive' else {'type':'enabled','budget_tokens':int(c.reasoning)}
            return '/messages',body
        result=[]
        for m in messages:
            n={'role':m['role'],'content':m.get('content','')}
            if m.get('images'):
                if c.protocol=='ollama':n['images']=[image_data(p) for p in m['images']]
                else:n['content']=[{'type':'text','text':n['content']}]+[{'type':'image_url','image_url':{'url':'data:image/png;base64,'+image_data(p)}} for p in m['images']]
            if m.get('tool_calls'):
                n['tool_calls']=[{'id':a['id'],'type':'function','function':{'name':a['name'],'arguments':a['arguments'] if c.protocol=='ollama' else json.dumps(a['arguments'])}} for a in m['tool_calls']]
            if m['role']=='tool':
                n['tool_call_id']=m['tool_call_id']
                if c.protocol=='ollama':n['tool_name']=m.get('name','')
            metadata=m.get('native',{}).get(meta_key,{})
            if isinstance(metadata,dict):n.update({k:v for k,v in metadata.items() if k in ('reasoning_content','reasoning_details','thinking')})
            result.append(n)
        body={'model':c.model,'messages':result,'stream':True}
        if tools:body['tools']=function_tools(tools)
        if c.protocol=='ollama':
            body['options']={'num_ctx':c.context_tokens,'num_predict':c.max_output}
            if c.reasoning:body['think']={'true':True,'false':False}.get(c.reasoning,c.reasoning)
            return '/api/chat',body
        body['max_tokens']=c.max_output
        body['stream_options']={'include_usage':True}
        if c.id=='openrouter':body['usage']={'include':True}
        if c.reasoning:body['reasoning_effort']=c.reasoning
        return '/chat/completions',body

    def complete(self,messages,tools,token,emit=lambda *_:None,local_only=False):
        # Inference runs in a short-lived I/O subprocess so a stalled socket can be
        # cancelled immediately without leaving a Python worker blocked in TLS.
        if not self.config.model:
            raise ProviderError('Choose a model at the top of the conversation before starting.')
        if tools and not self.config.use_tools:
            raise ProviderError('Tool calling is not enabled for this model.')
        if any(m.get('images') for m in messages) and not self.config.use_vision:
            raise ProviderError('This model is not configured for vision.')
        self.check(local_only)
        if local_only:self.verify_local_model(token)
        from .inference import isolated_completion
        return isolated_completion(self.config,self.key,messages,tools,token,emit,local_only)

    def _complete_http(self,messages,tools,token,emit=lambda *_:None,local_only=False):
        if not self.config.model:
            raise ProviderError('Choose a model at the top of the conversation before starting.')
        if tools and not self.config.use_tools:
            raise ProviderError('Tool calling is not enabled for this model. Enable computer tools in Advanced settings.')
        if any(m.get('images') for m in messages) and not self.config.use_vision:
            raise ProviderError('Images are turned off for this model. Enable images in Advanced settings or choose another model.')
        path,body=self.payload(messages,tools)
        response=self.request(path,token,body,True,local_only)
        started=time.monotonic()
        try:
            if self.config.protocol=='ollama':
                events=(json.loads(line) for line in response.iter_lines(chunk_size=256) if line)
            else:
                events=(json.loads(data) for data in sse(response.iter_lines(chunk_size=256)) if data!='[DONE]')
            answer,usage=self.consume(events,token,emit)
            usage['elapsed_seconds']=round(time.monotonic()-started,3)
            if self.config.input_cost or self.config.output_cost:
                usage['estimated_cost_usd']=(usage.get('input_tokens',0)*self.config.input_cost+usage.get('output_tokens',0)*self.config.output_cost)/1000000
            return answer,usage
        except requests.RequestException as e:
            token.check()
            raise ProviderError('Inference stream interrupted. Partial tool calls were discarded; no actions were executed.') from e
        finally:
            response.close()

    def consume(self,events,token,emit):
        c=self.config; text='';calls={};blocks={};native={};usage={};finished=False;reasoning='';reasoning_details=[]
        key=c.id+':'+c.model
        event_bytes=0
        for event in events:
            event_bytes+=len(json.dumps(event).encode())
            if event_bytes>16*1024*1024:raise ProviderError('Provider stream exceeded 16 MiB.')
            token.check()
            if event.get('error'):
                raise ProviderError('Provider reported a streaming error. Check model capabilities and service status.')
            delta=''
            if c.protocol=='responses':
                typ=event.get('type','')
                if typ=='response.output_text.delta':delta=event.get('delta','')
                if typ=='response.incomplete' and event.get('response',{}).get('incomplete_details',{}).get('reason')=='max_output_tokens':
                    raise OutputLimitReached(c.max_output)
                if typ in ('response.failed','response.incomplete','error'):
                    raise ProviderError('Provider did not complete the response. Increase output limit or check model/service status.')
                if typ=='response.completed':
                    response=event['response'];output=response.get('output',[])
                    native[key]=output
                    text=''.join(p.get('text','') for item in output if item.get('type')=='message' for p in item.get('content',[]))
                    for item in output:
                        if item.get('type')=='function_call':
                            calls[item['call_id']]={'id':item['call_id'],'name':item['name'],'arguments':parse_arguments(item['arguments'])}
                    usage=response.get('usage',{});finished=True
            elif c.protocol=='anthropic':
                typ=event.get('type','')
                if typ=='message_start':usage.update(event['message'].get('usage',{}))
                elif typ=='content_block_start':blocks[event['index']]=copy.deepcopy(event['content_block'])
                elif typ=='content_block_delta':
                    b=blocks[event['index']];d=event['delta'];dt=d['type']
                    if dt=='text_delta':delta=d['text'];b['text']=b.get('text','')+delta
                    elif dt=='input_json_delta':b['_json']=b.get('_json','')+d['partial_json']
                    elif dt=='thinking_delta':b['thinking']=b.get('thinking','')+d['thinking']
                    elif dt=='signature_delta':b['signature']=b.get('signature','')+d['signature']
                elif typ=='message_delta':
                    usage.update(event.get('usage',{}))
                    stop=event.get('delta',{}).get('stop_reason')
                    if stop=='max_tokens':raise OutputLimitReached(c.max_output)
                    if stop=='refusal':raise ProviderError('Claude declined this request; nothing was executed.')
                elif typ=='message_stop':finished=True
            elif c.protocol=='ollama':
                m=event.get('message',{});delta=m.get('content','');reasoning+=m.get('thinking','')
                for tc in m.get('tool_calls',[]):
                    cid=tc.get('id') or uuid.uuid4().hex
                    calls[cid]={'id':cid,'name':tc['function']['name'],'arguments':parse_arguments(tc['function']['arguments'])}
                if event.get('done'):
                    if event.get('done_reason')=='length':raise OutputLimitReached(c.max_output)
                    usage={'input_tokens':event.get('prompt_eval_count',0),'output_tokens':event.get('eval_count',0)};finished=True
            else:
                if event.get('usage'):
                    u=event['usage'];usage={'input_tokens':u.get('prompt_tokens',0),'output_tokens':u.get('completion_tokens',0)}
                    if u.get('cost') is not None:usage['reported_cost_usd']=u['cost']
                for choice in event.get('choices',[]):
                    if choice.get('index',0)!=0:continue
                    d=choice.get('delta',{});delta=d.get('content') or '';reasoning+=d.get('reasoning_content') or ''
                    reasoning_details.extend(d.get('reasoning_details') or [])
                    for tc in d.get('tool_calls',[]):
                        call=calls.setdefault(tc['index'],{'id':'','name':'','arguments':''})
                        if tc.get('id'):call['id']+=tc['id']
                        f=tc.get('function',{});call['name']+=f.get('name','');call['arguments']+=f.get('arguments','')
                    stop=choice.get('finish_reason')
                    if stop=='length':raise OutputLimitReached(c.max_output)
                    if stop=='content_filter':raise ProviderError('Model output was filtered by the service; partial actions discarded.')
                    if stop:finished=True
            if delta:
                text+=delta
                if len(text)>2*1024*1024:raise ProviderError('Model text exceeded 2 MiB.')
                emit('stream',text)
        if not finished:
            raise ProviderError('Stream ended without a completion marker. No partial tool call was executed.')
        if c.protocol=='anthropic':
            for b in blocks.values():
                if b['type']=='tool_use':
                    if '_json' in b:b['input']=parse_arguments(b.pop('_json'))
                    calls[b['id']]={'id':b['id'],'name':b['name'],'arguments':b['input']}
            native[key]=list(blocks.values())
        elif c.protocol=='chat':
            for a in calls.values():a['arguments']=parse_arguments(a['arguments'])
            native[key]={}
            if reasoning:native[key]['reasoning_content']=reasoning
            if reasoning_details:native[key]['reasoning_details']=reasoning_details
        elif c.protocol=='ollama' and reasoning:native[key]={'thinking':reasoning}
        if c.protocol=='anthropic':
            text=''.join(b.get('text','') for b in blocks.values() if b.get('type')=='text')
        call_list=list(calls.values())
        if len(call_list)>16 or any(not a['id'] or not a['name'] for a in call_list):
            raise ProviderError('Invalid or excessive tool calls.')
        return {'role':'assistant','content':text,'tool_calls':call_list,'native':native},usage
