import os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from desktop_buddy.desktop import InputTarget,DesktopError,Portal
from desktop_buddy.security import Policy,Redactor,StopToken,Denied
from desktop_buddy.state import Store
from desktop_buddy.tools import Registry


class Node:
    def __init__(self,name,role,parent=None):
        self.name=name;self.role=role;self.parent=parent;self.states=set()
        self.rect=SimpleNamespace(x=0,y=0,width=500,height=300)
        self.focus_calls=0
        self.value='original';self.writes=0
    def get_name(self):return self.name
    def get_role_name(self):return self.role
    def get_parent(self):return self.parent
    def get_application(self):return SimpleNamespace(get_process_id=lambda:os.getpid()+1000)
    def get_state_set(self):return SimpleNamespace(contains=lambda state:state in self.states)
    def get_component_iface(self):return self
    def get_extents(self,*_):return self.rect
    def grab_focus(self):
        self.focus_calls+=1;self.states.add('focused');self.parent.states.add('active');return True
    def get_editable_text_iface(self):return self
    def get_text_iface(self):return self
    def get_character_count(self):return len(self.value)
    def set_text_contents(self,text):self.value=text;self.writes+=1;return True


class InputTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.work=self.root/'work';self.work.mkdir()
        self.store=Store(self.root/'state',Redactor());self.token=StopToken()
        self.frame=Node('Editor','frame',Node('Editor app','application'))
        self.entry=Node('Report','text',self.frame)
        self.backend=SimpleNamespace(A=SimpleNamespace(StateType=SimpleNamespace(FOCUSED='focused',ACTIVE='active',DEFUNCT='defunct'),CoordType=SimpleNamespace(SCREEN=0)))
        self.backend.A.Text=SimpleNamespace(get_text=lambda obj,start,end:obj.value[start:end])
        self.a11y=Mock();self.a11y.input_target.side_effect=lambda path,name:InputTarget(self.backend,self.entry,name)
        self.portal=Mock()
        self.arguments={'action':'type','text':'hello','target_path':'0/0/1','expected_name':'Report','expected_window':'Editor'}

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def registry(self,approve):return Registry(Policy(self.work,'execute',False,approve),self.store,desktop=self.portal,accessibility=self.a11y)

    def test_target_is_pinned_before_approval_and_focus_restored_before_dispatch(self):
        def approve(*_):
            self.a11y.input_target.assert_called_once()
            self.entry.states.clear();self.frame.states.clear();return True
        def dispatch(args,token,verify_target):
            verify_target();self.assertEqual(self.entry.focus_calls,1);return {'sent':True}
        self.portal.input.side_effect=dispatch
        self.registry(approve).execute('desktop.input',self.arguments,self.token)
        self.portal.input.assert_called_once()

    def test_target_removed_or_renamed_during_approval_never_receives_input(self):
        for change in (lambda:self.entry.states.add('defunct'),lambda:setattr(self.entry,'name','Other control')):
            with self.subTest(change=change):
                self.entry.name='Report';self.entry.states.clear()
                def approve(*_):change();return True
                with self.assertRaises(DesktopError):self.registry(approve).execute('desktop.input',self.arguments,self.token)
                self.portal.input.assert_not_called();self.assertEqual(self.entry.focus_calls,0)

    def test_denied_action_does_not_restore_focus_or_send_input(self):
        with self.assertRaises(Denied):self.registry(lambda *_:False).execute('desktop.input',self.arguments,self.token)
        self.portal.input.assert_not_called();self.assertEqual(self.entry.focus_calls,0)

    def test_window_moved_during_approval_is_rejected(self):
        def approve(*_):self.frame.rect.x=20;return True
        with self.assertRaisesRegex(DesktopError,'moved during approval'):
            self.registry(approve).execute('desktop.input',self.arguments,self.token)
        self.portal.input.assert_not_called()

    def test_pointer_outside_target_is_rejected(self):
        args={**self.arguments,'action':'click','x':600,'y':10};args.pop('text')
        with self.assertRaisesRegex(DesktopError,'outside the approved target'):
            self.registry(lambda *_:True).execute('desktop.input',args,self.token)
        self.portal.input.assert_not_called()

    def test_scroll_requires_an_explicit_pointer_position_before_approval(self):
        approve=Mock(return_value=True);args={**self.arguments,'action':'scroll','steps':2};args.pop('text')
        with self.assertRaisesRegex(ValueError,'Missing arguments.x'):
            self.registry(approve).execute('desktop.input',args,self.token)
        approve.assert_not_called();self.portal.input.assert_not_called()

    def test_lost_focus_stops_typing_and_releases_the_last_key(self):
        portal=object.__new__(Portal);portal.session='fixture';portal.notify=Mock();portal.call=Mock()
        guard=Mock(side_effect=[None,None,DesktopError('Focus changed')])
        with self.assertRaises(DesktopError):portal.input({'action':'type','text':'ab'},self.token,guard)
        self.assertEqual(portal.notify.call_count,1)
        self.assertEqual(portal.call.call_count,1)
        self.assertEqual(portal.call.call_args.args[-1][-1],0)

    def test_direct_text_uses_exact_readback_without_focus_or_physical_input(self):
        args={k:v for k,v in self.arguments.items() if k!='action'};args['text']='Updated ✓'
        result=self.registry(lambda *_:True).execute('desktop.set_text',args,self.token)
        self.assertTrue(result['verified']);self.assertEqual(self.entry.value,'Updated ✓')
        self.assertEqual(self.entry.focus_calls,0);self.portal.input.assert_not_called()

    def test_direct_text_does_not_overwrite_a_field_changed_during_approval(self):
        def approve(*_):self.entry.value='Someone else edited this';return True
        args={k:v for k,v in self.arguments.items() if k!='action'}
        with self.assertRaisesRegex(DesktopError,'field changed during approval'):
            self.registry(approve).execute('desktop.set_text',args,self.token)
        self.assertEqual(self.entry.value,'Someone else edited this');self.assertEqual(self.entry.writes,0)

    def test_direct_text_denial_and_password_field_prevent_writes(self):
        args={k:v for k,v in self.arguments.items() if k!='action'}
        with self.assertRaises(Denied):self.registry(lambda *_:False).execute('desktop.set_text',args,self.token)
        self.assertEqual(self.entry.writes,0)
        self.entry.role='password text'
        self.entry.get_text_iface=Mock(side_effect=AssertionError('Password contents must not be read'))
        with self.assertRaisesRegex(DesktopError,'Enter passwords yourself'):
            self.registry(lambda *_:True).execute('desktop.set_text',args,self.token)
        self.entry.get_text_iface.assert_not_called();self.assertEqual(self.entry.writes,0)

    def test_legitimate_title_change_during_typing_keeps_pinned_control(self):
        target=InputTarget(self.backend,self.entry,'Report');verify=target.restore(self.token,self.arguments)
        self.frame.name='*Editor — unsaved'
        verify()

    def test_wrong_window_is_rejected_before_approval(self):
        approve=Mock(return_value=True)
        with self.assertRaisesRegex(Denied,'different window'):
            self.registry(approve).execute('desktop.input',{**self.arguments,'expected_window':'Other editor'},self.token)
        approve.assert_not_called();self.portal.input.assert_not_called()

    def test_single_window_switch_fallback_only_for_previously_focused_target(self):
        self.entry.states.add('focused');self.frame.states.add('active')
        target=InputTarget(self.backend,self.entry,'Report')
        self.entry.grab_focus=Mock(return_value=False);self.frame.states.clear()
        restore=Mock(side_effect=lambda:self.frame.states.add('active'))
        target.restore(self.token,self.arguments,restore)()
        restore.assert_called_once()
        self.frame.states.clear();target=InputTarget(self.backend,self.entry,'Report')
        restore.reset_mock()
        with self.assertRaises(DesktopError):target.restore(self.token,self.arguments,restore)
        restore.assert_not_called()

    def test_pointer_target_in_active_window_does_not_need_keyboard_focus(self):
        self.frame.states.add('active');self.entry.role='drawing area'
        self.entry.grab_focus=Mock(return_value=False)
        args={**self.arguments,'action':'click','x':10,'y':10}
        target=InputTarget(self.backend,self.entry,'Report')
        target.restore(self.token,args)()
        self.entry.grab_focus.assert_not_called()
        self.assertNotIn('focused',self.entry.states)

    def test_pointer_can_return_to_its_previously_active_window_only(self):
        self.entry.grab_focus=Mock(return_value=False)
        args={**self.arguments,'action':'click','x':10,'y':10}
        self.frame.states.add('active');target=InputTarget(self.backend,self.entry,'Report')
        self.frame.states.clear()
        restore=Mock(side_effect=lambda:self.frame.states.add('active'))
        target.restore(self.token,args,restore)()
        restore.assert_called_once()
        self.frame.states.clear();target=InputTarget(self.backend,self.entry,'Report');restore.reset_mock()
        with self.assertRaises(DesktopError):target.restore(self.token,args,restore)
        restore.assert_not_called()

    def test_pointer_rechecks_window_geometry_before_later_events(self):
        self.frame.states.add('active')
        target=InputTarget(self.backend,self.entry,'Report')
        verify=target.restore(self.token,{**self.arguments,'action':'click','x':10,'y':10})
        self.frame.rect.x=20
        with self.assertRaisesRegex(DesktopError,'window moved'):verify()

    def test_drag_lost_focus_stops_motion_and_releases_mouse_button(self):
        portal=object.__new__(Portal);portal.session='fixture';portal.notify=Mock();portal.call=Mock()
        portal.move=Mock();portal.check_unlocked=Mock()
        guard=Mock(side_effect=[None,None,None,DesktopError('Focus changed')])
        with self.assertRaisesRegex(DesktopError,'Focus changed'):
            portal.input({'action':'drag','x':10,'y':10,'to_x':100,'to_y':100},self.token,guard)
        self.assertEqual(portal.move.call_count,2)
        portal.notify.assert_called_once()
        self.assertEqual(portal.call.call_args.args[-1][-2:],(272,0))

    def test_double_click_checks_focus_before_the_second_press(self):
        portal=object.__new__(Portal);portal.session='fixture';portal.notify=Mock();portal.call=Mock()
        portal.move=Mock();portal.check_unlocked=Mock()
        guard=Mock(side_effect=[None,None,DesktopError('Focus changed')])
        with self.assertRaisesRegex(DesktopError,'Focus changed'):
            portal.input({'action':'click','x':10,'y':10,'double':True},self.token,guard)
        portal.notify.assert_called_once()
        self.assertEqual(portal.call.call_args.args[-1][-2:],(272,0))

    def test_window_keyboard_dispatches_without_a_focusable_child(self):
        self.frame.states.add('active');self.frame.grab_focus=Mock(side_effect=AssertionError('No focusable control'))
        self.a11y.input_target.side_effect=lambda path,name:InputTarget(self.backend,self.frame,name)
        args={**self.arguments,'action':'key','keys':'Ctrl+s','expected_name':'Editor','keyboard_scope':'window'}
        self.portal.input.side_effect=lambda args,token,verify_target:(verify_target() or {'sent':True})
        self.registry(lambda *_:True).execute('desktop.input',args,self.token)
        self.portal.input.assert_called_once();self.frame.grab_focus.assert_not_called()

    def test_window_keyboard_requires_explicit_window_and_active_state_before_approval(self):
        approve=Mock(return_value=True)
        args={**self.arguments,'keyboard_scope':'window'}
        with self.assertRaisesRegex(DesktopError,'window itself'):
            self.registry(approve).execute('desktop.input',args,self.token)
        self.a11y.input_target.side_effect=lambda path,name:InputTarget(self.backend,self.frame,name)
        with self.assertRaisesRegex(DesktopError,'must be active'):
            self.registry(approve).execute('desktop.input',{**args,'expected_name':'Editor'},self.token)
        self.frame.states.add('active')
        with self.assertRaisesRegex(DesktopError,'only for keys'):
            self.registry(approve).execute('desktop.input',{**args,'expected_name':'Editor','action':'click','x':10,'y':10},self.token)
        approve.assert_not_called();self.portal.input.assert_not_called()

    def test_window_keyboard_restores_previous_window_and_stops_when_focus_leaves(self):
        self.frame.states.add('active');target=InputTarget(self.backend,self.frame,'Editor')
        self.frame.grab_focus=Mock(return_value=False);self.frame.states.clear()
        restore=Mock(side_effect=lambda:self.frame.states.add('active'))
        verify=target.restore(self.token,{'action':'type','keyboard_scope':'window','text':'hello'},restore)
        restore.assert_called_once();verify()
        self.frame.states.clear()
        with self.assertRaisesRegex(DesktopError,'lost focus'):verify()

    def test_window_scope_does_not_relax_default_control_focus(self):
        self.frame.states.add('active');self.frame.grab_focus=Mock(return_value=False)
        target=InputTarget(self.backend,self.frame,'Editor')
        with self.assertRaisesRegex(DesktopError,'could not regain focus'):
            target.restore(self.token,{'action':'key','keys':'Ctrl+s'})

    def test_shortcut_rechecks_focus_before_each_key_and_releases_modifier(self):
        portal=object.__new__(Portal);portal.session='fixture';portal.notify=Mock();portal.call=Mock();portal.check_unlocked=Mock()
        guard=Mock(side_effect=[None,None,DesktopError('Focus changed')])
        with self.assertRaisesRegex(DesktopError,'Focus changed'):
            portal.input({'action':'key','keys':'Ctrl+s'},self.token,guard)
        portal.notify.assert_called_once()
        self.assertEqual(portal.call.call_args.args[-1][-1],0)


