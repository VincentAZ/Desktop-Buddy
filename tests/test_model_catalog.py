import unittest
from desktop_buddy.model_ui import is_likely_agent_model,is_versioned_snapshot,model_sort_key
from desktop_buddy.state import thinking_levels,thinking_label


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
        self.assertIn(('Adaptive','adaptive'),thinking_levels('anthropic'))
        self.assertIn(('On','true'),thinking_levels('ollama'))
        self.assertEqual(thinking_label('openai','xhigh'),'Extra high')


if __name__=='__main__':unittest.main()
