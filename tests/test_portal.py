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

if __name__=='__main__':unittest.main()
