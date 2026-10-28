import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desktop_buddy import __main__ as launcher


class StartupTests(unittest.TestCase):
    def launch(self, ui):
        with patch.object(sys, 'argv', ['desktop-buddy', '--smoke-test']), \
             patch.object(launcher, 'enter_own_scope'), \
             patch.dict(sys.modules, {'desktop_buddy.ui': ui}):
            with self.assertRaises(SystemExit) as raised:
                launcher.main()
        return raised.exception.code

    def test_no_display_fails_before_creating_a_profile(self):
        app = Mock()
        ui = SimpleNamespace(BuddyApp=app, Gdk=SimpleNamespace(Display=Mock()))
        ui.Gdk.Display.get_default.return_value = None
        with patch('sys.stderr'):
            self.assertEqual(self.launch(ui), 2)
        app.assert_not_called()

    def test_signal_handler_failure_cannot_look_like_a_successful_launch(self):
        app = Mock(startup_error='layout failed')
        app.run.return_value = 0
        ui = SimpleNamespace(BuddyApp=Mock(return_value=app), Gdk=Mock())
        self.assertEqual(self.launch(ui), 1)

    def test_successful_launch_keeps_gtk_exit_code(self):
        app = Mock(startup_error=None)
        app.run.return_value = 0
        ui = SimpleNamespace(BuddyApp=Mock(return_value=app), Gdk=Mock())
        self.assertEqual(self.launch(ui), 0)
