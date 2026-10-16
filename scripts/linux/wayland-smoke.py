#!/usr/bin/env python3
"""Opt-in live GNOME Wayland check. Uses only an owned GTK fixture for input.

Run from the unlocked desktop. Approve the normal portal dialog if presented.
No consent automation, root access, compositor changes, or Xwayland input.
"""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import time
from PIL import Image

os.umask(0o077)
root = Path(__file__).resolve().parents[2]
artifacts = root / 'artifacts'
artifacts.mkdir(exist_ok=True)
state_file = artifacts / 'wayland-fixture-state.json'
stop = root / 'STOP'
if stop.exists():
    raise SystemExit('Chatuse is stopped. Resume locally before running this test.')
env = dict(os.environ, CHATUSE_ROOT=str(root), GDK_BACKEND='wayland', CHATUSE_WAYLAND_FIXTURE='1')
helper = subprocess.Popen([str(root / 'runtime/chatuse-native')], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True, env=env)
fixture = None
owned_stop = False
checks = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)
    print('PASS:', name, flush=True)


def call(method, params=None, error=None):
    helper.stdin.write(json.dumps(dict(id=method, method=method, params=params or {}))+'\n')
    helper.stdin.flush()
    response = json.loads(helper.stdout.readline())
    if error:
        check(error, response.get('error', {}).get('code') == error)
        return
    if 'error' in response:
        raise RuntimeError(response['error'])
    return response['result']


def state():
    return json.loads(state_file.read_text())


try:
    check('Wayland backend', call('status')['backend'] == 'atspi-wayland-portal')
    print('Approve sharing one monitor and remote interaction if prompted.', flush=True)
    check('Portal input permission', call('request_permissions')['inputAvailable'])
    fixture = subprocess.Popen(['/usr/bin/python3', str(root / 'scripts/linux/fixture.py')],
                               env=dict(env, CHATUSE_FIXTURE_OUTPUT=str(state_file)))
    deadline = time.monotonic()+8
    while time.monotonic() < deadline:
        if state_file.exists() and state().get('pid') == fixture.pid:
            break
        time.sleep(.1)
    time.sleep(1)
    target = dict(pid=fixture.pid)
    tree = call('inspect', target)
    check('Secure field redacted', any(e.get('value') == '[secure field]' for e in tree['elements']))
    check('No misleading global AT-SPI bounds', all('bounds' not in e for e in tree['elements']))
    call('type_text', dict(target, text='Wayland λ 中 ✓'))
    time.sleep(.2)
    check('Unicode accessible insertion', state()['text'] == 'Wayland λ 中 ✓')
    call('press_key', dict(target, key='a', modifiers=['ctrl']))
    call('type_text', dict(target, text='Chatuse Wayland verified'))
    time.sleep(.2)
    check('Ctrl+A shortcut', state()['text'] == 'Chatuse Wayland verified')
    button = next(e for e in tree['elements'] if e.get('description') == 'chatuse-increment')
    call('click', dict(snapshotId=tree['snapshotId'], elementId=button['id']))
    time.sleep(.2)
    check('AT-SPI click', state()['clicks'] == 1)
    shot = call('screenshot', dict(maxWidth=1280, ocr=True))
    check('PipeWire capture', shot['captureMode'] == 'wayland-pipewire-monitor-cursor-embedded')
    image = Image.open(io.BytesIO(base64.b64decode(shot['imageBase64']))).convert('RGB')
    check('Valid screenshot pixels', image.size == (shot['width'], shot['height']))
    def region(rgb):
        points = [(x,y) for y in range(image.height) for x in range(image.width)
                  if all(abs(a-b)<3 for a,b in zip(image.getpixel((x,y)), rgb))]
        check('Owned colored region visible', len(points)>1000)
        return (min(x for x,y in points), min(y for x,y in points),
                max(x for x,y in points), max(y for x,y in points))
    bx,by,br,bb=region((219,97,31))
    call('click', dict(screenshotId=shot['screenshotId'], x=(bx+br)/2, y=(by+bb)/2))
    time.sleep(.2)
    check('Portal coordinate click', state()['clicks'] == 2)
    left,top,right,bottom=region((26,122,255))
    # Fixture's scroller is directly above its blue drag area.
    call('scroll', dict(target, screenshotId=shot['screenshotId'], x=(left+right)/2, y=top-60, dy=200))
    time.sleep(.3)
    check('Portal scrolling', state()['scroll'] > 0)
    call('drag', dict(screenshotId=shot['screenshotId'],fromX=left+20,fromY=(top+bottom)/2,
                     toX=right-20,toY=(top+bottom)/2,durationMs=500))
    time.sleep(.2)
    check('Portal drag', state()['drags'] == 1)
    call('move_pointer', dict(screenshotId=shot['screenshotId'],x=shot['width']/2,y=shot['height']/2))
    after=call('screenshot', dict(maxWidth=1280))
    check('Fresh observation', after['screenshotId'] != shot['screenshotId'])
    call('screenshot', target, error='WINDOW_CAPTURE_UNSUPPORTED')
    with stop.open('x'):
        owned_stop=True
    call('move_pointer', dict(x=10,y=10), error='STOPPED')
    report=dict(passed=True,checks=checks,backend='atspi-wayland-portal')
except Exception:
    report=dict(passed=False,checks=checks,backend='atspi-wayland-portal')
    raise
finally:
    if owned_stop:
        stop.unlink()
    if fixture:
        fixture.terminate();fixture.wait(timeout=5)
    helper.stdin.close()
    try:
        helper.wait(timeout=8)
    except subprocess.TimeoutExpired:
        helper.terminate();helper.wait(timeout=5)
    (artifacts/'wayland-smoke-report.json').write_text(json.dumps(report,indent=2)+'\n')
