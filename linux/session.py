"""Read-only desktop-session checks. Never unlock or alter the display manager."""
import os
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib


def call(bus, destination, path, interface, method, parameters=None):
    return bus.call_sync(destination, path, interface, method, parameters, None,
                         Gio.DBusCallFlags.NONE, 1000, None).unpack()


def session_state():
    result = dict(sessionType=os.environ.get('XDG_SESSION_TYPE', ''), locked=True,
                  sessionActive=False, lockStateKnown=False)
    try:
        system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        sessions = call(system, 'org.freedesktop.login1', '/org/freedesktop/login1',
                        'org.freedesktop.login1.Manager', 'ListSessions')[0]
        for sid, uid, _name, seat, path in sessions:
            if uid != os.getuid() or not seat:
                continue
            props = call(system, 'org.freedesktop.login1', path, 'org.freedesktop.DBus.Properties',
                         'GetAll', GLib.Variant('(s)', ('org.freedesktop.login1.Session',)))[0]
            if props.get('Active') and props.get('Class') == 'user':
                result.update(sessionType=props.get('Type', ''), sessionId=sid,
                              sessionActive=True, locked=bool(props.get('LockedHint', True)),
                              lockStateKnown='LockedHint' in props)
                break
        # GNOME's screen shield can be active before logind's hint is updated.
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        for dest, path in [('org.gnome.ScreenSaver', '/org/gnome/ScreenSaver'),
                           ('org.freedesktop.ScreenSaver', '/org/freedesktop/ScreenSaver')]:
            try:
                active = call(bus, dest, path, dest, 'GetActive')[0]
                result['locked'] = result['locked'] or active
                break
            except GLib.Error:
                continue
    except GLib.Error:
        pass
    # Never infer full desktop control from Xwayland's DISPLAY alone.
    if os.environ.get('XDG_SESSION_TYPE') == 'wayland' or os.environ.get('WAYLAND_DISPLAY'):
        result['sessionType'] = 'wayland'
    return result
