import unittest
from unittest.mock import Mock
from desktop_buddy.state import Credentials
from desktop_buddy.security import Redactor

class CredentialTests(unittest.TestCase):
 def test_secret_service_roundtrip_contract_without_writing_user_keyring(self):
  redactor=Redactor();vault=Credentials(redactor);secret_api=Mock();schema=object();secret_api.COLLECTION_DEFAULT='default'
  secret_api.password_store_sync.return_value=True;secret_api.password_lookup_sync.return_value='test-credential-not-a-real-key'
  vault._service=lambda:(secret_api,schema)
  vault.set('openai','test-credential-not-a-real-key')
  secret_api.password_store_sync.assert_called_once_with(schema,{'provider':'openai'},'default','Desktop Buddy — openai','test-credential-not-a-real-key',None)
  self.assertEqual(vault.get('openai'),'test-credential-not-a-real-key');self.assertEqual(redactor.text('test-credential-not-a-real-key'),'[REDACTED]')
  vault.set('openai','');secret_api.password_clear_sync.assert_called_once();self.assertEqual(vault.get('openai'),'')
 def test_keyring_failure_has_no_fallback(self):
  vault=Credentials(Redactor());api=Mock();api.password_store_sync.return_value=False;vault._service=lambda:(api,object())
  with self.assertRaises(RuntimeError):vault.set('openai','synthetic-test')
  self.assertNotIn('openai',vault.cache)

if __name__=='__main__':unittest.main()
