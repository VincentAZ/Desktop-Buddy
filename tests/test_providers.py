import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch
from desktop_buddy.providers import Provider,ProviderError,sse
from desktop_buddy.security import StopToken,Denied
from desktop_buddy.state import ProviderConfig,PROVIDERS
from desktop_buddy.agent import ENVELOPES

CALL={'id':'call_1','name':'run_tool','arguments':{'name':'fs.read','arguments':{'path':'test.txt'}}}

class ProviderProtocolTests(unittest.TestCase):
    def provider(self,pid='openai',**kwargs):
        name,url,protocol,local=PROVIDERS[pid]
        return Provider(ProviderConfig(pid,url,protocol,local,model='test-model',tools=True,**kwargs),'synthetic-test-key')
    def test_multiline_sse_comments_and_eof(self):
        self.assertEqual(list(sse([b': ping',b'data: {',b'data: "ok":true}',b'',b'data: [DONE]'])),['{\n"ok":true}','[DONE]'])
    def test_responses_function_call_and_encrypted_reasoning_replay(self):
        p=self.provider();items=[{'type':'reasoning','id':'rs_1','encrypted_content':'opaque','summary':[]},{'type':'function_call','call_id':CALL['id'],'name':CALL['name'],'arguments':json.dumps(CALL['arguments'])}]
        answer,u=p.consume([{'type':'response.completed','response':{'output':items,'usage':{'input_tokens':10,'output_tokens':20}}}],StopToken(),lambda *_:None)
        self.assertEqual(answer['tool_calls'],[CALL]);self.assertEqual(u['input_tokens'],10)
        path,payload=p.payload([answer,{'role':'tool','tool_call_id':CALL['id'],'content':'test'}],ENVELOPES)
        self.assertEqual(payload['input'][0]['encrypted_content'],'opaque');self.assertEqual(payload['input'][-1]['type'],'function_call_output')

    def test_stateless_responses_request_encrypted_reasoning_for_openai_and_xai(self):
        for provider_id in ('openai','xai'):
            with self.subTest(provider_id=provider_id):
                _, payload = self.provider(provider_id).payload([{'role':'user','content':'test'}], ENVELOPES)
                self.assertFalse(payload['store'])
                self.assertEqual(payload['include'], ['reasoning.encrypted_content'])
    def test_openrouter_requests_reported_usage(self):
        _, payload = self.provider('openrouter').payload([{'role':'user','content':'test'}], ENVELOPES)
        self.assertEqual(payload['usage'], {'include': True})
    def test_response_incomplete_never_executes_partial_tools(self):
        with self.assertRaises(ProviderError):self.provider().consume([{'type':'response.incomplete','response':{}}],StopToken(),lambda *_:None)
    def test_chat_fragmented_arguments_and_reasoning(self):
        p=self.provider('moonshot')
        events=[{'choices':[{'index':0,'delta':{'reasoning_content':'preserve thinking','tool_calls':[{'index':0,'id':'call_1','function':{'name':'run_tool','arguments':'{"name":"fs.read",'}}]}}]},
                {'choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"arguments":{"path":"test.txt"}}'}}]},'finish_reason':'tool_calls'}]},
                {'choices':[],'usage':{'prompt_tokens':13,'completion_tokens':7}}]
        answer,u=p.consume(events,StopToken(),lambda *_:None);self.assertEqual(answer['tool_calls'],[CALL]);self.assertEqual(u['input_tokens'],13)
        _,body=p.payload([answer],ENVELOPES);self.assertEqual(body['messages'][0]['reasoning_content'],'preserve thinking')
    def test_chat_invalid_json_fails_closed(self):
        events=[{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'c','function':{'name':'run_tool','arguments':'{bad'}}]},'finish_reason':'tool_calls'}]}]
        with self.assertRaises(ProviderError):self.provider('lmstudio').consume(events,StopToken(),lambda *_:None)
    def test_truncated_chat_fails_closed(self):
        with self.assertRaises(ProviderError):self.provider('openrouter').consume([{'choices':[{'delta':{},'finish_reason':'length'}]}],StopToken(),lambda *_:None)
    def test_response_limit_is_reported_separately_from_refusals(self):
        from desktop_buddy.providers import OutputLimitReached
        p=self.provider('anthropic');p.config.max_output=4096
        start=[{'type':'message_start','message':{'usage':{'input_tokens':2}}},{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}}]
        with self.assertRaises(OutputLimitReached) as cut:
            p.consume(start+[{'type':'message_delta','delta':{'stop_reason':'max_tokens'},'usage':{'output_tokens':4096}}],StopToken(),lambda *_:None)
        self.assertEqual(cut.exception.limit,4096);self.assertIn('response limit of 4,096 tokens',str(cut.exception))
        with self.assertRaises(ProviderError) as refused:
            p.consume(start+[{'type':'message_delta','delta':{'stop_reason':'refusal'},'usage':{}}],StopToken(),lambda *_:None)
        self.assertNotIsInstance(refused.exception,OutputLimitReached);self.assertIn('declined',str(refused.exception))
        with self.assertRaises(OutputLimitReached):
            self.provider('openrouter').consume([{'choices':[{'delta':{},'finish_reason':'length'}]}],StopToken(),lambda *_:None)
        with self.assertRaises(OutputLimitReached):
            self.provider('ollama').consume([{'message':{'content':'x'},'done':True,'done_reason':'length'}],StopToken(),lambda *_:None)
        with self.assertRaises(OutputLimitReached):
            self.provider('openai').consume([{'type':'response.incomplete','response':{'incomplete_details':{'reason':'max_output_tokens'}}}],StopToken(),lambda *_:None)
    def test_anthropic_signed_thinking_and_tool_result_blocks(self):
        p=self.provider('anthropic')
        events=[{'type':'message_start','message':{'usage':{'input_tokens':11}}},
            {'type':'content_block_start','index':0,'content_block':{'type':'thinking','thinking':'','signature':''}},
            {'type':'content_block_delta','index':0,'delta':{'type':'thinking_delta','thinking':'opaque thought'}},
            {'type':'content_block_delta','index':0,'delta':{'type':'signature_delta','signature':'signed'}},
            {'type':'content_block_start','index':1,'content_block':{'type':'tool_use','id':'call_1','name':'run_tool','input':{}}},
            {'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':json.dumps(CALL['arguments'])}},
            {'type':'message_delta','delta':{'stop_reason':'tool_use'},'usage':{'output_tokens':8}}, {'type':'message_stop'}]
        answer,u=p.consume(events,StopToken(),lambda *_:None);self.assertEqual(answer['tool_calls'],[CALL]);self.assertEqual(u['output_tokens'],8)
        _,body=p.payload([{'role':'system','content':'system'},answer,{'role':'tool','tool_call_id':'call_1','content':'result'}],ENVELOPES)
        self.assertEqual(body['messages'][0]['content'][0]['signature'],'signed');self.assertEqual(body['messages'][1]['content'][0]['type'],'tool_result')
    def test_ollama_ndjson_tools_thinking_and_usage(self):
        p=self.provider('ollama')
        events=[{'message':{'thinking':'reason','tool_calls':[{'id':'call_1','function':{'name':'run_tool','arguments':CALL['arguments']}}]},'done':False},
                {'message':{'content':''},'done':True,'prompt_eval_count':8,'eval_count':4}]
        a,u=p.consume(events,StopToken(),lambda *_:None);self.assertEqual(a['tool_calls'],[CALL]);self.assertEqual(u['output_tokens'],4)
        _,body=p.payload([a],ENVELOPES);self.assertEqual(body['messages'][0]['thinking'],'reason')
    def test_all_protocols_require_terminal_event(self):
        for pid in ('openai','anthropic','moonshot','ollama'):
            with self.subTest(pid=pid),self.assertRaises(ProviderError):self.provider(pid).consume([],StopToken(),lambda *_:None)
    def test_switch_does_not_forward_other_provider_reasoning(self):
        p=self.provider('lmstudio');m={'role':'assistant','content':'hello','native':{'moonshot:other-model':{'reasoning_content':'provider-private'}}}
        _,body=p.payload([m],[]);self.assertNotIn('reasoning_content',body['messages'][0])
    def test_missing_vision_or_tools_errors_before_network(self):
        p=self.provider('lmstudio');p.config.tools=False;p.config.vision=False
        with self.assertRaises(ProviderError):p.complete([{'role':'user','content':'hi'}],ENVELOPES,StopToken())
        with self.assertRaises(ProviderError):p.complete([{'role':'user','content':'look','images':['missing.png']}],[],StopToken())
    def test_ollama_cloud_is_rejected(self):
        p=self.provider('ollama');p.config.model='some-model:cloud'
        with self.assertRaises(Denied):p.verify_local_model(StopToken())
    def test_local_endpoint_refuses_remote_dns(self):
        with self.assertRaises(Denied):ProviderConfig('lmstudio','https://remote.example/v1','chat',True).validate()
    def test_cloud_calls_denied_by_privacy(self):
        with self.assertRaises(Denied):self.provider('openai').check(True)

