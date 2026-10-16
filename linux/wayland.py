"""Consent-based Wayland backend. No Xwayland fallback or privileged input.

A helper owns one RemoteDesktop/ScreenCast session and one selected monitor.
Coordinates are logical points relative to that monitor (origin 0,0), NOT a
compositor-global desktop. Window geometry/management and clipboard are not
portable Wayland APIs and are intentionally unsupported here.
"""
import os
import time
import uuid
from pathlib import Path
import gi
gi.require_version('Gio', '2.0')
gi.require_version('Atspi', '2.0')
from gi.repository import Gio, GLib, Atspi, Gdk
from PIL import Image
from core import fail, contains, ChatuseError

DEST = 'org.freedesktop.portal.Desktop'
PATH = '/org/freedesktop/portal/desktop'
RD = 'org.freedesktop.portal.RemoteDesktop'
SC = 'org.freedesktop.portal.ScreenCast'


def stream_geometry(streams):
    if len(streams) != 1:
        fail('STREAM_UNSUPPORTED', 'Select exactly one monitor in the sharing dialog.')
    node, props = streams[0]
    size = props.get('size', ())
    if props.get('source_type', 1) != 1 or len(size) != 2 or min(size) <= 0:
        fail('STREAM_UNSUPPORTED', 'A monitor stream with logical size metadata is required.')
    return node, dict(x=0, y=0, width=size[0], height=size[1])


