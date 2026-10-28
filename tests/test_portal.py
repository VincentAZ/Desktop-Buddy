import unittest
import io
from types import SimpleNamespace
from desktop_buddy.desktop import Portal,DesktopError
from desktop_buddy.security import StopToken,Cancelled

class PortalRequestTests(unittest.TestCase):
 def portal(self,alternate=False,unrelated=False):
  from gi.repository import GLib,Gio
  p=object.__new__(Portal);p.GLib=GLib;p.Gio=Gio;p.closed=[];p.unsubscribed=[]
  def subscribe(*args):p.callback=args[6];return 42
  p.bus=SimpleNamespace(get_unique_name=lambda:':1.23',signal_subscribe=subscribe,signal_unsubscribe=p.unsubscribed.append)
  p.on_loop=lambda f:f()
  def call(interface,method,signature,args,path=None):
   if method=='Close':p.closed.append(path);return
   ident=args[-1]['handle_token'].unpack()
   p.expected=p.PATH+'/request/1_23/'+ident
   p.returned=p.PATH+'/request/1_23/legacy' if alternate else p.expected
   signal_path=p.PATH+'/request/1_23/another' if unrelated else p.returned
   p.callback(None,None,signal_path,None,None,GLib.Variant('(ua{sv})',(0,{'accepted':GLib.Variant('b',True)})))
   return GLib.Variant('(o)',(p.returned,))
  p.call=call
  return p
 def test_immediate_response_is_not_lost(self):
  p=self.portal()
  self.assertEqual(p.request('RemoteDesktop','Start','(a{sv})',(),{},StopToken()),{'accepted':True})
  self.assertEqual(p.unsubscribed,[42]);self.assertEqual(p.closed,[])
 def test_response_at_returned_legacy_path_is_accepted(self):
  p=self.portal(alternate=True)
  self.assertEqual(p.request('RemoteDesktop','Start','(a{sv})',(),{},StopToken()),{'accepted':True})
  self.assertNotEqual(p.expected,p.returned)
  self.assertEqual(p.last_request['returned_path'],p.returned)
 def test_unrelated_response_cannot_grant_access_and_timeout_closes_actual_request(self):
  p=self.portal(alternate=True,unrelated=True)
  with self.assertRaises(DesktopError):p.request('RemoteDesktop','Start','(a{sv})',(),{},StopToken(),timeout=0)
  self.assertEqual(p.closed,[p.returned]);self.assertEqual(p.unsubscribed,[42])
  self.assertEqual(p.last_request['status'],'timed_out')

class PortalInputTests(unittest.TestCase):
 def portal(self):
  p=object.__new__(Portal);p.session='/test/session';p.stream=42;p.size=(1920,1080);p.screen_position=(0,0);p.events=[]
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
 def test_global_pointer_coordinates_are_mapped_to_selected_monitor(self):
  p=self.portal();p.screen_position=(-1920,100)
  args={'action':'drag','x':-1900,'y':120,'to_x':-1700,'to_y':140}
  mapped=p.screen_input(args)
  self.assertEqual((mapped['x'],mapped['y'],mapped['to_x'],mapped['to_y']),(20,20,220,40))
  self.assertEqual(args['x'],-1900)
  with self.assertRaises(DesktopError):p.screen_input({'action':'click','x':30,'y':50})
 def test_unknown_monitor_position_does_not_guess_pointer_mapping(self):
  p=self.portal();p.screen_position=None
  with self.assertRaises(DesktopError):p.screen_input({'action':'click','x':30,'y':50})
  self.assertEqual(p.screen_input({'action':'key','keys':'Return'})['keys'],'Return')

class PortalDouble:
 def portal(self,restore_token=''):
  import gi
  from gi.repository import GLib
  p=object.__new__(Portal);p.GLib=GLib;p.session=None;p.stream=None;p.size=None;p.closed_subscription=None
  p.bus=SimpleNamespace(signal_unsubscribe=lambda *_:None)
  p.parent_window='';p.restore_token=restore_token;p.saved=[];p.save_token=p.saved.append;p.load_token=None;p.requests=[];p.granted_devices=3
  p.call=lambda *a,**k:None
  p.on_loop=lambda func:1
  def request(interface,method,signature,args,options,token,timeout=120):
   p.requests.append((interface,method,{k:v.unpack() for k,v in options.items()}))
   if method=='CreateSession':return {'session_handle':'/s/1'}
   if method=='Start':return {'streams':[(7,{'logical_size':(1920,1080)})],'devices':p.granted_devices,'restore_token':'tok-new'}
   return {}
  from PIL import Image
  image=io.BytesIO();Image.new('RGB',(16,16),'white').save(image,format='PNG')
  p._stream_frame=lambda *args:image.getvalue()
  p.request=request
  return p
