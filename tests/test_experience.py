import unittest
from desktop_buddy.experience import activity_summary,friendly_error,action_name,approval_summary,preference_text
import json


class ExperienceTests(unittest.TestCase):
    def test_structured_preference_is_readable_without_json_editing(self):
        self.assertEqual(preference_text({'Columns':['Region','Revenue'],'Include totals':True}),
                         'Columns: • Region\n  • Revenue\nInclude totals: Yes')
        self.assertEqual(preference_text('Keep my exact words'), 'Keep my exact words')

    def test_approval_preserves_exact_recipients_amount_and_nested_values(self):
        args={'server':'sales-123','name':'update_record','arguments':{'to':['jane@example.com','lee@example.com'],'amount':1250.75,'currency':'USD','record':{'id':'Account-0042','enabled':False}}}
        summary=approval_summary('mcp.call',json.dumps(args),{'sales-123':'Sales app'})
        for text in ('App: Sales app','Update record','jane@example.com','lee@example.com','1250.75','USD','Account-0042','false'):
            self.assertIn(text,summary)
        self.assertNotIn('{',summary);self.assertNotIn('success',summary)

    def test_shell_review_explains_task_and_scope_without_exposing_code(self):
        argv=['python3','-c','print("$HOME is text")']
        text=approval_summary('shell.run',json.dumps({'argv':argv,'timeout':300}),purpose='Create the quarterly report.',workspace='/work',local_only=True)
        self.assertIn('Create the quarterly report.',text);self.assertIn('300 seconds',text)
        self.assertIn('/work',text);self.assertIn('Internet access is blocked',text)
        self.assertNotIn('python3',text);self.assertNotIn('$HOME',text)
        self.assertIn('not explained',approval_summary('shell.run',json.dumps({'argv':argv})))

    def test_unverified_effect_does_not_look_successful(self):
        title,body=activity_summary('RESULT','{"verified":false,"sent":true}')
        self.assertIn('needs checking',title)
        self.assertIn('not been confirmed',body)

    def test_window_keyboard_review_names_actual_scope_and_exact_keys(self):
        args={'action':'key','keyboard_scope':'window','target_path':'3/0','expected_name':'Drawing',
              'expected_window':'Drawing','keys':'Ctrl+s'}
        text=approval_summary('desktop.input',json.dumps(args))
        self.assertIn('Window: Drawing',text);self.assertIn('Keys: Ctrl+s',text)
        self.assertIn('active part of this window',text)
        self.assertNotIn('Control:',text);self.assertNotIn('3/0',text)

    def test_timeout_recovery_does_not_encourage_duplicate_changes(self):
        title,body=friendly_error('HTTP timeout after posting order')
        self.assertNotIn('HTTP',title)
        self.assertIn('whether it already happened',body)

    def test_context_limit_explains_saved_progress_without_customer_configuration(self):
        title,body=friendly_error('Context budget exceeded even after compaction (about 10,340 tokens needed, 10,336 available).')
        self.assertIn('more room',title);self.assertIn('progress is saved',body)
        self.assertIn('completed changes',body);self.assertIn('Ask IT',body)
        self.assertNotIn('tokens',title+body);self.assertNotIn('compaction',title+body)

    def test_response_limit_and_refusal_have_plain_explanations(self):
        title,body=friendly_error('The reply was cut off at the response limit of 4,096 tokens; nothing was executed.')
        self.assertIn('too long',title);self.assertIn('Maximum response tokens',body);self.assertIn('smaller',body)
        title,body=friendly_error('Claude declined this request; nothing was executed.')
        self.assertIn('declined',title);self.assertIn('Nothing was changed',body)

    def test_raw_errors_stay_out_of_customer_summary(self):
        for raw in ('ConnectionRefusedError: [Errno 111]', 'invalid_api_key sk-test-12345', 'Traceback in /tmp/helper.py line 42'):
            title,body=friendly_error(raw)
            self.assertNotIn(raw,title+body)
        self.assertEqual(action_name('mcp.call'),'Work in a connected app')

    def test_wrapped_tool_name_and_exact_target(self):
        title,body=activity_summary('TOOL','{"name":"run_tool","arguments":{"name":"fs.write","arguments":{"path":"Quarterly report.xlsx"}}}')
        self.assertEqual(title,'Save a file');self.assertEqual(body,'Quarterly report.xlsx')
