"""X11/EWMH/XTest operations. No shell interpolation, clipboard typing, or replay."""
import os
import time
from pathlib import Path

import gi
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk
from PIL import Image, ImageGrab
from Xlib import X, XK, Xatom, display, error, protocol
from Xlib.ext import composite, xfixes, xtest  # Register extension methods.

from core import ChatuseError, fail, contains


class Desktop:
    def __init__(self):
        self.connection = display.Display()
        self.root = self.connection.screen().root
        self.connection.set_error_handler(lambda *_: None)
        self.held_buttons = set()
        self.held_keys = set()
        self.temporary_key = None

    def atom(self, name):
        return self.connection.intern_atom(name)

    def prop(self, window, name):
        value = window.get_full_property(self.atom(name), X.AnyPropertyType)
        return value.value if value is not None else None

    def window(self, wid):
        try:
            w = self.connection.create_resource_object('window', int(wid))
            w.get_attributes()
            return w
        except (error.XError, ValueError, OverflowError):
            fail('WINDOW_NOT_FOUND', 'The selected window is no longer available.')

    def bounds(self, w):
        g = w.get_geometry()
        p = self.root.translate_coords(w, 0, 0)
        return dict(x=p.x, y=p.y, width=g.width, height=g.height)

    def pid(self, w):
        value = self.prop(w, '_NET_WM_PID')
        return int(value[0]) if value is not None and len(value) else None

    def active_window(self):
        value = self.prop(self.root, '_NET_ACTIVE_WINDOW')
        return self.window(value[0]) if value is not None and len(value) and value[0] else None

    def active_pid(self):
        w = self.active_window()
        return self.pid(w) if w else None

    def window_info(self, w):
        title = self.prop(w, '_NET_WM_NAME')
        if isinstance(title, bytes):
            title = title.decode('utf-8', 'replace')
        if title is None:
            title = w.get_wm_name() or ''
        classes = w.get_wm_class() or ('', '')
        return dict(windowId=w.id, pid=self.pid(w), app=classes[-1], title=str(title), bounds=self.bounds(w))

    def visible(self, w):
        states = self.prop(w, '_NET_WM_STATE')
        hidden = states is not None and self.atom('_NET_WM_STATE_HIDDEN') in states
        return w.get_attributes().map_state == X.IsViewable and not hidden

    def windows(self, pid=None, visible=True):
        ids = self.prop(self.root, '_NET_CLIENT_LIST_STACKING')
        if ids is None:
            ids = self.prop(self.root, '_NET_CLIENT_LIST')
        result = []
        for wid in reversed(list(ids) if ids is not None else []):
            try:
                w = self.window(wid)
                if visible and not self.visible(w):
                    continue
                row = self.window_info(w)
                if pid is None or row['pid'] == pid:
                    result.append(row)
            except (error.XError, ChatuseError):
                # Windows may disappear during enumeration. Do not reuse their IDs.
                continue
        return result

    def apps(self):
        result = {}
        active = self.active_pid()
        for w in self.windows(visible=False):
            pid = w['pid']
            if not pid or pid in result:
                continue
            try:
                executable = os.readlink(f'/proc/{pid}/exe')
            except OSError:
                executable = ''
            result[pid] = dict(pid=pid, name=w['app'] or Path(executable).name,
                               appId=w['app'], path=executable, active=pid == active,
                               hidden=not bool(self.windows(pid)))
        return list(result.values())

    def resolve_app(self, args):
        apps = self.apps()
        if 'pid' in args:
            matches = [a for a in apps if a['pid'] == args['pid']]
        else:
            target = args.get('app', '').strip()
            if target == 'frontmost':
                matches = [a for a in apps if a['active']]
            else:
                matches = [a for a in apps if target and target.casefold() in
                           [str(a[k]).casefold() for k in ('name', 'appId', 'path')]]
        if len(matches) != 1:
            fail('APP_NOT_UNIQUE', 'Use list_apps and provide a unique running app PID or app ID.')
        return matches[0]

    def monitors(self):
        d = Gdk.Display.get_default()
        result = []
        if d:
            primary = d.get_primary_monitor()
            for i in range(d.get_n_monitors()):
                m = d.get_monitor(i)
                r, scale = m.get_geometry(), m.get_scale_factor()
                # X11 input/capture coordinates use device pixels, not GTK logical points.
                result.append(dict(id=i, main=m == primary or (primary is None and i == 0),
                                   bounds=dict(x=r.x * scale, y=r.y * scale,
                                               width=r.width * scale, height=r.height * scale),
                                   pixelsWide=r.width * scale, pixelsHigh=r.height * scale))
        if not result:
            g = self.root.get_geometry()
            result = [dict(id=0, main=True, bounds=dict(x=0, y=0, width=g.width, height=g.height),
                           pixelsWide=g.width, pixelsHigh=g.height)]
        return result

    def message(self, w, name, data):
        event = protocol.event.ClientMessage(window=w, client_type=self.atom(name), data=(32, data + [0] * (5 - len(data))))
        self.root.send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.connection.flush()

    def focus(self, pid, check, window_id=None):
        windows = self.windows(pid, visible=False)
        if not windows:
            fail('WINDOW_NOT_FOUND', 'The app has no window.')
        w = self.window(window_id if window_id is not None else windows[0]['windowId'])
        if self.pid(w) != pid:
            fail('APP_MISMATCH', 'The selected window no longer belongs to the target app.')
        w.map()
        self.message(w, '_NET_ACTIVE_WINDOW', [2, X.CurrentTime, 0])
        for _ in range(30):
            check()
            active = self.active_window()
            if active and active.id == w.id:
                return
            time.sleep(.04)
        fail('FOCUS_FAILED', 'Could not focus the app; no input was sent.')

    def capture(self, args):
        pid = wid = None
        mode = 'display'
        if 'windowId' in args or 'pid' in args or 'app' in args:
            if 'windowId' in args:
                w = self.window(args['windowId'])
            else:
                app = self.resolve_app(args)
                windows = self.windows(app['pid'])
                if not windows:
                    fail('WINDOW_NOT_FOUND', 'The app has no visible window.')
                w = self.window(windows[0]['windowId'])
            if not self.visible(w):
                fail('WINDOW_NOT_FOUND', 'The selected window is not visible.')
            frame, pid, wid = self.bounds(w), self.pid(w), w.id
            if not self.connection.has_extension('Composite'):
                fail('COMPOSITE_REQUIRED', 'Window capture requires an X11 compositor (GNOME provides one). Use a display screenshot instead.')
            pixmap = w.composite_name_window_pixmap()
            try:
                g = pixmap.get_geometry()
                raw = pixmap.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xffffffff)
                formats = self.connection.display.info.pixmap_formats
                bits = next(f.bits_per_pixel for f in formats if f.depth == g.depth)
                if bits != 32 or self.connection.display.info.image_byte_order != X.LSBFirst:
                    fail('PIXEL_FORMAT_UNSUPPORTED', 'This X11 pixel format is not supported; expected little-endian 32-bit pixels.')
                # Client pixmaps exclude window-manager decorations. Match the
                # exact client rectangle used by AT-SPI and coordinate validation.
                image = Image.frombytes('RGB', (g.width, g.height), raw.data, 'raw', 'BGRX')
                mode = 'window-composite'
            finally:
                pixmap.free()
        else:
            monitors = self.monitors()
            selected = next((m for m in monitors if m['id'] == args.get('displayId', next(d['id'] for d in monitors if d['main']))), None)
            if selected is None:
                fail('DISPLAY_NOT_FOUND', 'Use displays to select an available display.')
            frame = selected['bounds']
            image = ImageGrab.grab(bbox=(frame['x'], frame['y'], frame['x'] + frame['width'], frame['y'] + frame['height']),
                                   xdisplay=os.environ.get('DISPLAY'))
        if args.get('showCursor'):
            if not self.connection.has_extension('XFIXES'):
                fail('CURSOR_CAPTURE_UNSUPPORTED', 'Cursor capture requires the XFixes extension.')
            self.connection.xfixes_query_version()
            c = self.connection.xfixes_get_cursor_image(self.root)
            if contains(frame, c.x, c.y):
                data = b''.join(int(pixel).to_bytes(4, 'little') for pixel in c.cursor_image)
                cursor = Image.frombytes('RGBA', (c.width, c.height), data, 'raw', 'BGRA')
                image.paste(cursor, (c.x - c.xhot - frame['x'], c.y - c.yhot - frame['y']), cursor)
        return image, frame, pid, wid, mode

    def motion(self, x, y):
        xtest.fake_input(self.connection, X.MotionNotify, x=round(x), y=round(y))
        self.connection.sync()

    def button(self, button, down):
        if down:
            self.held_buttons.add(button)
        else:
            self.held_buttons.discard(button)
        xtest.fake_input(self.connection, X.ButtonPress if down else X.ButtonRelease, button)
        self.connection.sync()

    def key(self, code, down):
        if down:
            self.held_keys.add(code)
        else:
            self.held_keys.discard(code)
        xtest.fake_input(self.connection, X.KeyPress if down else X.KeyRelease, code)
        self.connection.sync()

    def shortcut(self, name, modifiers, check):
        names = {'enter': 'Return', 'return': 'Return', 'tab': 'Tab', 'escape': 'Escape',
                 'backspace': 'BackSpace', 'delete': 'Delete', 'space': 'space',
                 'left': 'Left', 'right': 'Right', 'up': 'Up', 'down': 'Down',
                 'home': 'Home', 'end': 'End', 'pageup': 'Prior', 'pagedown': 'Next'}
        names.update({f'f{i}': f'F{i}' for i in range(1, 13)})
        keysym = XK.string_to_keysym(names.get(name.lower(), name))
        code = self.connection.keysym_to_keycode(keysym)
        if not keysym or not code:
            fail('UNKNOWN_KEY', 'Unknown key name; use type_text for arbitrary Unicode.')
        aliases = {'ctrl': 'Control_L', 'control': 'Control_L', 'alt': 'Alt_L', 'option': 'Alt_L',
                   'shift': 'Shift_L', 'cmd': 'Super_L', 'command': 'Super_L', 'meta': 'Super_L'}
        mods = []
        for m in modifiers:
            if m.lower() not in aliases:
                fail('UNKNOWN_MODIFIER', 'Unknown keyboard modifier.')
            k = self.connection.keysym_to_keycode(XK.string_to_keysym(aliases[m.lower()]))
            if not k:
                fail('UNKNOWN_MODIFIER', 'Modifier is unavailable in the current X11 keymap.')
            if k not in mods:
                mods.append(k)
        try:
            check()
            for k in mods:
                self.key(k, True)
            self.key(code, True)
            self.key(code, False)
        finally:
            for k in reversed(mods):
                self.key(k, False)

    def type_text(self, text, check):
        # Reserve only an unused keycode; restore its mapping on every exit.
        # This sends real Unicode key events without changing the clipboard.
        minimum, maximum = self.connection.display.info.min_keycode, self.connection.display.info.max_keycode
        mapping = self.connection.get_keyboard_mapping(minimum, maximum - minimum + 1)
        code = next((minimum + i for i in reversed(range(len(mapping))) if not any(mapping[i])), None)
        if code is None:
            fail('KEYMAP_FULL', 'No unused X11 keycode is available for Unicode input.')
        original = list(mapping[code - minimum])
        self.temporary_key = (code, original)
        try:
            for char in text:
                check()
                if char in '\n\r\t':
                    self.shortcut('tab' if char == '\t' else 'enter', [], check)
                    continue
                scalar = ord(char)
                if 0xd800 <= scalar <= 0xdfff:
                    fail('INVALID_TEXT', 'Text contains an invalid Unicode scalar.')
                keysym = scalar if scalar <= 0xff else 0x01000000 | scalar
                self.connection.change_keyboard_mapping(code, [[keysym] * len(original)])
                self.connection.sync()
                # Allow clients to process MappingNotify before the key event.
                time.sleep(.012)
                check()
                self.key(code, True)
                self.key(code, False)
                time.sleep(.008)
        finally:
            self.connection.change_keyboard_mapping(code, [original])
            self.connection.sync()
            self.temporary_key = None

    def release(self):
        for button in list(self.held_buttons):
            self.button(button, False)
        for code in list(self.held_keys):
            self.key(code, False)
        if self.temporary_key:
            code, original = self.temporary_key
            self.connection.change_keyboard_mapping(code, [original])
            self.connection.sync()
            self.temporary_key = None
