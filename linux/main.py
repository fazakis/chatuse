#!/usr/bin/env python3
"""Serialized JSON-lines Linux helper. Run as the desktop user, never with sudo."""
import base64
import csv
import fcntl
import io
import json
import math
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, Gdk, Gio, GLib
from Xlib import X, error
from PIL import Image

from accessibility import Accessibility
from core import ChatuseError, References, contains, fail, number, same_geometry, scaled_size, screen_point
from desktop import Desktop
from session import session_state


INPUTS = {'click', 'type_text', 'press_key', 'scroll', 'drag', 'move_pointer',
          'set_value', 'window', 'clipboard_write', 'launch', 'open_url'}


class Driver:
    def __init__(self, root):
        self.root = root
        self.cancelled = False
        self.images = References()
        self.accessibility = Accessibility()
        self.desktop = None
        if os.environ.get('DISPLAY'):
            try:
                self.desktop = Desktop()
            except (error.DisplayConnectionError, error.XError, OSError):
                pass
        self._session_at, self._session = 0, None

    def session(self, fresh=False):
        if fresh or self._session is None or time.monotonic() - self._session_at > .15:
            self._session = session_state()
            self._session_at = time.monotonic()
        return self._session

    def require_session(self):
        s = self.session()
        if s['sessionType'] == 'wayland':
            fail('WAYLAND_UNSUPPORTED', 'Full desktop control currently requires Ubuntu on Xorg. Select that session at the login screen; Xwayland alone is not supported.')
        if self.desktop is None or s['sessionType'] != 'x11':
            fail('DESKTOP_REQUIRED', 'Run Chatuse inside an X11 desktop as its user, with DISPLAY, XAUTHORITY and the desktop D-Bus session available.')
        if not s['sessionActive'] or not s['lockStateKnown'] or s['locked']:
            fail('SESSION_LOCKED', 'An active, unlocked local desktop session is required. Chatuse does not unlock sessions.')

    def require_input(self):
        if self.cancelled:
            fail('CANCELLED', 'Input stopped because the helper is closing. Observe before retrying.')
        if (self.root / 'STOP').exists():
            fail('STOPPED', 'Emergency stop is active. Resume locally with chatuse resume.')
        self.require_session()
        if not self.desktop.connection.has_extension('XTEST'):
            fail('INPUT_UNAVAILABLE', 'The X server does not expose the XTest input extension.')

    def ensure_focus(self, pid):
        self.require_input()
        if pid is not None and self.desktop.active_pid() != pid:
            fail('FOCUS_CHANGED', 'Foreground app changed; input stopped. Observe before retrying.')

    def status(self):
        s = self.session(fresh=True)
        x11 = self.desktop is not None and s['sessionType'] == 'x11'
        return dict(version='0.1.0', platform='Linux', distribution=platform.freedesktop_os_release().get('PRETTY_NAME', 'Linux'),
                    architecture=platform.machine(), backend='atspi-x11', **s,
                    accessibility=bool(x11 and self.accessibility.available()), screenRecording=x11,
                    inputAvailable=bool(x11 and self.desktop.connection.has_extension('XTEST')),
                    ocrAvailable=shutil.which('tesseract') is not None, secureInput=False,
                    secureInputDetection='unavailable on X11', stopped=(self.root / 'STOP').exists(),
                    perAppApprovals=False, allAppsAllowed=True, stopFile=str(self.root / 'STOP'),
                    capabilities=['accessibility', 'screenshots', 'ocr', 'mouse', 'keyboard', 'windows', 'clipboard'],
                    limitations=['Requires a local unlocked X11 session; Wayland is not supported',
                                 'X11 has no macOS-style global Secure Input indicator',
                                 'Accessibility depends on each app exposing AT-SPI',
                                 'Window screenshots capture client content without decorations',
                                 'Wheel scrolling approximates pixels using discrete X11 wheel steps'])

    def app(self, args):
        self.require_session()
        return self.desktop.resolve_app(args)

    def target_point(self, args):
        x, y = number(args.get('x')), number(args.get('y'))
        if args.get('screenshotId'):
            s = self.images.get(args['screenshotId'], 'UNKNOWN_SCREENSHOT')
            if s['windowId'] is not None:
                w = self.desktop.window(s['windowId'])
                if self.desktop.pid(w) != s['pid'] or not same_geometry(self.desktop.bounds(w), s['frame']):
                    fail('WINDOW_MOVED', 'Window geometry changed. Capture a fresh screenshot before clicking.')
            x, y = screen_point(x, y, s['width'], s['height'], s['frame'])
            return x, y, s['pid']
        if not any(contains(m['bounds'], x, y) for m in self.desktop.monitors()):
            fail('INVALID_COORDINATES', 'The point is outside the active displays.')
        pid = self.app(args)['pid'] if 'pid' in args or 'app' in args else None
        return x, y, pid

    def screenshot(self, args):
        self.require_session()
        source, frame, pid, wid, mode = self.desktop.capture(args)
        image = source.resize(scaled_size(frame, args.get('maxWidth', 1440)), Image.Resampling.LANCZOS)
        data = io.BytesIO()
        image.save(data, format='PNG')
        payload = data.getvalue()
        key = self.images.add(dict(frame=frame, width=image.width, height=image.height, pid=pid, windowId=wid))
        result = dict(screenshotId=key, width=image.width, height=image.height, screenBounds=frame,
                      imageBase64=base64.b64encode(payload).decode('ascii'), mimeType='image/png',
                      expiresInSeconds=120, captureMode=mode)
        if pid is not None:
            result['pid'] = pid
        if wid is not None:
            result['windowId'] = wid
        if args.get('ocr'):
            if not shutil.which('tesseract'):
                fail('OCR_UNAVAILABLE', 'Install tesseract-ocr for OCR support.')
            try:
                # OCR before thumbnail downsampling so small returned images do
                # not lose their text. Bounds are mapped back to output pixels.
                ocr_image = source if source.width > image.width else image
                ocr_data = io.BytesIO()
                ocr_image.save(ocr_data, format='PNG')
                process = subprocess.run(['tesseract', 'stdin', 'stdout', '--psm', '11', 'tsv'], input=ocr_data.getvalue(),
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=True)
            except (subprocess.SubprocessError, OSError):
                fail('OCR_FAILED', 'OCR did not complete successfully.')
            lines = {}
            for row in csv.DictReader(io.StringIO(process.stdout.decode('utf-8', 'replace')), delimiter='\t'):
                text = (row.get('text') or '').strip()
                if not text or row.get('level') != '5':
                    continue
                group = tuple(row[k] for k in ('page_num', 'block_num', 'par_num', 'line_num'))
                x, y, width, height = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
                confidence = max(0, float(row['conf']) / 100)
                if group not in lines:
                    lines[group] = dict(text=text, confidence=confidence, bounds=dict(x=x, y=y, width=width, height=height))
                else:
                    item, bounds = lines[group], lines[group]['bounds']
                    right, bottom = max(bounds['x'] + bounds['width'], x + width), max(bounds['y'] + bounds['height'], y + height)
                    bounds['x'], bounds['y'] = min(bounds['x'], x), min(bounds['y'], y)
                    bounds['width'], bounds['height'] = right - bounds['x'], bottom - bounds['y']
                    item['text'] += ' ' + text
                    item['confidence'] = min(item['confidence'], confidence)
            sx, sy = image.width / ocr_image.width, image.height / ocr_image.height
            for item in lines.values():
                b = item['bounds']
                item['bounds'] = dict(x=b['x'] * sx, y=b['y'] * sy, width=b['width'] * sx, height=b['height'] * sy)
            result['text'] = list(lines.values())
        return result

    def focus_target(self, args, pid):
        wid = None
        if args.get('screenshotId'):
            shot = self.images.get(args['screenshotId'], 'UNKNOWN_SCREENSHOT')
            if shot['pid'] is not None and shot['pid'] != pid:
                fail('APP_MISMATCH', 'Screenshot belongs to a different app than the input target.')
            wid = shot['windowId']
        self.desktop.focus(pid, self.require_input, wid)

    def handle(self, method, args):
        lock = None
        try:
            if method in INPUTS:
                lock = open(self.root / 'runtime/input.lock', 'a', encoding='utf-8')
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    fail('INPUT_BUSY', 'Another Chatuse helper is sending input. Observe before retrying.')
                self.require_input()
            return self.execute(method, args)
        finally:
            if lock:
                try:
                    if self.desktop:
                        self.desktop.release()
                finally:
                    lock.close()

    def execute(self, method, args):
        if method == 'status':
            return self.status()
        if method == 'request_permissions':
            Gio.Settings.new('org.gnome.desktop.interface').set_boolean('toolkit-accessibility', True)
            return dict(**self.status(), setupMessage='Desktop accessibility enabled. Run in Ubuntu on Xorg as the desktop user; restart apps that do not expose an AT-SPI tree.')
        if method == 'list_apps':
            self.require_session()
            query = args.get('query', '').casefold()
            return dict(apps=[a for a in self.desktop.apps() if not query or any(query in str(a[k]).casefold() for k in ('name', 'appId', 'path'))])
        if method == 'windows':
            self.require_session()
            pid = self.app(args)['pid'] if 'pid' in args or 'app' in args else None
            windows = self.desktop.windows(pid, visible=False)
            for index, row in enumerate(windows):
                if pid is not None:
                    row['index'] = index
                row['visible'] = self.desktop.visible(self.desktop.window(row['windowId']))
            return dict(windows=windows)
        if method == 'displays':
            self.require_session()
            return dict(displays=self.desktop.monitors())
        if method == 'inspect':
            return self.accessibility.inspect(self.app(args), args)
        if method == 'screenshot':
            return self.screenshot(args)
        if method == 'set_value':
            return self.accessibility.set_value(args)
        if method == 'click' and args.get('elementId'):
            return self.accessibility.click(args)
        if method in ('click', 'move_pointer'):
            x, y, pid = self.target_point(args)
            if pid and method == 'click':
                self.focus_target(args, pid)
            self.target_point(args)
            self.ensure_focus(pid if method == 'click' else None)
            self.desktop.motion(x, y)
            if method == 'click':
                button = {'left': 1, 'middle': 2, 'right': 3}.get(args.get('button', 'left'))
                if button is None:
                    fail('INVALID_BUTTON', 'Use left, middle, or right.')
                for _ in range(min(max(int(args.get('count', 1)), 1), 3)):
                    self.ensure_focus(pid)
                    try:
                        self.desktop.button(button, True)
                    finally:
                        self.desktop.button(button, False)
                    time.sleep(.06)
            return dict(performed=method, x=x, y=y, method='pointer')
        if method in ('type_text', 'press_key', 'scroll'):
            pid = self.app(args)['pid']
            self.focus_target(args, pid)
            check = lambda: self.ensure_focus(pid)
            check()
            if method == 'type_text':
                text = args.get('text')
                if not isinstance(text, str) or len(text) > 50000:
                    fail('INVALID_TEXT', 'Provide text of at most 50,000 characters.')
                self.desktop.type_text(text, check)
                return dict(typedCharacters=len(text))
            if method == 'press_key':
                self.desktop.shortcut(args.get('key', ''), args.get('modifiers', []), check)
                return dict(performed='key', key=args.get('key'))
            if 'x' in args:
                x, y, owner = self.target_point(args)
                if owner is not None and owner != pid:
                    fail('APP_MISMATCH', 'Screenshot belongs to a different app than the scroll target.')
                self.desktop.motion(x, y)
            for axis, negative, positive in [('dy', 4, 5), ('dx', 6, 7)]:
                amount = max(-10000, min(int(args.get(axis, 0)), 10000))
                for _ in range(math.ceil(abs(amount) / 40)):
                    check()
                    button = positive if amount > 0 else negative
                    try:
                        self.desktop.button(button, True)
                    finally:
                        self.desktop.button(button, False)
            return dict(performed='scroll', dx=args.get('dx', 0), dy=args.get('dy', 0), scrollUnits='approximate pixels (40 per wheel step)')
        if method == 'drag':
            start = dict(args, x=args.get('fromX'), y=args.get('fromY'))
            end = dict(args, x=args.get('toX'), y=args.get('toY'))
            x, y, pid = self.target_point(start)
            tx, ty, end_pid = self.target_point(end)
            if pid != end_pid:
                fail('APP_MISMATCH', 'Drag endpoints must belong to the same app.')
            if pid:
                self.focus_target(args, pid)
            self.target_point(start)
            self.target_point(end)
            self.ensure_focus(pid)
            self.desktop.motion(x, y)
            try:
                self.desktop.button(1, True)
                duration = min(max(args.get('durationMs', 600), 100), 3000) / 1000
                for step in range(1, 31):
                    self.ensure_focus(pid)
                    self.desktop.motion(x + (tx - x) * step / 30, y + (ty - y) * step / 30)
                    time.sleep(duration / 30)
            finally:
                self.desktop.button(1, False)
            return dict(performed='drag')
        if method == 'window':
            pid, action = self.app(args)['pid'], args.get('action', 'raise')
            windows = self.desktop.windows(pid, visible=False)
            index = args.get('index', 0)
            if not 0 <= index < len(windows):
                fail('WINDOW_NOT_FOUND', 'Invalid window index.')
            w = self.desktop.window(windows[index]['windowId'])
            if action in ('focus', 'raise'):
                self.desktop.focus(pid, self.require_input, w.id)
            elif action == 'minimize':
                # ICCCM iconification is a request to the window manager, not
                # an Xlib Window.iconify method (Python-Xlib has no such API).
                self.desktop.message(w, 'WM_CHANGE_STATE', [3])
            elif action == 'restore':
                self.desktop.focus(pid, self.require_input, w.id)
            elif action in ('move', 'resize'):
                if action == 'move':
                    x, y = number(args.get('x')), number(args.get('y'))
                    self.desktop.message(w, '_NET_MOVERESIZE_WINDOW', [X.StaticGravity | (3 << 8) | (2 << 12), int(x) & 0xffffffff, int(y) & 0xffffffff, 0, 0])
                else:
                    width, height = number(args.get('width')), number(args.get('height'))
                    if min(width, height) < 100 or max(width, height) > 32767:
                        fail('INVALID_SIZE', 'Window dimensions must be between 100 and 32767.')
                    self.desktop.message(w, '_NET_MOVERESIZE_WINDOW', [X.StaticGravity | (12 << 8) | (2 << 12), 0, 0, int(width), int(height)])
            elif action == 'close':
                self.desktop.message(w, '_NET_CLOSE_WINDOW', [X.CurrentTime, 2])
            else:
                fail('UNKNOWN_ACTION', 'Unknown window action.')
            self.desktop.connection.sync()
            return dict(performed=action, windowIndex=index)
        if method in ('clipboard_read', 'clipboard_write'):
            self.require_session()
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            if method == 'clipboard_read':
                return dict(text=(clipboard.wait_for_text() or '')[:50000])
            text = args.get('text')
            if not isinstance(text, str) or len(text) > 50000:
                fail('INVALID_TEXT', 'Provide text of at most 50,000 characters.')
            clipboard.set_text(text, -1)
            clipboard.set_can_store(None)
            clipboard.store()
            return dict(writtenCharacters=len(text), persistence='Depends on the desktop clipboard manager after this helper exits')
        if method == 'launch':
            if args.get('activate') is False:
                fail('BACKGROUND_LAUNCH_UNSUPPORTED', 'Linux desktop launchers do not guarantee background activation. Launch normally or use an existing running app.')
            target = args.get('app', '')
            desktop = Gio.DesktopAppInfo.new_from_filename(target) if target.startswith('/') and target.endswith('.desktop') else Gio.DesktopAppInfo.new(target if target.endswith('.desktop') else target + '.desktop')
            if desktop is None:
                fail('APP_NOT_FOUND', 'Use an installed desktop-file ID or an absolute .desktop file path.')
            if not desktop.launch([], None):
                fail('LAUNCH_FAILED', 'The desktop launcher could not start the app.')
            return dict(launched=True, appId=desktop.get_id(), name=desktop.get_name(), activation='The desktop launcher controls initial activation')
        if method == 'open_url':
            url = args.get('url', '')
            if urlparse(url).scheme.lower() not in ('http', 'https') or not urlparse(url).netloc:
                fail('INVALID_URL', 'Use an HTTP or HTTPS URL.')
            if not Gio.AppInfo.launch_default_for_uri(url, None):
                fail('OPEN_FAILED', 'Could not open the URL.')
            return dict(opened=True)
        fail('UNKNOWN_METHOD', 'Unknown native method.')