if __name__=='__main__':unittest.main()


class ScreenshotCoordinateTests(unittest.TestCase):
    """Apps without accessibility information are driven from the screenshot."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.work=self.root/'work';self.work.mkdir()
        self.store=Store(self.root/'state',Redactor());self.token=StopToken()
        portal=Portal.__new__(Portal);portal.session='live';portal.stream=7
        portal.size=[5120,1440];portal.screen_position=[1080,0]
        portal.last_shot={'width':1920,'height':540,'logical_size':[5120,1440],'screen_position':[1080,0]}
        portal.sent=[];portal.connects=0
        portal.input=lambda args,token,verify_target=None:portal.sent.append(dict(args)) or {'sent':True,'verified':False}
        portal.connect=lambda token:portal.connects.__add__(1)
        self.portal=portal

    def tearDown(self):self.store.close();self.tmp.cleanup()

    def registry(self,mode='execute',approve=lambda *_:True,accessibility=None):
        return Registry(Policy(self.work,mode,False,approve),self.store,desktop=self.portal,accessibility=accessibility)

    def test_screenshot_pixels_are_converted_to_the_shared_screen(self):
        approvals=[]
        r=self.registry(approve=lambda name,detail,token:approvals.append(name) or True)
        result=r.execute('desktop.input',{'action':'click','x':960,'y':270},self.token)
        self.assertTrue(result['sent'])
        # 960/1920 of 5120 = 2560 logical on the shared monitor (its own origin), 270/540 of 1440 = 720
        self.assertEqual((self.portal.sent[0]['x'],self.portal.sent[0]['y']),(2560.0,720.0))
        self.assertEqual(approvals,['desktop.input'])
        r.execute('desktop.input',{'action':'drag','x':0,'y':0,'to_x':1920,'to_y':540},self.token)
        self.assertEqual((self.portal.sent[1]['to_x'],self.portal.sent[1]['to_y']),(5119.0,1439.0))

    def test_screen_coordinates_from_accessibility_are_offset_by_the_monitor_position(self):
        r=self.registry()
        r.execute('desktop.input',{'action':'click','x':1080+100,'y':50,'coordinates':'screen'},self.token)
        self.assertEqual((self.portal.sent[0]['x'],self.portal.sent[0]['y']),(100,50))
        with self.assertRaises(DesktopError):
            r.execute('desktop.input',{'action':'click','x':10,'y':50,'coordinates':'screen'},self.token)

    def test_positions_outside_the_screenshot_and_missing_screenshots_are_refused_before_approval(self):
        approvals=[]
        r=self.registry(approve=lambda name,*_:approvals.append(name) or True)
        with self.assertRaises(DesktopError):r.execute('desktop.input',{'action':'click','x':5000,'y':10},self.token)
        self.portal.last_shot=None
        with self.assertRaises(DesktopError):r.execute('desktop.input',{'action':'click','x':10,'y':10},self.token)
        self.assertEqual(approvals,[]);self.assertEqual(self.portal.sent,[])

    def test_keys_and_text_need_no_coordinates_and_set_text_still_needs_a_target(self):
        r=self.registry()
        r.execute('desktop.input',{'action':'key','keys':'Ctrl+s'},self.token)
        r.execute('desktop.input',{'action':'type','text':'hello'},self.token)
        self.assertEqual([a['action'] for a in self.portal.sent],['key','type'])
        from desktop_buddy.security import InvalidArguments
        with self.assertRaises(InvalidArguments):r.execute('desktop.set_text',{'text':'x'},self.token)
        with self.assertRaises(InvalidArguments):r.execute('desktop.input',{'action':'click','x':1},self.token)

    def test_input_connects_screen_sharing_itself_when_not_connected(self):
        self.portal.session=None;connects=[]
        def connect(token):connects.append(True);self.portal.session='live'
        self.portal.connect=connect
        r=self.registry()
        r.execute('desktop.input',{'action':'key','keys':'Return'},self.token)
        self.assertEqual(connects,[True]);self.assertEqual(self.portal.sent[0]['action'],'key')

    def test_discuss_only_and_offline_block_screen_input(self):
        with self.assertRaises(Denied):self.registry(mode='plan').execute('desktop.input',{'action':'key','keys':'Return'},self.token)
        offline=Registry(Policy(self.work,'execute',True),self.store,desktop=self.portal)
        with self.assertRaises(Denied):offline.execute('desktop.input',{'action':'key','keys':'Return'},self.token)
        self.assertEqual(self.portal.sent,[])
