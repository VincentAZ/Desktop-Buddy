"""Regressions for the October UI and runtime audit; no paid service calls."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop_buddy.agent import Agent
from desktop_buddy.browser import Browser
from desktop_buddy.comfyui import ComfyUI, ComfyUIError
from desktop_buddy.desktop import LazyBackend
from desktop_buddy.providers import Provider, ProviderError
from desktop_buddy.security import Cancelled, Denied, Policy, Redactor, StopToken
from desktop_buddy.tools import Registry
from desktop_buddy.state import ProviderConfig, Store
from test_core import Sandbox, ScriptedProvider, final, tool_answer


class CapabilityTests(Sandbox):
    def test_untested_model_can_execute_tools(self):
        provider=ScriptedProvider([tool_answer('fs.write',{'path':'proof','content':'works'},'1'),final()])
        provider.config.tools=None
        cid=self.store.conversation()
        Agent(self.store,self.registry(),provider).run(cid,'Write proof')
        self.assertEqual((self.workspace/'proof').read_text(),'works')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')

    def test_cloud_context_default_is_raised_once_but_explicit_values_stay(self):
        config=ProviderConfig('openai','https://api.openai.com/v1','responses',False,model='gpt-6.1-sol',context_tokens=32768)
        self.store.save_config(config)
        chosen=ProviderConfig('xai','https://api.x.ai/v1','responses',False,model='grok',context_tokens=65536);self.store.save_config(chosen)
        local=self.store.configs()['lmstudio'];local.model='small';self.store.save_config(local)
        self.store.set('capability_schema',2)
        self.store.close();self.store=Store(self.root/'state',self.redactor)
        self.assertEqual(self.store.configs()['openai'].context_tokens,128000)
        self.assertEqual(self.store.get('model_profiles')['openai:gpt-6.1-sol']['context_tokens'],128000)
        self.assertEqual(self.store.configs()['xai'].context_tokens,65536)
        self.assertEqual(self.store.configs()['lmstudio'].context_tokens,32768)
        self.assertEqual(self.store.configs()['anthropic'].context_tokens,128000)
    def test_legacy_false_is_migrated_once_without_changing_model_or_reasoning(self):
        config=ProviderConfig('openai','https://api.openai.com/v1','responses',False,
                              model='gpt-6.1-sol',tools=False,reasoning='high')
        self.store.save_config(config)
        self.store.set('capability_schema',1)
        self.store.close();self.store=Store(self.root/'state',self.redactor)
        c=self.store.configs()['openai']
        self.assertIsNone(c.tools)
        self.assertTrue(c.use_tools)
        self.assertEqual((c.model,c.reasoning),('gpt-6.1-sol','high'))
        self.assertIsNone(self.store.get('model_profiles')['openai:gpt-6.1-sol']['tools'])
        c.tools=False;self.store.save_config(c)
        self.store.close();self.store=Store(self.root/'state',self.redactor)
        self.assertFalse(self.store.configs()['openai'].use_tools)

    def test_explicit_text_only_still_prevents_effects(self):
        provider=ScriptedProvider([final()]);provider.config.tools=False
        provider.complete=Mock(side_effect=AssertionError('must not call'))
        cid=self.store.conversation()
        Agent(self.store,self.registry(),provider).run(cid,'Do something')
        provider.complete.assert_not_called()
        self.assertEqual(self.store.tasks(cid)[0]['status'],'failed')

    def test_presaved_message_not_duplicated(self):
        cid=self.store.conversation()
        self.store.add_message(cid,{'role':'user','content':'hello'})
        Agent(self.store,self.registry(),ScriptedProvider([final()])).run(cid,'hello',user_saved=True)
        self.assertEqual(sum(m['role']=='user' for m in self.store.messages(cid)),1)

    def test_delete_removes_draft(self):
        cid=self.store.conversation();self.store.set('drafts',{cid:'private draft','other':'keep'})
        self.store.delete_conversation(cid)
        self.assertEqual(self.store.get('drafts'),{'other':'keep'})

    def test_write_preserves_executable_permission(self):
        path=self.workspace/'script';path.write_text('old');path.chmod(0o750)
        self.registry().files.write('script','new')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o750)
        self.assertEqual(path.read_text(),'new')

    def test_protected_comfy_workflow_is_denied_before_approval(self):
        approve=Mock(return_value=True)
        with self.assertRaises(Denied):
            self.registry(approve=approve).execute('comfyui.run',{'workflow_path':str(self.store.root/'workflow.json'),'output_directory':'out'},StopToken())
        approve.assert_not_called()


class AdapterAuditTests(unittest.TestCase):
    def provider(self,protocol='responses'):
        return Provider(ProviderConfig('openai','https://api.openai.com/v1',protocol,False,model='fixture-model'),'synthetic-secret-value')

    def test_sol_uses_responses_and_valid_reasoning_without_setup_gates(self):
        from desktop_buddy.state import thinking_levels
        config=ProviderConfig('openai','https://api.openai.com/v1','chat',False,
                              model='gpt-6.1-sol',reasoning='minimal')
        self.assertEqual(config.protocol,'responses')
        self.assertEqual(config.reasoning,'low')
        choices=dict((wire,title) for title,wire in thinking_levels('openai',config.model))
        self.assertNotIn('none',choices);self.assertNotIn('minimal',choices)
        path,body=Provider(config,'fixture').payload([{'role':'user','content':'test'}],[{'name':'probe','parameters':{'type':'object'}}])
        self.assertEqual(path,'/responses');self.assertTrue(body['tools'])
        self.assertEqual(body['reasoning'],{'effort':'low'})

    def test_response_formatting_escapes_markup(self):
        from desktop_buddy.ui_widgets import message_markup
        import gi
        from gi.repository import Pango
        formatted=message_markup('## Result\n**Done** & <b>untrusted</b>\n- `file.txt`')
        self.assertIn('&lt;b&gt;untrusted&lt;/b&gt;',formatted)
        self.assertIn('<b>Done</b>',formatted)
        Pango.parse_markup(formatted,-1,'\x00')

    def test_old_portal_close_event_cannot_clear_new_session(self):
        from desktop_buddy.desktop import Portal
        portal=object.__new__(Portal)
        portal.session='/new';portal.stream=7;portal.size=(100,100)
        portal.closed_subscription=3;portal.bus=Mock()
        portal._closed(None,None,'/old')
        self.assertEqual(portal.session,'/new');portal.bus.signal_unsubscribe.assert_not_called()
        portal._closed(None,None,'/new')
        self.assertIsNone(portal.session);portal.bus.signal_unsubscribe.assert_called_once_with(3)

    def test_unknown_image_support_reaches_transport(self):
        p=self.provider()
        with patch('desktop_buddy.inference.isolated_completion',return_value=({'content':'ok'},{})) as complete:
            p.complete([{'role':'user','content':'look','images':['fixture.png']}],[],StopToken())
            complete.assert_called_once()

    def test_browser_spawn_failure_closes_pipes(self):
        with tempfile.TemporaryDirectory() as folder:
            browser=Browser(Path(folder)/'browser')
            before=len(list(Path('/proc/self/fd').iterdir()))
            with patch('desktop_buddy.browser.shutil.which',return_value='/fake/chrome'), patch('desktop_buddy.browser.subprocess.Popen',side_effect=OSError('spawn failed')):
                with self.assertRaises(OSError):browser.start(StopToken())
            self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),before)

    def test_unknown_tools_reaches_transport(self):
        p=self.provider()
        with patch('desktop_buddy.inference.isolated_completion',return_value=({'content':'ok'},{})) as complete:
            p.complete([{'role':'user','content':'hi'}],[{'name':'probe'}],StopToken())
            complete.assert_called_once()

    def test_protocol_change_discards_incompatible_native_metadata(self):
        response={'role':'assistant','content':'hello','native':{'openai:fixture-model':[{'type':'message','content':[]}]}}
        _,body=self.provider('chat').payload([response],[])
        self.assertEqual(body['messages'][0],{'role':'assistant','content':'hello'})
        response['native']['openai:fixture-model']={'reasoning_content':'private'}
        _,body=self.provider().payload([response],[])
        self.assertEqual(body['input'][0]['role'],'assistant')
        self.assertEqual(body['input'][0]['content'][0]['text'],'hello')

    def test_anthropic_initial_text_is_preserved(self):
        p=Provider(ProviderConfig('anthropic','https://api.anthropic.com/v1','anthropic',False,model='fixture'))
        answer,_=p.consume([{'type':'content_block_start','index':0,'content_block':{'type':'text','text':'Hello '}},
            {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'world'}},
            {'type':'message_stop'}],StopToken(),lambda *_:None)
        self.assertEqual(answer['content'],'Hello world')

    def test_provider_error_includes_redacted_reason(self):
        p=self.provider();response=Mock(status_code=400)
        response.iter_content.return_value=iter([json.dumps({'error':{'message':'Unsupported effort for synthetic-secret-value'}}).encode()])
        p.session.request=Mock(return_value=response)
        with self.assertRaisesRegex(ProviderError,'Unsupported effort for \\[REDACTED\\]'):
            p.request('/responses',StopToken(),{})
        response.close.assert_called_once()

    def test_cancel_after_headers_closes_response(self):
        p=self.provider();token=StopToken();response=Mock(status_code=200)
        def request(*args,**kwargs):token.stop();return response
        p.session.request=request
        with self.assertRaises(Cancelled):p.request('/responses',token,{},True)
        response.close.assert_called_once()

    def test_invalid_thinking_budget_and_nan_cost_rejected(self):
        c=ProviderConfig('anthropic','https://api.anthropic.com/v1','anthropic',False,reasoning='8192',max_output=4096)
        with self.assertRaisesRegex(ValueError,'Thinking budget'):c.validate()
        c.reasoning='';c.input_cost=float('nan')
        with self.assertRaisesRegex(ValueError,'finite'):c.validate()

    def test_browser_waits_for_new_document(self):
        browser=Browser('/tmp/unused-audit-profile');browser.start=Mock()
        browser.call=Mock(side_effect=[{'loaderId':'new'}, {'frameTree':{'frame':{'loaderId':'old'}}}, {'frameTree':{'frame':{'loaderId':'new'}}}])
        browser.inspect=Mock(return_value={'url':'https://example.com/new','ready':'complete'})
        token=Mock()
        result=browser.navigate('https://example.com/new',token)
        self.assertTrue(result['verified']);browser.inspect.assert_called_once()
        self.assertEqual(browser.call.call_count,3)

    def test_desktop_initialization_is_lazy_and_failure_can_retry(self):
        factory=Mock(side_effect=[RuntimeError('no portal'),Mock()]);backend=LazyBackend(factory)
        backend.close();factory.assert_not_called()
        with self.assertRaises(RuntimeError):backend.screenshot('x',StopToken())
        backend.screenshot('x',StopToken())
        self.assertEqual(factory.call_count,2)

    def test_comfy_does_not_claim_missing_status_is_success(self):
        client=ComfyUI();token=Mock()
        client.json=Mock(side_effect=lambda method,path,*args,**kwargs: {'prompt_id':args[1]['prompt_id']} if path=='/prompt' else {path.rsplit('/',1)[-1]:{'outputs':{}}})
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ComfyUIError):
            client.run('{"1":{"class_type":"Test","inputs":{}}}',{},Path(folder),token)

    def test_comfy_rejects_node_path_traversal(self):
        client=ComfyUI();token=Mock()
        def reply(method,path,*args,**kwargs):
            if path=='/prompt':return {'prompt_id':args[1]['prompt_id']}
            return {path.rsplit('/',1)[-1]:{'status':{'status_str':'success','completed':True},'outputs':{'../../escape':{'images':[{'filename':'x.png'}]}}}}
        client.json=reply
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ComfyUIError,'node identifier'):
            client.run('{"1":{"class_type":"Test","inputs":{}}}',{},Path(folder),token)


if __name__=='__main__':unittest.main()


class LicenceKeyLogTests(unittest.TestCase):
    """Live 2026-10-27: a Resolve key read from Gmail and typed into the app sat in the activity log."""
    KEY='7QX4T-HB2KM-W9RZD-PL3NE-5VCJA'

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.workspace=self.root/'work';self.workspace.mkdir()

    def tearDown(self):self.tmp.cleanup()

    def test_activity_log_hides_the_key_but_the_assistant_still_receives_it(self):
        from desktop_buddy.agent import Agent
        from test_core import ScriptedProvider, tool_answer, final
        store=Store(self.root/'state',Redactor())
        try:
            registry=Registry(Policy(self.workspace,'yolo',False,lambda *_:True,protected=[store.root]),store)
            registry.tools['mcp.call'].handler=Mock(return_value={'content':[{'type':'text','text':json.dumps({'body':'Your key:\n'+self.KEY})}]})
            script=[tool_answer('mcp.call',{'server':'gmail','name':'read','arguments':{}},'1'),
                    tool_answer('fs.write',{'path':'key.txt','content':self.KEY},'2'),final('I saved `'+self.KEY+'`.')]
            cid=store.conversation();Agent(store,registry,ScriptedProvider(script)).run(cid,'Save my key')
            self.assertEqual((self.workspace/'key.txt').read_text(),self.KEY)
            self.assertIn(self.KEY,json.dumps(store.messages(cid)))
            with store.lock:
                logs=json.dumps([list(r) for r in store.db.execute('SELECT body FROM audit')]+[list(r) for r in store.db.execute('SELECT checkpoint FROM tasks')])
            self.assertNotIn(self.KEY,logs);self.assertIn('[REDACTED KEY]',logs)
        finally:store.close()

    def test_keys_already_in_the_log_are_hidden_on_the_next_start(self):
        store=Store(self.root/'state',Redactor())
        with store.lock,store.db:
            store.db.execute('INSERT INTO audit(task,event,body,created) VALUES (?,?,?,?)',('t','tool_started',json.dumps({'arguments':{'text':self.KEY}}),0))
            store.db.execute('INSERT INTO audit(task,event,body,created) VALUES (?,?,?,?)',('t','report',json.dumps({'content':'Key:\n'+self.KEY+'\nVersion 2026-10-27'}),0))
            store.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?)',('t','c','complete',json.dumps({'pending_call':{'text':self.KEY}}),0))
        store.set('log_schema',1);store.close()
        store=Store(self.root/'state',Redactor())
        try:
            with store.lock:
                bodies=[r[0] for r in store.db.execute('SELECT body FROM audit')]+[r[0] for r in store.db.execute('SELECT checkpoint FROM tasks')]
            self.assertFalse(any(self.KEY in b for b in bodies))
            self.assertIn('2026-10-27',bodies[1])
            self.assertEqual(store.get('log_schema'),2)
        finally:store.close()
        wal=Path(str(self.root/'state'/'state.sqlite3')+'-wal')
        self.assertNotIn(self.KEY.encode(),(self.root/'state'/'state.sqlite3').read_bytes()+(wal.read_bytes() if wal.exists() else b''))
