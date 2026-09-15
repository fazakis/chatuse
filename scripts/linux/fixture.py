#!/usr/bin/env python3
"""Owned GTK test window; never targets the user's applications."""
import json
import os
import signal
from pathlib import Path
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

os.umask(0o077)
output = Path(os.environ['CHATUSE_FIXTURE_OUTPUT'])
state = dict(pid=os.getpid(), clicks=0, text='', scroll=0, drags=0)


def save():
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False))
    temporary.replace(output)


def describe(widget, identifier):
    widget.get_accessible().set_description(identifier)
    return widget


window = Gtk.Window(title='Chatuse Ubuntu Fixture')
window.set_wmclass('chatuse-fixture', 'ChatuseFixture')
window.set_default_size(640, 580)
window.connect('destroy', Gtk.main_quit)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=20)
window.add(box)
title = Gtk.Label(label='Chatuse Ubuntu input test', xalign=0)
box.pack_start(title, False, False, 0)
entry = describe(Gtk.Entry(), 'chatuse-text')
entry.get_accessible().set_name('Chatuse text')
entry.connect('changed', lambda widget: (state.update(text=widget.get_text()), save()))
box.pack_start(entry, False, False, 0)
button = describe(Gtk.Button(label='Increment'), 'chatuse-increment')
box.pack_start(button, False, False, 0)
result = describe(Gtk.Label(label='Clicks: 0', xalign=0), 'chatuse-result')
box.pack_start(result, False, False, 0)


def increment(*_):
    state['clicks'] += 1
    result.set_text('Clicks: ' + str(state['clicks']))
    save()


button.connect('clicked', increment)
password = describe(Gtk.Entry(), 'chatuse-password')
password.set_visibility(False)
password.set_text('fixture-private-value')
password.get_accessible().set_name('Password test field')
box.pack_start(password, False, False, 0)
scroller = describe(Gtk.ScrolledWindow(), 'chatuse-scroll')
scroller.set_size_request(-1, 140)
text = Gtk.TextView(editable=False)
text.get_buffer().set_text('\n'.join('Scroll line ' + str(i) for i in range(1, 101)))
scroller.add(text)
box.pack_start(scroller, True, True, 0)
scroller.get_vadjustment().connect('value-changed', lambda adjustment: (state.update(scroll=adjustment.get_value()), save()))
drag = describe(Gtk.DrawingArea(), 'chatuse-drag')
drag.set_size_request(-1, 90)
drag.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
drag_start = None


def draw(_widget, cr):
    cr.set_source_rgb(.1, .48, 1)
    cr.paint()
    cr.set_source_rgb(1, 1, 1)
    cr.move_to(18, 45)
    cr.show_text('Drag across this blue area')


def press(_widget, event):
    global drag_start
    drag_start = (event.x, event.y)


def release(_widget, event):
    global drag_start
    if drag_start and abs(event.x - drag_start[0]) > 30:
        state['drags'] += 1
        save()
    drag_start = None


drag.connect('draw', draw)
drag.connect('button-press-event', press)
drag.connect('button-release-event', release)
box.pack_start(drag, False, False, 0)


def second_window():
    other = Gtk.Window(title='Chatuse Secondary Fixture')
    other.set_default_size(400, 200)
    other.add(Gtk.Label(label='Second window of the same test process'))
    other.show_all()
    other.present()
    return True


GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, second_window)
window.show_all()
entry.grab_focus()
save()
Gtk.main()
