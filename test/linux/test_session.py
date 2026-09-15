import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
import session


class SessionTests(unittest.TestCase):
    def read(self, properties, shield=False, environment=None):
        def reply(_bus, _destination, _path, _interface, method, _parameters=None):
            if method == 'ListSessions':
                return ([('test-session', os.getuid(), 'test-user', 'seat0', '/test/session')],)
            if method == 'GetAll':
                return (properties,)
            return (shield,)
        with patch.dict(os.environ, environment or {}, clear=True), \
             patch('session.Gio.bus_get_sync'), patch('session.call', side_effect=reply):
            return session.session_state()

    def test_active_unlocked_x11_session(self):
        result = self.read(dict(Active=True, Class='user', Type='x11', LockedHint=False))
        self.assertTrue(result['sessionActive'])
        self.assertTrue(result['lockStateKnown'])
        self.assertFalse(result['locked'])
        self.assertEqual(result['sessionType'], 'x11')

    def test_screen_shield_wins_over_delayed_logind_hint(self):
        result = self.read(dict(Active=True, Class='user', Type='x11', LockedHint=False), shield=True)
        self.assertTrue(result['locked'])

    def test_missing_lock_hint_and_inactive_sessions_fail_closed(self):
        for properties in [dict(Active=True, Class='user', Type='x11'),
                           dict(Active=False, Class='user', Type='x11', LockedHint=False)]:
            with self.subTest(properties=properties):
                result = self.read(properties)
                self.assertTrue(result['locked'])
                self.assertFalse(result['lockStateKnown'])

    def test_dbus_failure_fails_closed(self):
        with patch('session.Gio.bus_get_sync', side_effect=session.GLib.Error('unavailable')):
            result = session.session_state()
        self.assertTrue(result['locked'])
        self.assertFalse(result['sessionActive'])
        self.assertFalse(result['lockStateKnown'])

    def test_xwayland_display_is_not_mistaken_for_full_x11_support(self):
        for environment in [dict(DISPLAY=':99', XDG_SESSION_TYPE='wayland'),
                            dict(DISPLAY=':99', WAYLAND_DISPLAY='wayland-0')]:
            with self.subTest(environment=environment):
                result = self.read(dict(Active=True, Class='user', Type='x11', LockedHint=False), environment=environment)
                self.assertEqual(result['sessionType'], 'wayland')


if __name__ == '__main__':
    unittest.main()
