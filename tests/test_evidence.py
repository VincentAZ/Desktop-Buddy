"""Missing source information must not turn into saved or entered facts."""
import json
from unittest.mock import Mock
from desktop_buddy.agent import Agent
from test_core import Sandbox, ScriptedProvider, tool_answer, final


class EvidenceTests(Sandbox):
    def test_corrected_invalid_page_request_can_proceed_after_a_real_read(self):
        (self.workspace/'source.txt').write_text('Actual source data')
        script=[tool_answer('fs.read',{'path':'source.txt','offset':-1},'1'),
                tool_answer('fs.read',{'path':'source.txt','expected_revision':'0'},'2'),
                tool_answer('fs.read',{'path':'source.txt'},'3'),
                tool_answer('fs.write',{'path':'report.txt','content':'Actual source data'},'4'),final('Done')]
        cid=self.store.conversation();Agent(self.store,self.registry(),ScriptedProvider(script)).run(cid,'Read and save the source')
        self.assertEqual((self.workspace/'report.txt').read_text(),'Actual source data')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')

    def test_argument_correction_does_not_clear_a_genuine_failed_source(self):
        (self.workspace/'other.txt').write_text('Unrelated source')
        script=[tool_answer('fs.read',{'path':'missing.txt'},'1'),
                tool_answer('fs.read',{'path':'other.txt','offset':-1},'2'),
                tool_answer('fs.read',{'path':'other.txt'},'3'),
                tool_answer('fs.write',{'path':'report.txt','content':'Invented missing source'},'4'),final('Done')]
        cid=self.store.conversation();Agent(self.store,self.registry(),ScriptedProvider(script)).run(cid,'Read both sources')
        self.assertFalse((self.workspace/'report.txt').exists())
        task=self.store.tasks(cid)[0];self.assertEqual(task['status'],'needs_review')
        failed=json.loads(task['checkpoint'])['source_failures']
        self.assertEqual(len(failed),1);self.assertIn('missing.txt',next(iter(failed)))

    def test_invalid_arguments_return_the_exact_schema_without_executing(self):
        registry=self.registry();handler=Mock();registry.tools['fs.read'].handler=handler
        call=tool_answer('fs.read',{'path':'source.txt','purpose':'Read the source'},'1')
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider([call,final('Could not read')])).run(cid,'Read the file')
        handler.assert_not_called()
        output=next(json.loads(m['content'])['untrusted_tool_result'] for m in self.store.messages(cid) if m['role']=='tool')
        self.assertEqual(output['arguments_schema'],registry.tools['fs.read'].parameters)
        self.assertIn('never inside',output['next'])
        self.assertEqual(json.loads(self.store.tasks(cid)[0]['checkpoint'])['source_failures'],{})

    def test_oversized_read_is_valid_json_and_blocks_invented_output(self):
        registry=self.registry();registry.tools['fs.read'].handler=Mock(return_value={'content':'data '*20000})
        script=[tool_answer('fs.read',{'path':'large.txt'},'1'),
                tool_answer('fs.write',{'path':'report.txt','content':'Invented facts'},'2'),final('Everything verified')]
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Read the large file')
        self.assertFalse((self.workspace/'report.txt').exists())
        self.assertEqual(self.store.tasks(cid)[0]['status'],'needs_review')
        outputs=[json.loads(m['content'])['untrusted_tool_result'] for m in self.store.messages(cid) if m['role']=='tool']
        self.assertEqual(outputs[0]['type'],'ResultTooLarge');self.assertTrue(outputs[0]['isError'])
        self.assertNotIn('data data',outputs[0]['error'])

    def test_oversized_read_recovers_with_a_smaller_range(self):
        registry=self.registry();registry.tools['fs.read'].handler=Mock(side_effect=[
            {'content':'x'*70000},{'content':'Actual source data','next_offset':None,'truncated':False}])
        script=[tool_answer('fs.read',{'path':'large.txt','limit':100000},'1'),
                tool_answer('fs.read',{'path':'large.txt','limit':8000},'2'),
                tool_answer('fs.write',{'path':'report.txt','content':'Actual source data'},'3'),final('Done')]
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Read and save the source')
        self.assertEqual((self.workspace/'report.txt').read_text(),'Actual source data')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')

    def test_reading_another_page_does_not_clear_the_failed_page(self):
        registry=self.registry();registry.tools['fs.read'].handler=Mock(side_effect=[
            OSError('The requested page could not be read'),{'content':'First page only'}])
        script=[tool_answer('fs.read',{'path':'large.txt','offset':8000},'1'),
                tool_answer('fs.read',{'path':'large.txt','offset':0},'2'),
                tool_answer('fs.write',{'path':'report.txt','content':'Invented second page'},'3'),final('Done')]
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Read both pages')
        self.assertFalse((self.workspace/'report.txt').exists())
        self.assertEqual(self.store.tasks(cid)[0]['status'],'needs_review')

    def failing_browser(self):
        registry=self.registry()
        registry.tools['browser.navigate'].handler=Mock(side_effect=TimeoutError('Source unavailable'))
        return registry

    def test_batched_read_failure_stops_the_following_write(self):
        answer=tool_answer('browser.navigate',{'url':'http://127.0.0.1:4567/'},'1')
        answer['tool_calls']+=tool_answer('fs.write',{'path':'report.txt','content':'Invented facts'},'2')['tool_calls']
        cid=self.store.conversation()
        Agent(self.store,self.failing_browser(),ScriptedProvider([answer,final('Done')])).run(cid,'Copy the source')
        self.assertFalse((self.workspace/'report.txt').exists())
        task=self.store.tasks(cid)[0]
        self.assertEqual(task['status'],'needs_review')
        self.assertTrue(json.loads(task['checkpoint'])['source_failures'])

    def test_failed_read_note_is_added_and_native_copy_dropped(self):
        class StreamingProvider(ScriptedProvider):
            def complete(self,messages,tools,token,emit,local_only):
                answer,usage=super().complete(messages,tools,token,emit,local_only)
                if not answer.get('tool_calls'):emit('stream','Everything was verified.')
                return answer,usage
        finish=final('Everything was verified.')
        finish['native']={'provider:model':[{'type':'text','text':'Everything was verified.'}]}
        provider=StreamingProvider([tool_answer('browser.navigate',{'url':'http://127.0.0.1:4567/'},'1'),finish])
        cid=self.store.conversation();events=[]
        Agent(self.store,self.failing_browser(),provider,lambda *event:events.append(event)).run(cid,'Read the source')
        message=self.store.messages(cid)[-1]
        self.assertEqual(self.store.tasks(cid)[0]['status'],'needs_review')
        self.assertTrue(message['content'].startswith('Everything was verified.'))
        self.assertIn('Note from Desktop Buddy: a step that read information for this task did not work (Open a web page).',message['content'])
        self.assertIn('Anything above that depends on it is unconfirmed',message['content'])
        # Later turns read the reply with the note, not the provider's cached copy.
        self.assertNotIn('native',message)

    def test_successful_retry_allows_writing_the_actual_source(self):
        registry=self.registry()
        registry.tools['browser.navigate'].handler=Mock(side_effect=[TimeoutError('Unavailable'),{'text':'Observed delivery details'}])
        script=[tool_answer('browser.navigate',{'url':'http://127.0.0.1:4567/'},'1'),
                tool_answer('fs.write',{'path':'report.txt','content':'Invented facts'},'2'),
                tool_answer('browser.navigate',{'url':'http://127.0.0.1:4567/'},'3'),
                tool_answer('fs.write',{'path':'report.txt','content':'Observed delivery details'},'4'),final('Done')]
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Copy the source')
        self.assertEqual((self.workspace/'report.txt').read_text(),'Observed delivery details')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')
        writes=[row for row in self.store.audits() if row['event']=='tool_finished' and 'fs.write' in row['body']]
        self.assertEqual(len(writes),1)

    def test_unrelated_success_does_not_allow_content_changes_or_shell_bypass(self):
        registry=self.failing_browser()
        registry.tools['shell.run'].handler=Mock()
        registry.tools['desktop.set_text'].handler=Mock()
        (self.workspace/'unrelated.txt').write_text('An unrelated source')
        shell=tool_answer('shell.run',{'argv':['echo','invented facts']},'4')
        shell['tool_calls'][0]['arguments']['purpose']='Save the requested details.'
        script=[tool_answer('browser.navigate',{'url':'http://127.0.0.1:4567/'},'1'),
                tool_answer('fs.read',{'path':'unrelated.txt'},'2'),
                tool_answer('fs.write',{'path':'report.txt','content':'Invented facts'},'3'),shell,
                tool_answer('desktop.set_text',{'target_path':'0/0','expected_name':'Field',
                    'expected_window':'Window','text':'Invented facts'},'5'),final('Done')]
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Copy the source')
        registry.tools['shell.run'].handler.assert_not_called()
        registry.tools['desktop.set_text'].handler.assert_not_called()
        self.assertFalse((self.workspace/'report.txt').exists())
        messages=self.store.messages(cid)
        blocked=[m for m in messages if m['role']=='tool' and 'UnverifiedSourceError' in m['content']]
        self.assertEqual(len(blocked),3)
        status=registry.execute('buddy.status',{},self.token)
        self.assertTrue(status['content_changes_paused'])
        self.assertIn('Open a web page',status['unread_source_steps'])

    def test_resume_restores_source_failure_and_a_new_request_can_change_scope(self):
        cid=self.store.conversation()
        Agent(self.store,self.failing_browser(),ScriptedProvider([
            tool_answer('browser.navigate',{'url':'http://127.0.0.1:4567/'},'1'),final('Source unavailable')])).run(cid,'Copy the source')
        write=tool_answer('fs.write',{'path':'report.txt','content':'Unverified contents'},'2')
        Agent(self.store,self.registry(),ScriptedProvider([write,final('Done')])).run(cid,'Resume',resume=True)
        self.assertFalse((self.workspace/'report.txt').exists())
        self.assertEqual(self.store.tasks(cid)[0]['status'],'needs_review')
        Agent(self.store,self.registry(),ScriptedProvider([
            tool_answer('fs.write',{'path':'new.txt','content':'User supplied text'},'3'),final('Done')])).run(cid,'New task: save my supplied text')
        self.assertEqual((self.workspace/'new.txt').read_text(),'User supplied text')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')

    def test_http_error_response_blocks_derived_output(self):
        registry=self.registry();registry.tools['http.request'].handler=Mock(return_value={'status':403,'body':'Not authorized','verified':False})
        script=[tool_answer('http.request',{'url':'http://127.0.0.1:4567/'},'1'),
                tool_answer('fs.write',{'path':'report.txt','content':'Invented response'},'2'),final('Done')]
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Read the service')
        self.assertFalse((self.workspace/'report.txt').exists())
        self.assertEqual(self.store.tasks(cid)[0]['status'],'needs_review')

    def test_mcp_error_is_not_source_data_and_exact_retry_can_recover(self):
        registry=self.registry()
        registry.tools['mcp.call'].handler=Mock(side_effect=[
            {'isError':True,'content':[{'type':'text','text':'Temporarily unavailable'}]},
            {'isError':False,'content':[{'type':'text','text':'Actual app data'}]}])
        arguments={'server':'fixture','name':'read_record','arguments':{'id':'record-1'}}
        script=[tool_answer('mcp.call',arguments,'1'),
                tool_answer('fs.write',{'path':'report.txt','content':'Invented data'},'2'),
                tool_answer('mcp.call',arguments,'3'),
                tool_answer('fs.write',{'path':'report.txt','content':'Actual app data'},'4'),final('Done')]
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Read the app record')
        self.assertEqual((self.workspace/'report.txt').read_text(),'Actual app data')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')
        errors=[m for m in self.store.messages(cid) if m['role']=='tool' and m.get('error')]
        self.assertEqual(len(errors),2)

    def test_paused_notice_quotes_the_failed_steps_own_message(self):
        registry=self.registry()
        google='Access to this tool requires that your Google Cloud project (487331381719) is enrolled in the Google Workspace Developer Preview Program.'
        registry.tools['mcp.call'].handler=Mock(return_value={'isError':True,'content':[{'type':'text','text':google}]})
        script=[tool_answer('mcp.call',{'server':'gmail','name':'search_threads','arguments':{'query':'invoice'}},'1'),
                final('I found your invoice: it was sent on Monday.')]
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Find my invoice')
        message=self.store.messages(cid)[-1]['content']
        self.assertTrue(message.startswith('I found your invoice'))
        self.assertIn('The app answered: “'+google+'”',message)
        self.assertIn('Open Connect apps',message);self.assertIn('unconfirmed',message)
        self.assertEqual(self.store.tasks(cid)[0]['status'],'needs_review')
        self.assertEqual(json.loads(self.store.tasks(cid)[0]['checkpoint'])['source_failures'].popitem()[1]['reason'],google)

    def test_continue_task_retries_the_failed_app_read_before_asking_the_model(self):
        registry=self.registry()
        google='Access to this tool requires that your Google Cloud project is enrolled in the Developer Preview Program.'
        handler=Mock(side_effect=[{'isError':True,'content':[{'type':'text','text':google}]},
                                  {'isError':False,'content':[{'type':'text','text':'Order 247086: key ABCD-1234'}]}])
        registry.tools['mcp.call'].handler=handler
        arguments={'server':'gmail','name':'search_threads','arguments':{'query':'from:blackmagicdesign.com'}}
        cid=self.store.conversation()
        Agent(self.store,registry,ScriptedProvider([tool_answer('mcp.call',arguments,'1'),final('Found it: ABCD-1234')])).run(cid,'Find my code')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'needs_review')
        # The model is not asked whether to retry: the host repeats the read, then the model continues.
        Agent(self.store,registry,ScriptedProvider([final('Your code is ABCD-1234.')])).run(cid,'Resume',resume=True)
        self.assertEqual(handler.call_count,2)
        self.assertEqual(handler.call_args_list[1].args[0],arguments)
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')
        self.assertEqual(self.store.messages(cid)[-1]['content'],'Your code is ABCD-1234.')
        self.assertEqual(json.loads(self.store.tasks(cid)[0]['checkpoint'])['source_failures'],{})
        self.assertTrue(any(json.loads(r['body'])=={'operations':['mcp.call']} for r in self.store.audit_records(cid) if r['event']=='source_retry') if hasattr(self.store,'audit_records') else True)

    def test_failed_app_source_points_to_the_connection_screen(self):
        registry=self.registry()
        registry.tools['mcp.read_resource'].handler=Mock(side_effect=RuntimeError('Sign-in required'))
        provider=ScriptedProvider([tool_answer('mcp.read_resource',{'server':'fixture','uri':'record:1'},'1'),final('Done')])
        cid=self.store.conversation();Agent(self.store,registry,provider).run(cid,'Read the app record')
        self.assertIn('Open Connect apps',self.store.messages(cid)[-1]['content'])
        self.assertEqual(self.store.tasks(cid)[0]['status'],'needs_review')

    def test_nonzero_command_exit_is_information_for_the_model_not_a_failed_task(self):
        # Live 2026-10-27: `ls` of a missing folder (exit 2) and `grep -c` counting
        # zero missing libraries (exit 1) replaced two correct reports with a failure.
        for code,output in [(2,'2.1.308 (Claude Code)\nls: cannot access versions: No such file or directory'),(1,'21.1.2.0010_studio\n0')]:
            with self.subTest(exit_code=code):
                registry=self.registry();registry.tools['shell.run'].handler=Mock(return_value={'exit_code':code,'output':output,'verified':False})
                call=tool_answer('shell.run',{'argv':['bash','-c','checks']},'1')
                call['tool_calls'][0]['arguments']['purpose']='Check what is installed.'
                cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider([call,final('Already the newest version.')])).run(cid,'Update it')
                self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')
                self.assertEqual(self.store.messages(cid)[-1]['content'],'Already the newest version.')
                tool=next(m for m in self.store.messages(cid) if m['role']=='tool')
                self.assertTrue(tool['error'])  # the model is still told the command exited non-zero

    def test_continue_task_does_not_inherit_the_previous_attempts_refusals(self):
        registry=self.registry();registry.execute=Mock(return_value={'launched':False,'verified':False})
        cid=self.store.conversation()
        Agent(self.store,registry,ScriptedProvider([tool_answer('app.launch',{'desktop_id':'fixture'},'1'),final('It did not open.')])).run(cid,'Open it')
        Agent(self.store,self.registry(),ScriptedProvider([final('It is open now.')])).run(cid,'Resume',resume=True)
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')
        self.assertEqual(self.store.messages(cid)[-1]['content'],'It is open now.')

    def test_a_declined_step_is_the_persons_choice_not_a_failure(self):
        from desktop_buddy.security import Denied
        registry=self.registry();registry.execute=Mock(side_effect=Denied('Not authorized.'))
        cid=self.store.conversation()
        Agent(self.store,registry,ScriptedProvider([tool_answer('app.launch',{'desktop_id':'fixture'},'1'),final('Okay, I left it closed.')])).run(cid,'Open it')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')
        self.assertEqual(self.store.messages(cid)[-1]['content'],'Okay, I left it closed.')

    def test_failed_discovery_and_new_read_tools_block_writes_until_retried(self):
        for name,arguments in [('fs.list',{'path':'.'}),('mcp.tools',{'server':'fixture'}),
                               ('fixture.read',{})]:
            with self.subTest(tool=name):
                registry=self.registry()
                if name=='fixture.read':registry.add(name,'Read a test source',{},[],lambda x,t:{})
                registry.tools[name].handler=Mock(side_effect=[RuntimeError('Unavailable'),{'data':'Observed'}])
                script=[tool_answer(name,arguments,'1'),
                        tool_answer('fs.write',{'path':'report.txt','content':'Invented'},'2'),
                        tool_answer(name,arguments,'3'),
                        tool_answer('fs.write',{'path':'report.txt','content':'Observed'},'4'),final('Done')]
                cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Read and save')
                self.assertEqual((self.workspace/'report.txt').read_text(),'Observed')
                self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')
                blocked=[m for m in self.store.messages(cid) if m['role']=='tool' and 'UnverifiedSourceError' in m['content']]
                self.assertEqual(len(blocked),1)
                (self.workspace/'report.txt').unlink()

    def test_different_search_cannot_clear_the_failed_query(self):
        registry=self.registry()
        registry.tools['fs.search'].handler=Mock(side_effect=[RuntimeError('Unavailable'),{'matches':['unrelated.txt']}])
        script=[tool_answer('fs.search',{'path':'.','query':'requested'},'1'),
                tool_answer('fs.search',{'path':'.','query':'unrelated'},'2'),
                tool_answer('fs.write',{'path':'report.txt','content':'Invented'},'3'),final('Done')]
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Find and summarize requested files')
        self.assertFalse((self.workspace/'report.txt').exists())
        self.assertEqual(self.store.tasks(cid)[0]['status'],'needs_review')

    def test_explicit_app_rejections_cannot_be_reported_as_success(self):
        class StreamingProvider(ScriptedProvider):
            def complete(self,messages,tools,token,emit,local_only):
                answer,usage=super().complete(messages,tools,token,emit,local_only)
                if not answer.get('tool_calls'):emit('stream','Everything worked perfectly.')
                return answer,usage
        cases=[('desktop.set_text',{'verified':False,'accepted':True}),
               ('app.launch',{'launched':False,'verified':False}),
               ('desktop.accessible_action',{'accepted':False,'verified':False}),
               ('desktop.input',{'sent':False,'verified':False}),
               ('desktop.window',{'sent':False,'verified':False}),
               ('browser.action',{'sent':False,'verified':False})]
        for name,outcome in cases:
            with self.subTest(tool=name):
                registry=self.registry();registry.execute=Mock(return_value=outcome)
                cid=self.store.conversation();events=[]
                finish=final('Everything worked perfectly.')
                finish['native']={'provider:model':[{'type':'text','text':'Everything worked perfectly.'}]}
                provider=StreamingProvider([tool_answer(name,{},'1'),finish])
                Agent(self.store,registry,provider,lambda *event:events.append(event)).run(cid,'Make the requested change')
                message=self.store.messages(cid)[-1]
                self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')
                self.assertTrue(message['content'].startswith('Everything worked perfectly.'))
                self.assertIn('Note from Desktop Buddy: this step was refused and not retried successfully',message['content'])
                self.assertNotIn('native',message)

    def test_sent_but_unverified_input_is_distinct_from_an_explicit_rejection(self):
        from desktop_buddy.agent import tool_failed
        self.assertFalse(tool_failed('desktop.input',{'sent':True,'verified':False}))
        self.assertFalse(tool_failed('desktop.accessible_action',{'accepted':True,'verified':False}))
        self.assertFalse(tool_failed('app.launch',{'launched':True,'verified':False}))
        self.assertFalse(tool_failed('desktop.set_text',{'accepted':False,'verified':True}))

    def test_app_rejection_recovery_requires_the_failed_operation_to_succeed(self):
        registry=self.registry()
        execute=registry.execute
        outcomes=iter([{'launched':False,'verified':False},{'launched':True,'verified':False}])
        registry.execute=lambda name,args,*rest:next(outcomes) if name=='app.launch' else execute(name,args,*rest)
        # Live 2026-10-27: the retry used the corrected ID (with .desktop), which
        # is a different request but settles the same action.
        script=[tool_answer('app.launch',{'desktop_id':'com.blackmagicdesign.resolve'},'1'),
                tool_answer('fs.list',{'path':'.'},'2'),
                tool_answer('app.launch',{'desktop_id':'com.blackmagicdesign.resolve.desktop'},'3'),final('Resolve is open.')]
        cid=self.store.conversation();Agent(self.store,registry,ScriptedProvider(script)).run(cid,'Launch the app')
        self.assertEqual(self.store.tasks(cid)[0]['status'],'complete')
        self.assertEqual(self.store.messages(cid)[-1]['content'],'Resolve is open.')
        self.assertFalse(json.loads(self.store.tasks(cid)[0]['checkpoint'])['unresolved_operations'])

    def test_launch_accepts_an_id_without_the_desktop_ending(self):
        from desktop_buddy.tools import desktop_app
        found=Mock(name='app')
        gio=Mock();gio.DesktopAppInfo.new=Mock(side_effect=lambda value:found if value=='fixture.desktop' else (_ for _ in ()).throw(TypeError('constructor returned NULL')))
        self.assertIs(desktop_app(gio,'fixture'),found)
        gio.AppInfo.get_all=Mock(return_value=[])
        with self.assertRaisesRegex(ValueError,'No installed app has the ID missing'):desktop_app(gio,'missing')
