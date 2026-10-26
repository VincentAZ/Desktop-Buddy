import unittest
from desktop_buddy.model_ui import is_likely_agent_model,is_versioned_snapshot,model_sort_key
from desktop_buddy.state import thinking_levels,thinking_label,thinking_hint,ProviderConfig


class ModelCatalogTests(unittest.TestCase):
    def test_specialized_and_snapshot_classification(self):
        self.assertTrue(is_likely_agent_model('gpt-5.4'))
        self.assertFalse(is_likely_agent_model('text-embedding-3-large'))
        self.assertFalse(is_likely_agent_model('gpt-realtime-2'))
        self.assertTrue(is_versioned_snapshot('gpt-5.4-2026-03-05'))
        self.assertTrue(is_versioned_snapshot('gpt-3.5-turbo-0125'))
        self.assertFalse(is_versioned_snapshot('gpt-5.4-mini'))

    def test_sort_is_deterministic_and_newer_general_models_lead(self):
        models=['text-embedding-3-large','gpt-3.5-turbo','o4-mini','gpt-5.4-mini','gpt-5.6-luna','gpt-5.4-2026-03-05']
        self.assertEqual(sorted(models,key=model_sort_key),[
            'gpt-5.6-luna','gpt-5.4-mini','gpt-5.4-2026-03-05','gpt-3.5-turbo','o4-mini','text-embedding-3-large'])

    def test_thinking_choices_are_provider_aware(self):
        self.assertIn(('Maximum','max'),thinking_levels('openai'))
        self.assertIn(('Automatic','adaptive'),thinking_levels('anthropic'))
        self.assertIn(('High','8192'),thinking_levels('anthropic'))
        self.assertEqual(thinking_label('anthropic',''),'Default')
        self.assertIn(('On','true'),thinking_levels('ollama'))
        self.assertEqual(thinking_label('openai','xhigh'),'Extra high')
        self.assertIn('harder problems',thinking_hint('anthropic','8192'))

    def test_anthropic_level_raises_response_limit_so_every_level_works(self):
        c=ProviderConfig(id='anthropic',base_url='https://api.anthropic.com',protocol='anthropic',local=False,
                         model='claude-fable-5-1',context_tokens=200000,max_output=4096)
        c.set_thinking('8192').validate()
        self.assertEqual((c.reasoning,c.max_output),('8192',12288))
        c.set_thinking('32768').validate()
        self.assertEqual(c.max_output,36864)
        c.set_thinking('1024').validate()
        self.assertEqual(c.max_output,36864)
        c.set_thinking('').validate()
        self.assertEqual(c.reasoning,'')


if __name__=='__main__':unittest.main()
