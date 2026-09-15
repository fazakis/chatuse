#!/usr/bin/env python3
"""Nonactivating, click-through GTK overlay for the X11 desktop."""
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
import cairo
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gtk, Gdk, GdkX11, GLib
from core import number, ChatuseError, fail
from session import session_state


class Pointer:
    def __init__(self, root):
        self.root, self.position = root, (0, 0)
        self.activity, self.pulse = -100, -100
        self.paused, self.closed, self.pressed = False, False, False
        self.label = 'Chatuse'
        self.session_at, self.session = 0, None
        self.window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.window.set_title('Chatuse Visual Pointer')
        self.window.set_default_size(250, 140)
        self.window.set_decorated(False)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_keep_above(True)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.window.stick()
        self.window.set_app_paintable(True)
        visual = self.window.get_screen().get_rgba_visual()
        if visual:
            self.window.set_visual(visual)
        self.window.connect('draw', self.draw)
        self.window.realize()
        self.window.get_window().set_pass_through(True)
        self.window.get_window().input_shape_combine_region(cairo.Region(), 0, 0)
        GLib.timeout_add(33, self.tick)

    def permitted(self):
        if time.monotonic() - self.session_at > .5 or self.session is None:
            self.session, self.session_at = session_state(), time.monotonic()
        return (not self.closed and self.session['sessionType'] == 'x11' and self.session['sessionActive']
                and not self.session['locked'] and not (self.root / 'STOP').exists()
                and not (self.root / 'runtime/pointer-disabled').exists())

    def capturing(self):
        directory = self.root / 'runtime/pointer-captures'
        if not directory.exists():
            return False
        for marker in directory.iterdir():
            try:
                pid = int(marker.name.split('-')[0])
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                marker.unlink(missing_ok=True)
            except PermissionError:
                return True
            except ValueError:
                continue
        return False

    def tick(self):
        age = time.monotonic() - self.activity
        if not self.permitted() or self.paused or self.capturing() or age > 3.2:
            self.window.hide()
        else:
            self.window.set_opacity(1 if age < 2.5 else max(0, (3.2 - age) / .7))
            self.window.queue_draw()
        return not self.closed

    def place(self, point):
        self.position, self.activity = point, time.monotonic()
        scale = self.window.get_scale_factor()
        self.window.move(round(point[0] / scale - 60), round(point[1] / scale - 60))
        self.window.set_opacity(1)
        if self.permitted() and not self.paused and not self.capturing():
            self.window.show_all()
        self.window.queue_draw()

    def animate(self, point, duration, linear=False):
        start = self.position
        steps = max(1, min(max(int(duration), 0), 3000) // 16)
        for step in range(steps + 1):
            if not self.permitted():
                self.window.hide()
                return
            progress = step / steps
            if not linear:
                progress = progress * progress * (3 - 2 * progress)
            self.place((start[0] + (point[0] - start[0]) * progress, start[1] + (point[1] - start[1]) * progress))
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            if step < steps:
                time.sleep(min(max(duration, 0), 3000) / steps / 1000)

    def draw(self, _window, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        pulse = min(1, (time.monotonic() - self.pulse) / .7)
        if pulse < 1 or self.pressed:
            cr.arc(60, 60, 17 if self.pressed else 12 + 34 * pulse, 0, 6.28319)
            cr.set_source_rgba(.1, .48, 1, .2 if self.pressed else .15 * (1 - pulse))
            cr.fill_preserve()
            cr.set_source_rgba(.1, .48, 1, 1 if self.pressed else 1 - pulse)
            cr.set_line_width(3)
            cr.stroke()
        cr.move_to(60, 60)
        for x, y in [(62, 95), (71, 87), (78, 101), (85, 97), (78, 84), (90, 83)]:
            cr.line_to(x, y)
        cr.close_path()
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(4)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke_preserve()
        cr.set_source_rgb(.1, .48, 1)
        cr.fill()
        cr.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(11)
        width = min(cr.text_extents(self.label).width + 18, 145)
        cr.rectangle(94, 88, width, 23)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(102, 104)
        cr.show_text(self.label)

    def handle(self, method, args):
        if method in ('move', 'drag'):
            self.paused = False
            self.label = 'Chatuse' if method == 'move' else 'Chatuse · drag'
            self.pressed = method == 'drag'
            try:
                self.animate((number(args.get('x')), number(args.get('y'))), args.get('durationMs', 320), method == 'drag')
            finally:
                self.pressed = False
        elif method == 'pulse':
            if 'x' in args:
                self.place((number(args['x']), number(args['y'])))
            self.label = str(args.get('label', 'Chatuse · click'))[:80]
            self.pulse = self.activity = time.monotonic()
        elif method == 'hide':
            self.paused = True
            self.window.hide()
        elif method == 'restore':
            self.paused = False
            if time.monotonic() - self.activity < 3.2 and self.permitted() and not self.capturing():
                self.window.show_all()
        elif method != 'state':
            fail('UNKNOWN_METHOD', 'Unknown pointer method.')
        self.tick()
        return dict(visible=self.window.get_visible(), windowId=self.window.get_window().get_xid(),
                    ignoresMouseEvents=self.window.get_window().get_pass_through(), canBecomeKey=False,
                    x=self.position[0], y=self.position[1])


def main():
    os.umask(0o077)
    if not Gtk.init_check()[0]:
        return
    pointer = Pointer(Path(os.environ.get('CHATUSE_ROOT', Path(__file__).resolve().parent.parent)))
    def close(*_):
        pointer.closed = True
        pointer.window.hide()
        Gtk.main_quit()
    signal.signal(signal.SIGTERM, close)
    signal.signal(signal.SIGINT, close)
    def dispatch(line, done):
        identifier = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or len(line) > 100000:
                fail('INVALID_REQUEST', 'Invalid pointer request.')
            identifier = request.get('id')
            response = dict(id=identifier, result=pointer.handle(request.get('method'), request.get('params', {})))
        except Exception as exc:
            response = dict(id=identifier, error=dict(code=getattr(exc, 'code', 'POINTER_ERROR'), message='Pointer operation failed.'))
        sys.stdout.write(json.dumps(response) + '\n')
        sys.stdout.flush()
        done.set()
        return False
    def reader():
        for line in sys.stdin:
            done = threading.Event()
            GLib.idle_add(dispatch, line, done)
            done.wait()
        GLib.idle_add(close)
    threading.Thread(target=reader, daemon=True).start()
    Gtk.main()


if __name__ == '__main__':
    main()