class FixtureServer(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    requests=[]
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.endswith('/requires-key'):
            self.send_response(401);self.send_header('Content-Length','0');self.end_headers();return
        if self.path.endswith('/auth-echo'):
            self.reply(json.dumps({'authorization':self.headers.get('Authorization')}).encode(),'application/json');return
        if self.path.endswith('/redirect'):
            self.send_response(302);self.send_header('Location','https://example.com');self.send_header('Content-Length','0');self.end_headers();return
        self.reply(json.dumps({'models':[{'name':'fixture'}]} if self.path.endswith('/api/tags') else {'data':[{'id':'fixture'}]}).encode(),'application/json')
    def reply(self,data,ctype):
        self.send_response(200);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.requests.append((self.path,body))
        if self.path.endswith('/api/show'):
            self.reply(json.dumps({'capabilities':['completion','tools']}).encode(),'application/json');return
        if self.path.endswith('/responses'):
            events=[{'type':'response.output_text.delta','delta':'fixture ok'},{'type':'response.completed','response':{'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'fixture ok'}]}],'usage':{'input_tokens':2,'output_tokens':3}}}]
        elif self.path.endswith('/messages'):
            events=[{'type':'message_start','message':{'usage':{'input_tokens':2}}},{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'fixture ok'}},{'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':3}},{'type':'message_stop'}]
        elif self.path.endswith('/api/chat'):
            self.reply((json.dumps({'message':{'content':'fixture ok'},'done':True,'prompt_eval_count':2,'eval_count':3})+'\n').encode(),'application/x-ndjson');return
        else:
            events=[{'choices':[{'index':0,'delta':{'content':'fixture ok'},'finish_reason':'stop'}],'usage':{'prompt_tokens':2,'completion_tokens':3}}]
        data=': fixture heartbeat\n\n'+''.join('data: '+json.dumps(e)+'\n\n' for e in events)
        self.reply(data.encode(),'text/event-stream')

class HTTPIntegrationTests(unittest.TestCase):
    def test_keyless_model_connections_and_inference(self):
        for pid,(_,_,protocol,local) in PROVIDERS.items():
            with self.subTest(provider=pid):
                provider=Provider(ProviderConfig(pid,self.url,protocol,local,model='fixture'))
                self.assertIsNone(provider.request('/auth-echo',StopToken())['authorization'])
                self.assertEqual(provider.models(StopToken())[0]['id'],'fixture')
                answer,_=provider.complete([{'role':'user','content':'hello'}],[],StopToken())
                self.assertEqual(answer['content'],'fixture ok')
                if not local:
                    with self.assertRaises(Denied):provider.check(True)

    def test_endpoint_authentication_is_still_enforced(self):
        provider=Provider(ProviderConfig('openai',self.url,'responses',False))
        with self.assertRaisesRegex(ProviderError,'HTTP 401.*IT setup'):
            provider.request('/requires-key',StopToken())
        provider.key='fixture-key'
        self.assertEqual(provider.request('/auth-echo',StopToken())['authorization'],'Bearer fixture-key')

    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),FixtureServer)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()
    def test_seven_adapters_against_real_local_http_fixture(self):
        for pid,(_,_,protocol,local) in PROVIDERS.items():
            with self.subTest(pid=pid):
                config=ProviderConfig(pid,self.url,protocol,local,model='fixture',tools=True)
                provider=Provider(config,'synthetic-fixture-key');models=provider.models(StopToken());self.assertTrue(models)
                answer,u=provider.complete([{'role':'user','content':'test'}],ENVELOPES,StopToken(),local_only=local)
                self.assertEqual(answer['content'],'fixture ok');self.assertEqual(u['output_tokens'],3)
    def test_live_provider_object_switch_no_restart(self):
        results=[]
        for pid in ('lmstudio','ollama','lmstudio'):
            protocol=PROVIDERS[pid][2];p=Provider(ProviderConfig(pid,self.url,protocol,True,model='fixture'))
            results.append(p.complete([{'role':'user','content':'hello'}],[],StopToken(),local_only=True)[0]['content'])
        self.assertEqual(results,['fixture ok']*3)
    def test_discovery_needs_no_model_or_capability_flags(self):
        for pid,(_,_,protocol,local) in PROVIDERS.items():
            with self.subTest(provider=pid):
                config=ProviderConfig(pid,self.url,protocol,local)
                models=Provider(config,'synthetic-fixture-key').models(StopToken(),local_only=local)
                self.assertEqual(models[0]['id'],'fixture')
    def test_redirect_not_followed(self):
        p=Provider(ProviderConfig('lmstudio',self.url,'chat',True))
        with self.assertRaises(ProviderError):p.request('/redirect',StopToken())
    def test_no_ambient_proxy_or_netrc(self):
        p=Provider(ProviderConfig('lmstudio',self.url,'chat',True));self.assertFalse(p.session.trust_env)

if __name__=='__main__':unittest.main()