def setup(driver):
    window = Gtk.Window(title='Chatuse Setup')
    window.set_default_size(580, 320)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
    label = Gtk.Label(xalign=0, selectable=True, wrap=True)
    def refresh(*_):
        s = driver.status()
        label.set_text('Chatuse for Ubuntu\n\nSession: ' + s['sessionType'] +
                       '\nAccessibility: ' + str(s['accessibility']) + '\nScreen capture: ' + str(s['screenRecording']) +
                       '\nInput paused: ' + str(s['stopped']) +
                       '\n\nUse an unlocked Ubuntu on Xorg desktop. Wayland is not supported. Run Chatuse as your desktop user, not root.')
    box.pack_start(label, True, True, 0)
    enable = Gtk.Button(label='Enable desktop accessibility')
    enable.connect('clicked', lambda *_: (driver.execute('request_permissions', {}), refresh()))
    box.pack_start(enable, False, False, 0)
    button = Gtk.Button(label='Refresh')
    button.connect('clicked', refresh)
    box.pack_start(button, False, False, 0)
    window.add(box)
    window.connect('destroy', Gtk.main_quit)
    refresh()
    window.show_all()
    Gtk.main()


def main():
    os.umask(0o077)
    root = Path(os.environ.get('CHATUSE_ROOT', Path(__file__).resolve().parent.parent))
    driver = Driver(root)
    if '--setup' in sys.argv:
        if not Gtk.init_check()[0]:
            fail('DESKTOP_REQUIRED', 'Open setup inside your X11 desktop.')
        setup(driver)
        return
    loop = GLib.MainLoop()
    def quit_helper(*_):
        driver.cancelled = True
        if driver.desktop:
            driver.desktop.release()
        loop.quit()
    signal.signal(signal.SIGTERM, quit_helper)
    signal.signal(signal.SIGINT, quit_helper)
    def dispatch(line, done):
        identifier = None
        try:
            if len(line) > 1_000_000:
                fail('INVALID_REQUEST', 'Request exceeds the maximum line size.')
            request = json.loads(line)
            if not isinstance(request, dict) or not isinstance(request.get('method'), str) or not isinstance(request.get('params', {}), dict):
                fail('INVALID_REQUEST', 'Expected a JSON object containing method and params.')
            identifier = request.get('id')
            response = dict(id=identifier, result=driver.handle(request['method'], request.get('params', {})))
        except ChatuseError as exc:
            response = dict(id=identifier, error=dict(code=exc.code, message=str(exc)))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            response = dict(id=identifier, error=dict(code='INVALID_REQUEST', message='Invalid native request or arguments.'))
        except Exception:
            # Never put app contents, typed text, or native diagnostic dumps in errors.
            response = dict(id=identifier, error=dict(code='NATIVE_ERROR', message='The desktop operation failed. Observe again before retrying input.'))
        try:
            sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + '\n')
            sys.stdout.flush()
        finally:
            done.set()
        return False
    def reader():
        while not driver.cancelled:
            line = sys.stdin.readline(1_000_002)
            if not line:
                break
            done = threading.Event()
            GLib.idle_add(dispatch, line, done)
            done.wait()
            if len(line) > 1_000_000:
                break
        GLib.idle_add(quit_helper)
    threading.Thread(target=reader, daemon=True).start()
    try:
        loop.run()
    finally:
        if driver.desktop:
            driver.desktop.release()


if __name__ == '__main__':
    main()
