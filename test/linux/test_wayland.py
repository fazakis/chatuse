"""Portal boundary tests without a display or a consent prompt."""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import gi
gi.require_version('Gdk', '3.0')
from wayland import WaylandDesktop, stream_geometry, RD
from core import ChatuseError


class WaylandTests(unittest.TestCase):
    def desktop(self):
        d = WaylandDesktop.__new__(WaylandDesktop)
        d.session='/session/test';d.node=42;d.frame=dict(x=0,y=0,width=800,height=600)
        d.held_keys=set();d.held_buttons=set();d.call=Mock();d.guard=Mock()
        return d

    def test_requires_one_monitor_and_logical_geometry(self):
        for streams in [[],[(1,{} )],[(1,{'size':(0,100)})],[(1,{'size':(100,100),'source_type':2})],[(1,{}),(2,{})]]:
            with self.subTest(streams=streams), self.assertRaises(ChatuseError):stream_geometry(streams)
        node, frame=stream_geometry([(12,{'size':(800,600),'position':(-800,50),'source_type':1})])
        self.assertEqual(node,12);self.assertEqual(frame,dict(x=0,y=0,width=800,height=600))

    def test_absolute_motion_uses_stream_local_coordinates(self):
        d=self.desktop();d.motion(799,599)
        args=d.call.call_args.args
        self.assertEqual(args[1],'NotifyPointerMotionAbsolute');self.assertEqual(args[3],('/session/test',{},42,799.,599.))
        for point in [(-1,0),(800,0),(0,600)]:
            with self.assertRaises(ChatuseError):d.motion(*point)
        self.assertEqual(d.call.call_count,1)

    def test_revoked_session_does_not_send_input(self):
        d=self.desktop();d.session=None
        with self.assertRaises(ChatuseError):d.motion(10,10)
        d.call.assert_not_called()

    def test_button_mapping_and_release_after_failure(self):
        d=self.desktop();d.button(1,True);d.key(65,True);d.release()
        self.assertEqual(d.held_buttons,set());self.assertEqual(d.held_keys,set())
        self.assertEqual(d.call.call_args_list[0].args[3],('/session/test',{},272,1))
        self.assertEqual(d.call.call_args_list[-1].args[3],('/session/test',{},65,0))

    def test_typing_checks_cancel_before_each_character(self):
        d=self.desktop();guard=Mock(side_effect=[None,ChatuseError('STOPPED','stop')])
        with self.assertRaises(ChatuseError):d.type_text('ab',guard)
        self.assertEqual(d.call.call_count,2);self.assertEqual(d.held_keys,set())

    def test_unicode_keysyms_and_shortcut_cleanup(self):
        d=self.desktop();d.insert_unicode=Mock();check=Mock();d.type_text('中',check)
        d.insert_unicode.assert_called_once_with('中',check)
        d.call.assert_not_called()
        d.shortcut('a',['ctrl'],Mock());self.assertFalse(d.held_keys)

    def test_no_silent_app_capture_or_focus_stealing(self):
        d=self.desktop();d.active_pid=lambda:20
        with self.assertRaises(ChatuseError):d.focus(21,Mock())
        for args in [dict(pid=21),dict(app='frontmost'),dict(windowId=0),dict(displayId=1)]:
            with self.assertRaises(ChatuseError):d.capture(args)
        d.call.assert_not_called()

    def test_failed_press_is_tracked_for_cleanup(self):
        d=self.desktop();d.call.side_effect=ChatuseError('PORTAL_ERROR','failure')
        with self.assertRaises(ChatuseError):d.key(65,True)
        self.assertIn(65,d.held_keys)
        d.call.side_effect=None;d.release()
        self.assertEqual(d.call.call_args.args[3],('/session/test',{},65,0))

    def test_locked_or_unknown_session_refuses_input(self):
        from main import Driver
        driver=Driver.__new__(Driver);driver.desktop=self.desktop()
        for locked,known,active in [(True,True,True),(False,False,True),(False,True,False)]:
            driver.session=lambda:dict(sessionType='wayland',locked=locked,lockStateKnown=known,sessionActive=active)
            with self.assertRaises(ChatuseError) as e:driver.require_session()
            self.assertEqual(e.exception.code,'SESSION_LOCKED')
        driver.desktop.call.assert_not_called()

    def test_old_sharing_session_screenshot_rejected(self):
        from main import Driver
        from core import References
        driver=Driver.__new__(Driver);driver.wayland=True;driver.desktop=self.desktop()
        driver.desktop.generation=2;driver.images=References()
        ref=driver.images.add(dict(generation=1))
        with self.assertRaises(ChatuseError) as e:driver.target_point(dict(x=1,y=1,screenshotId=ref))
        self.assertEqual(e.exception.code,'STALE_SNAPSHOT')
        driver.desktop.call.assert_not_called()

    def request_case(self, response=None):
        d=self.desktop();d.bus=Mock();d.bus.get_unique_name.return_value=':1.42'
        d.deadline=time.monotonic()+1
        def subscribe(*args):
            d.callback=args[-1];return 7
        d.bus.signal_subscribe.side_effect=subscribe
        def request(_interface,_method,_signature,values,*args):
            path='/org/freedesktop/portal/desktop/request/1_42/'+values[-1]['handle_token'].unpack()
            if response is not None:
                class Reply:
                    def unpack(self):return response
                d.callback(None,None,None,None,None,Reply())
            return (path,)
        d.call.side_effect=request
        return d

    def test_permission_denial_unsubscribes_without_input(self):
        d=self.request_case((1,{}))
        with self.assertRaises(ChatuseError) as e:d.request(RD,'Start','(osa{sv})',('/session','',{}))
        self.assertEqual(e.exception.code,'PERMISSION_DENIED')
        d.bus.signal_unsubscribe.assert_called_once_with(7)
        self.assertEqual(d.call.call_count,1)

    def test_permission_timeout_closes_outstanding_request(self):
        d=self.request_case();d.deadline=0
        # Close has no options dictionary.
        original=d.call.side_effect
        d.call.side_effect=lambda *a: () if a[1]=='Close' else original(*a)
        with self.assertRaises(ChatuseError) as e:d.request(RD,'Start','(osa{sv})',('/session','',{}))
        self.assertEqual(e.exception.code,'PERMISSION_TIMEOUT')
        self.assertEqual(d.call.call_args.args[1],'Close')
        d.bus.signal_unsubscribe.assert_called_once_with(7)

if __name__=='__main__':unittest.main()
