"""Just do it (yolo) is a human-selected approval policy, not a tool-controlled permission."""
from unittest.mock import Mock
from desktop_buddy.agent import Agent
from desktop_buddy.security import Cancelled, Denied, Policy, StopToken
from desktop_buddy.state import ProviderConfig
from test_core import Sandbox, ScriptedProvider, final, tool_answer


class YoloTests(Sandbox):
    def test_yolo_allows_every_action_without_a_prompt(self):
        callback=Mock(side_effect=AssertionError('Just do it must not prompt'))
        policy=Policy(self.workspace,'yolo',False,callback)
        token=StopToken()
        for name,risk in [('shell.run','command'),('fs.delete','destructive'),('desktop.input','desktop_input'),
                          ('http.request','http'),('comfyui.run','compute'),('memory.set','sensitive'),
                          ('mcp.call','network'),('app.launch','launch'),('desktop.window','desktop_input'),('desktop.connect','desktop_input')]:
            with self.subTest(name=name):policy.authorize(name,{},True,risk,token)
        policy.authorize('fs.read',{'path':str(self.root/'outside')},False,'read',token)
        self.assertTrue(policy.request_approval('Cloud data disclosure','request',token))
        callback.assert_not_called()

    def test_cloud_agent_in_yolo_runs_without_a_disclosure_prompt(self):
        provider=ScriptedProvider([tool_answer('fs.write',{'path':'proof','content':'done'},'1'),final()])
        provider.config=ProviderConfig('openai','https://api.openai.com/v1','responses',False,model='fixture')
        callback=Mock(side_effect=AssertionError('Just do it must not prompt'))
        cid=self.store.conversation()
        Agent(self.store,self.registry(mode='yolo',approve=callback),provider).run(cid,'Write proof')
        self.assertEqual((self.workspace/'proof').read_text(),'done')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')
        callback.assert_not_called()

    def test_execute_mode_still_asks_and_a_denied_disclosure_never_calls_the_model(self):
        provider=ScriptedProvider([final()])
        provider.config=ProviderConfig('openai','https://api.openai.com/v1','responses',False,model='fixture')
        provider.complete=Mock(side_effect=AssertionError('Cloud data leaked'))
        Agent(self.store,self.registry(mode='execute',approve=lambda *_:False),provider).run(self.store.conversation(),'Private task')
        provider.complete.assert_not_called()
        callback=Mock(return_value=False)
        policy=Policy(self.workspace,'execute',False,callback)
        with self.assertRaises(Denied):policy.authorize('app.launch',{},True,'launch',StopToken())
        callback.assert_called_once()

    def test_stop_still_blocks_yolo_actions(self):
        policy=Policy(self.workspace,'yolo',False)
        token=StopToken();token.stop()
        with self.assertRaises(Cancelled):policy.authorize('shell.run',{},True,'command',token)
        with self.assertRaises(Cancelled):policy.request_approval('cloud','',token)

    def test_offline_discuss_only_and_protected_paths_remain_hard_limits(self):
        policy=Policy(self.workspace,'yolo',True,protected=[self.store.root])
        with self.assertRaises(Denied):policy.authorize('http.request',{},True,'http',StopToken())
        with self.assertRaises(Denied):policy.authorize('fs.read',{'path':str(self.store.root/'state.sqlite3')},False,'read',StopToken())
        with self.assertRaises(Denied):policy.authorize('fs.read',{'path':str(self.root/'.ssh'/'id_ed25519')},False,'read',StopToken())
        plan=Policy(self.workspace,'plan',False,Mock(return_value=True))
        with self.assertRaises(Denied):plan.authorize('fs.write',{'path':'x'},True,'write',StopToken())

    def test_unknown_mode_is_not_implicit_execution(self):
        with self.assertRaises(ValueError):Policy(self.workspace,'typo')
