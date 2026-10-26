from pathlib import Path
import tempfile
import unittest
from PIL import Image
from desktop_buddy.state import Store,ProviderConfig
from desktop_buddy.security import Policy,Redactor,StopToken,Denied
from desktop_buddy.tools import Registry
from desktop_buddy.providers import Provider

class ImageAndStateTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'state',Redactor())
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def test_workspace_image_normalizes_without_altering_source(self):
  source=self.root/'photo.jpg';Image.new('RGB',(2400,1200),'blue').save(source);original=source.read_bytes()
  registry=Registry(Policy(self.root,'plan',True,protected=[self.store.root]),self.store)
  result=registry.execute('fs.image',{'path':'photo.jpg'},StopToken())
  self.assertEqual(source.read_bytes(),original);self.assertEqual((result['width'],result['height']),(1920,960))
  p=Provider(ProviderConfig('lmstudio','http://127.0.0.1:1234/v1','chat',True,model='vision',vision=True))
  _,body=p.payload([{'role':'user','content':'describe','images':[result['path']]}],[])
  self.assertTrue(body['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
 def test_all_four_wire_protocols_encode_image_inputs(self):
  source=self.root/'input.png';Image.new('RGB',(8,8),'green').save(source)
  cases=[
   (ProviderConfig('openai','https://api.openai.com/v1','responses',False,model='vision',vision=True),'input','input_image'),
   (ProviderConfig('anthropic','https://api.anthropic.com/v1','anthropic',False,model='vision',vision=True),'messages','image'),
   (ProviderConfig('lmstudio','http://127.0.0.1:1234/v1','chat',True,model='vision',vision=True),'messages','image_url'),
   (ProviderConfig('ollama','http://127.0.0.1:11434','ollama',True,model='vision',vision=True),'messages','ollama'),
  ]
  for config,collection,expected in cases:
   with self.subTest(protocol=config.protocol):
    _,body=Provider(config,'fake').payload([{'role':'user','content':'describe','images':[str(source)]}],[])
    message=body[collection][0]
    if expected=='input_image':self.assertEqual(message['content'][1]['type'],expected)
    elif expected=='image':self.assertEqual(message['content'][1]['type'],expected)
    elif expected=='image_url':self.assertEqual(message['content'][1]['type'],expected)
    else:self.assertTrue(message['images'][0])
 def test_second_instance_rejected_without_corrupting_state(self):
  cid=self.store.conversation('one')
  with self.assertRaises(RuntimeError):Store(self.store.root,Redactor())
  self.assertEqual(self.store.conversations()[0]['id'],cid)
 def test_model_capabilities_are_saved_per_model(self):
  c=self.store.configs()['lmstudio'];c.model='text-model';c.tools=True;self.store.save_config(c)
  c.model='image-model';c.vision=True;self.store.save_config(c)
  profiles=self.store.get('model_profiles');self.assertFalse(profiles['lmstudio:text-model']['vision']);self.assertTrue(profiles['lmstudio:image-model']['vision'])
 def test_anthropic_current_auth_and_version(self):
  p=Provider(ProviderConfig('anthropic','https://api.anthropic.com/v1','anthropic',False,workspace_id='wrkspc_test'),'fake')
  self.assertEqual(p.headers()['anthropic-version'],'2026-10-21');self.assertEqual(p.headers()['Authorization'],'Bearer fake');self.assertEqual(p.headers()['anthropic-workspace-id'],'wrkspc_test')

if __name__=='__main__':unittest.main()
