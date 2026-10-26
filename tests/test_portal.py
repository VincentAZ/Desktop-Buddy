import unittest
from desktop_buddy.desktop import Portal,DesktopError
from desktop_buddy.security import StopToken,Cancelled

class PortalInputTests(unittest.TestCase):
 def portal(self):
  p=object.__new__(Portal);p.session='/test/session';p.stream=42;p.size=(1920,1080);p.events=[]
  p.call=lambda *args,**kw:p.events.append(args)
  return p
 def test_click_uses_logical_coordinates_and_releases_button(self):
  p=self.portal();r=p.input({'action':'click','x':30,'y':50},StopToken())
  self.assertFalse(r['verified']);self.assertEqual(p.events[0][1],'NotifyPointerMotionAbsolute')
  self.assertEqual(p.events[0][3][-3:],(42,30.0,50.0));self.assertEqual(p.events[-1][3][-1],0)
 def test_coordinates_outside_shared_monitor_rejected(self):
  with self.assertRaises(DesktopError):self.portal().move(2000,0,StopToken())
 def test_cancel_during_click_releases_button(self):
  p=self.portal();t=StopToken()
  def call(*args,**kw):
   p.events.append(args)
   if args[1]=='NotifyPointerButton' and args[3][-1]==1:t.stop()
  p.call=call
  with self.assertRaises(Cancelled):p.input({'action':'click','x':5,'y':5},t)
  self.assertEqual(p.events[-1][3][-1],0)
 def test_no_input_without_granted_session(self):
  p=self.portal();p.session=None
  with self.assertRaises(DesktopError):p.move(4,4,StopToken())
  self.assertFalse(p.events)

class PortalConnectTests(unittest.TestCase):
 def portal(self,restore_token=''):
  import gi
  from gi.repository import GLib
  p=object.__new__(Portal);p.GLib=GLib;p.session=None;p.stream=None;p.size=None;p.closed_subscription=None
  p.parent_window='';p.restore_token=restore_token;p.saved=[];p.save_token=p.saved.append;p.load_token=None;p.requests=[];p.granted_devices=3
  p.call=lambda *a,**k:None
  p.on_loop=lambda func:1
  def request(interface,method,signature,args,options,token,timeout=120):
   p.requests.append((interface,method,{k:v.unpack() for k,v in options.items()}))
   if method=='CreateSession':return {'session_handle':'/s/1'}
   if method=='Start':return {'streams':[(7,{'logical_size':(1920,1080)})],'devices':p.granted_devices,'restore_token':'tok-new'}
   return {}
  p.request=request
  return p
 def test_first_connect_asks_gnome_to_remember_and_stores_token(self):
  p=self.portal();result=p._connect(StopToken())
  devices=next(o for i,m,o in p.requests if m=='SelectDevices')
  self.assertEqual(devices['persist_mode'],2);self.assertNotIn('restore_token',devices)
  self.assertEqual(p.saved,['tok-new']);self.assertTrue(result['remembered']);self.assertEqual(p.stream,7)
 def test_reconnect_sends_remembered_token(self):
  p=self.portal('tok-old');p._connect(StopToken())
  devices=next(o for i,m,o in p.requests if m=='SelectDevices')
  self.assertEqual(devices['restore_token'],'tok-old');self.assertEqual(p.saved,['tok-new'])

class PortalGrantWithoutControlTests(PortalConnectTests):
 def test_fresh_grant_without_remote_interaction_explains_what_to_switch_on(self):
  p=self.portal();p.granted_devices=0
  with self.assertRaises(DesktopError) as ctx:p._connect(StopToken())
  self.assertIn('Allow Remote Interaction',str(ctx.exception));self.assertEqual(p.saved,[])
 def test_remembered_grant_without_control_is_forgotten_and_dialog_retried(self):
  p=self.portal('tok-old');calls={'n':0}
  def request(interface,method,signature,args,options,token,timeout=120):
   p.requests.append((interface,method,{k:v.unpack() for k,v in options.items()}))
   if method=='CreateSession':return {'session_handle':'/s/%d'%len(p.requests)}
   if method=='Start':
    calls['n']+=1
    return {'streams':[(7,{'logical_size':(1920,1080)})],'devices':0 if calls['n']==1 else 3,'restore_token':'tok-new'}
   return {}
  p.request=request
  p._connect(StopToken())
  selects=[o for i,m,o in p.requests if m=='SelectDevices']
  self.assertEqual(selects[0].get('restore_token'),'tok-old');self.assertNotIn('restore_token',selects[1])
  self.assertEqual(p.saved,['','tok-new'])

if __name__=='__main__':unittest.main()