class WaylandDesktop:
    def __init__(self, root, guard):
        self.root, self.guard = root, guard
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.session = None
        self.node = None
        self.frame = None
        self.generation = 0
        self.pipeline = None
        self.pipe_fd = None
        self.closed_subscription = None
        self.capture_size = None
        self.held_buttons, self.held_keys = set(), set()
        self.deadline = 0
        self.token_file = root / 'runtime/wayland-restore-token'

    def call(self, interface, method, signature, values, path=PATH):
        try:
            return self.bus.call_sync(DEST, path, interface, method,
                                     GLib.Variant(signature, values), None,
                                     Gio.DBusCallFlags.NONE, 5000, None).unpack()
        except GLib.Error:
            fail('PORTAL_ERROR', 'The desktop portal rejected the operation. Check sharing permission and portal availability.')

    def request(self, interface, method, signature, values):
        options = values[-1]
        token = 'chatuse_' + uuid.uuid4().hex
        options['handle_token'] = GLib.Variant('s', token)
        sender = self.bus.get_unique_name()[1:].replace('.', '_')
        path = PATH + '/request/' + sender + '/' + token
        response = []
        sub = self.bus.signal_subscribe(DEST, 'org.freedesktop.portal.Request', 'Response',
                                       path, None, Gio.DBusSignalFlags.NONE,
                                       lambda _b, _s, _p, _i, _n, args: response.append(args.unpack()))
        try:
            actual = self.call(interface, method, signature, values)[0]
            if actual != path:
                fail('PORTAL_ERROR', 'Portal did not honor the request handle token.')
            while not response:
                self.guard()
                if time.monotonic() >= self.deadline:
                    fail('PERMISSION_TIMEOUT', 'Desktop permission was not granted in time. Retry permissions when ready to approve the dialog.')
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                time.sleep(.01)
            code, result = response[0]
            if code != 0:
                fail('PERMISSION_DENIED', 'Desktop sharing was cancelled or denied. No input was sent.')
            return result
        finally:
            self.bus.signal_unsubscribe(sub)
            if not response:
                try:
                    self.call('org.freedesktop.portal.Request', 'Close', '()', (), path)
                except ChatuseError:
                    pass

    def connect(self):
        self.guard()
        if self.session:
            return
        self.deadline = time.monotonic() + 32
        try:
            result = self.request(RD, 'CreateSession', '(a{sv})', ({
                'session_handle_token': GLib.Variant('s', 'chatuse_' + uuid.uuid4().hex)},))
            self.session = result['session_handle']
            self.closed_subscription = self.bus.signal_subscribe(
                DEST, 'org.freedesktop.portal.Session', 'Closed', self.session, None,
                Gio.DBusSignalFlags.NONE, lambda *_: self.close(remote=False))
            devices = {'types': GLib.Variant('u', 3), 'persist_mode': GLib.Variant('u', 2)}
            if self.token_file.exists():
                devices['restore_token'] = GLib.Variant('s', self.token_file.read_text().strip())
            self.request(RD, 'SelectDevices', '(oa{sv})', (self.session, devices))
            self.request(SC, 'SelectSources', '(oa{sv})', (self.session, {
                'types': GLib.Variant('u', 1), 'multiple': GLib.Variant('b', False),
                'cursor_mode': GLib.Variant('u', 2)}))  # embed compositor cursor
            result = self.request(RD, 'Start', '(osa{sv})', (self.session, '', {}))
            if result.get('devices', 0) & 3 != 3:
                fail('PERMISSION_DENIED', 'Both pointer and keyboard permission must be granted.')
            self.node, self.frame = stream_geometry(result.get('streams', []))
            if result.get('restore_token'):
                # A grant is private; never include it in status, logs, or errors.
                temp = self.token_file.with_suffix('.tmp')
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w') as f:
                    f.write(result['restore_token'])
                os.replace(temp, self.token_file)
            self.generation += 1
        except Exception:
            self.close()
            raise

    def notify(self, method, signature, values):
        if not self.session:
            fail('PERMISSION_REQUIRED', 'The sharing session ended. Request permissions again.')
        self.call(RD, method, '(oa{sv}' + signature + ')', (self.session, {}, *values))

    def monitors(self):
        self.connect()
        return [dict(id=0, name='Portal-selected monitor', bounds=self.frame.copy(),
                     coordinateSpace='selected-monitor-logical', primary=True)]

    def capture(self, args):
        if args.get('app') or args.get('pid') or args.get('windowId') is not None:
            fail('WINDOW_CAPTURE_UNSUPPORTED', 'Wayland captures only the consent-selected monitor; omit app/pid/windowId.')
        if args.get('displayId', 0) != 0:
            fail('DISPLAY_NOT_FOUND', 'Only displayId 0 (the selected monitor) is available.')
        self.connect()
        try:
            gi.require_version('Gst', '1.0')
            gi.require_version('GstApp', '1.0')
            gi.require_version('GstVideo', '1.0')
            from gi.repository import Gst, GstApp, GstVideo
        except (ImportError, ValueError):
            fail('CAPTURE_UNAVAILABLE', 'Install GStreamer introspection, plugins-base and gstreamer1.0-pipewire; see linux/README.md.')
        Gst.init(None)
        if self.pipeline is None:
            try:
                result, fds = self.bus.call_with_unix_fd_list_sync(
                    DEST, PATH, SC, 'OpenPipeWireRemote', GLib.Variant('(oa{sv})', (self.session, {})),
                    GLib.VariantType.new('(h)'), Gio.DBusCallFlags.NONE, 5000, None, None)
                self.pipe_fd = fds.get(result.unpack()[0])
                self.pipeline = Gst.parse_launch(
                    f'pipewiresrc fd={self.pipe_fd} path={self.node} do-timestamp=true ! '
                    'videoconvert ! video/x-raw,format=RGB ! appsink name=frames max-buffers=1 drop=true sync=false')
                self.pipeline.set_state(Gst.State.PLAYING)
            except (GLib.Error, OSError):
                self.stop_capture()
                fail('CAPTURE_UNAVAILABLE', 'Could not open the approved PipeWire stream. Check GStreamer PipeWire support.')
        sink = self.pipeline.get_by_name('frames')
        # Discard an older queued frame; never return a previous observation.
        while sink.try_pull_sample(0) is not None:
            pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.guard()
            sample = sink.try_pull_sample(100 * Gst.MSECOND)
            if sample:
                info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
                size = (info.width, info.height)
                if self.capture_size is not None and self.capture_size != size:
                    self.close()
                    fail('STREAM_CHANGED', 'Monitor capture dimensions changed. Reopen sharing and capture again.')
                self.capture_size = size
                buffer = sample.get_buffer()
                ok, mapping = buffer.map(Gst.MapFlags.READ)
                if not ok:
                    fail('CAPTURE_FAILED', 'PipeWire frame could not be read.')
                try:
                    image = Image.frombytes('RGB', (info.width, info.height), bytes(mapping.data),
                                            'raw', 'RGB', info.stride[0], 1)
                finally:
                    buffer.unmap(mapping)
                return image, self.frame.copy(), None, None, 'wayland-pipewire-monitor-cursor-embedded'
            error = self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if error:
                self.stop_capture()
                fail('CAPTURE_FAILED', 'PipeWire capture failed. Reopen sharing and observe again.')
        fail('CAPTURE_TIMEOUT', 'No fresh PipeWire frame arrived within five seconds.')

    def motion(self, x, y):
        if not self.frame or not contains(self.frame, x, y):
            fail('INVALID_COORDINATES', 'Point is outside the selected monitor.')
        self.notify('NotifyPointerMotionAbsolute', 'udd', (self.node, float(x), float(y)))

    def button(self, button, down):
        if button in (4, 5, 6, 7):
            if down:
                self.notify('NotifyPointerAxisDiscrete', 'ui', (0 if button < 6 else 1, -1 if button in (4, 6) else 1))
            return
        code = {1: 272, 2: 274, 3: 273}[button]
        if down:
            self.held_buttons.add(button)
        self.notify('NotifyPointerButton', 'iu', (code, int(down)))
        if not down:
            self.held_buttons.discard(button)

    def key(self, keysym, down):
        if down:
            self.held_keys.add(keysym)
        self.notify('NotifyKeyboardKeysym', 'iu', (keysym, int(down)))
        if not down:
            self.held_keys.discard(keysym)

    def shortcut(self, name, modifiers, check):
        aliases = {'enter': 'Return', 'return': 'Return', 'tab': 'Tab', 'escape': 'Escape',
                   'backspace': 'BackSpace', 'delete': 'Delete', 'space': 'space', 'left': 'Left',
                   'right': 'Right', 'up': 'Up', 'down': 'Down', 'home': 'Home', 'end': 'End',
                   'pageup': 'Prior', 'pagedown': 'Next', **{f'f{i}': f'F{i}' for i in range(1, 13)}}
        mods = {'ctrl': 'Control_L', 'control': 'Control_L', 'shift': 'Shift_L', 'alt': 'Alt_L',
                'option': 'Alt_L', 'cmd': 'Super_L', 'command': 'Super_L', 'meta': 'Super_L'}
        key = Gdk.keyval_from_name(aliases.get(name.lower(), name))
        if key in (0, 0xffffff):
            fail('UNKNOWN_KEY', 'Unknown key name.')
        try:
            check()
            for mod in dict.fromkeys(modifiers):
                if mod not in mods:
                    fail('UNKNOWN_MODIFIER', 'Unknown modifier.')
                self.key(Gdk.keyval_from_name(mods[mod]), True)
            self.key(key, True)
            self.key(key, False)
        finally:
            self.release()

    def insert_unicode(self, text, check):
        # GNOME's portal keysyms cannot represent characters absent from its
        # active keymap reliably. Use the focused accessible editor, without
        # reading field contents or replacing the clipboard. Never fall back
        # after partially typing a string.
        check()
        pid = self.active_pid()
        root = Atspi.get_desktop(0)
        deadline = time.monotonic() + 2
        visited = [0]
        def find(element, secure=False, depth=0):
            visited[0] += 1
            if visited[0] > 1000 or depth > 24 or time.monotonic() > deadline:
                return None
            secure = secure or element.get_role() == Atspi.Role.PASSWORD_TEXT
            if element.get_state_set().contains(Atspi.StateType.FOCUSED):
                if secure:
                    fail('UNICODE_INPUT_UNSUPPORTED', 'Unicode insertion is unavailable for protected fields.')
                editor, content = element.get_editable_text_iface(), element.get_text_iface()
                if editor and content:
                    return editor, content
            for i in range(element.get_child_count()):
                result = find(element.get_child_at_index(i), secure, depth+1)
                if result:
                    return result
            return None
        target = None
        for i in range(root.get_child_count()):
            app = root.get_child_at_index(i)
            if app.get_process_id() == pid:
                target = find(app)
                break
        if not target:
            fail('UNICODE_INPUT_UNSUPPORTED', 'Unicode requires an active AT-SPI editable text field on Wayland. No text was sent.')
        editor, content = target
        check()
        position = content.get_caret_offset()
        if content.get_n_selections() > 1:
            fail('UNICODE_INPUT_UNSUPPORTED', 'Multiple selections are not supported; no text was sent.')
        if content.get_n_selections() == 1:
            selection = content.get_selection(0)
            position = selection.start_offset
            if not editor.delete_text(position, selection.end_offset):
                fail('ACCESSIBILITY_ACTION_FAILED', 'The focused editor rejected selection replacement.')
        check()
        if not editor.insert_text(position, text, len(text.encode('utf-8'))):
            fail('ACCESSIBILITY_ACTION_FAILED', 'The focused editor rejected Unicode insertion.')
        content.set_caret_offset(position + len(text))

    def type_text(self, text, check):
        if any(0xd800 <= ord(char) <= 0xdfff for char in text):
            fail('INVALID_TEXT', 'Invalid Unicode scalar.')
        if any(ord(char) > 127 for char in text):
            self.insert_unicode(text, check)
            return
        try:
            for char in text:
                check()
                if 0xd800 <= ord(char) <= 0xdfff:
                    fail('INVALID_TEXT', 'Invalid Unicode scalar.')
                key = {'\n': Gdk.KEY_Return, '\r': Gdk.KEY_Return, '\t': Gdk.KEY_Tab}.get(char, Gdk.unicode_to_keyval(ord(char)))
                self.key(key, True)
                self.key(key, False)
                time.sleep(.008)
        finally:
            self.release()

    def apps(self):
        root = Atspi.get_desktop(0)
        result = []
        for i in range(root.get_child_count()):
            try:
                a = root.get_child_at_index(i)
                result.append(dict(pid=a.get_process_id(), name=a.get_name() or '',
                                   appId=a.get_name() or '', path='', discovery='atspi'))
            except GLib.Error:
                pass
        return result

    def active_pid(self):
        root = Atspi.get_desktop(0)
        for i in range(root.get_child_count()):
            a = root.get_child_at_index(i)
            try:
                for j in range(a.get_child_count()):
                    if a.get_child_at_index(j).get_state_set().contains(Atspi.StateType.ACTIVE):
                        return a.get_process_id()
            except GLib.Error:
                pass
        return None

    def resolve_app(self, args):
        apps = self.apps()
        pid = args.get('pid')
        name = args.get('app', 'frontmost')
        if pid is None and name == 'frontmost':
            pid = self.active_pid()
            if pid is None:
                fail('FOCUS_UNAVAILABLE', 'AT-SPI cannot identify the active app. Use an observed PID or screenshot click.')
        matches = [a for a in apps if a['pid'] == pid] if pid else [a for a in apps if name in (a['name'], a['appId'])]
        if len(matches) != 1:
            fail('APP_NOT_FOUND', 'Use a unique PID from list_apps; Wayland discovery includes only accessible apps.')
        return matches[0]

    def focus(self, pid, check, wid=None):
        check()
        if self.active_pid() != pid:
            fail('FOCUS_REQUIRED', 'Wayland does not permit arbitrary app activation. Click the target in a fresh monitor screenshot first.')

    def release(self):
        for button in list(self.held_buttons):
            try:
                self.button(button, False)
            except ChatuseError:
                pass
        for key in list(self.held_keys):
            try:
                self.key(key, False)
            except ChatuseError:
                pass
        self.held_buttons.clear()
        self.held_keys.clear()

    def stop_capture(self):
        if self.pipeline:
            from gi.repository import Gst
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        if self.pipe_fd is not None:
            os.close(self.pipe_fd)
            self.pipe_fd = None

    def close(self, remote=True):
        self.release()
        self.stop_capture()
        if self.closed_subscription is not None:
            self.bus.signal_unsubscribe(self.closed_subscription)
            self.closed_subscription = None
        old, self.session = self.session, None
        self.node, self.frame = None, None
        self.capture_size = None
        if old and remote:
            try:
                self.call('org.freedesktop.portal.Session', 'Close', '()', (), old)
            except ChatuseError:
                pass