class PortalConnectTests(PortalDouble,unittest.TestCase):
 def test_screen_frame_is_verified_before_connection_is_published(self):
  p=self.portal();capture=p._stream_frame;seen=[]
  def frame(session,stream,token,serial=None):
   self.assertIsNone(p.session);self.assertEqual(p.pending_session,session)
   seen.append(stream);return capture(session,stream,token,serial)
  p._stream_frame=frame
  result=p._connect(StopToken())
  self.assertEqual(seen,[7]);self.assertTrue(result['screen_verified'])
 def test_unreadable_frame_closes_permission_and_never_reports_connected(self):
  p=self.portal();closed=[];p.call=lambda *args:closed.append(args)
  p._stream_frame=lambda *args:b'not an image'
  with self.assertRaisesRegex(DesktopError,'unreadable screen image'):p._connect(StopToken())
  self.assertIsNone(p.session);self.assertIsNone(p.pending_session)
  self.assertEqual([args[-1] for args in closed],['/s/1'])
 def test_capture_failure_closes_permission(self):
  p=self.portal()
  def fail(*args):raise DesktopError('No frame arrived')
  p._stream_frame=fail
  with self.assertRaisesRegex(DesktopError,'No frame arrived'):p._connect(StopToken())
  self.assertIsNone(p.session);self.assertIsNone(p.pending_session)
 def test_permission_revoked_while_frame_arrives_cannot_become_ready(self):
  p=self.portal();capture=p._stream_frame
  def frame(*args):
   data=capture(*args);p._closed(None,None,'/s/1');return data
  p._stream_frame=frame
  with self.assertRaises(Cancelled):p._connect(StopToken())
  self.assertIsNone(p.session)
 def test_stream_serial_is_used_when_supplied_by_portal(self):
  p=self.portal();original=p.request;capture=p._stream_frame;seen=[]
  def request(interface,method,*args,**kwargs):
   response=original(interface,method,*args,**kwargs)
   if method=='Start':response['streams'][0][1]['pipewire-serial']=123456
   return response
  def frame(session,stream,token,serial):
   seen.append(serial);return capture(session,stream,token,serial)
  p.request=request;p._stream_frame=frame;p._connect(StopToken())
  self.assertEqual(seen,[123456]);self.assertEqual(p.stream_serial,123456)
 def test_pending_permission_is_never_reported_as_a_connected_session(self):
  p=self.portal();original=p.request
  def request(interface,method,*args,**kwargs):
   if method in ('SelectDevices','SelectSources','Start'):
    self.assertIsNone(p.session);self.assertEqual(p.pending_session,'/s/1')
   return original(interface,method,*args,**kwargs)
  p.request=request;p._connect(StopToken())
  self.assertEqual(p.session,'/s/1');self.assertIsNone(p.pending_session)
 def test_stopping_pending_permission_closes_its_session_without_connecting(self):
  p=self.portal();original=p.request;closed=[]
  p.call=lambda *args:closed.append(args)
  def request(interface,method,*args,**kwargs):
   if method=='Start':p.close()
   return original(interface,method,*args,**kwargs)
  p.request=request
  with self.assertRaises(Cancelled):p._connect(StopToken())
  self.assertIsNone(p.session);self.assertIsNone(p.pending_session)
  self.assertEqual([args[-1] for args in closed],['/s/1'])
 def test_input_only_session_needs_no_screen_stream_but_still_requires_permission(self):
  p=self.portal();p.capture_screen=False
  original=p.request
  def request(interface,method,*args,**kwargs):
   result=original(interface,method,*args,**kwargs)
   if method=='Start':result.pop('streams')
   return result
  p.request=request
  result=p._connect(StopToken())
  self.assertTrue(result['connected']);self.assertIsNone(p.stream)
  self.assertEqual([m for _,m,_ in p.requests],['CreateSession','SelectDevices','Start'])
  with self.assertRaises(DesktopError):p.move(5,5,StopToken())
 def test_combined_session_still_rejects_missing_screen(self):
  p=self.portal();original=p.request
  def request(interface,method,*args,**kwargs):
   result=original(interface,method,*args,**kwargs)
   if method=='Start':result.pop('streams')
   return result
  p.request=request
  with self.assertRaisesRegex(DesktopError,'No screen was shared'):p._connect(StopToken())
 def test_first_connect_asks_gnome_to_remember_and_stores_token(self):
  p=self.portal();result=p._connect(StopToken())
  devices=next(o for i,m,o in p.requests if m=='SelectDevices')
  self.assertEqual(devices['persist_mode'],2);self.assertNotIn('restore_token',devices)
  self.assertEqual(p.saved,['tok-new']);self.assertTrue(result['remembered']);self.assertEqual(p.stream,7)
 def test_reconnect_sends_remembered_token(self):
  p=self.portal('tok-old');p._connect(StopToken())
  devices=next(o for i,m,o in p.requests if m=='SelectDevices')
  self.assertEqual(devices['restore_token'],'tok-old');self.assertEqual(p.saved,['tok-new'])

class PortalGrantWithoutControlTests(PortalDouble,unittest.TestCase):
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
