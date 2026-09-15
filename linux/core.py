"""Platform-independent validation shared by the Linux backend and its tests."""
import math
import time
import uuid
from collections import OrderedDict


class ChatuseError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise ChatuseError(code, message)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        fail('INVALID_COORDINATES', 'Expected finite numeric coordinates.')
    return value


def contains(frame, x, y):
    return frame['x'] <= x < frame['x'] + frame['width'] and frame['y'] <= y < frame['y'] + frame['height']


def screen_point(x, y, width, height, frame):
    x, y = number(x), number(y)
    if width <= 0 or height <= 0 or not 0 <= x < width or not 0 <= y < height:
        fail('INVALID_COORDINATES', 'Coordinates must be inside the referenced screenshot.')
    return frame['x'] + x * frame['width'] / width, frame['y'] + y * frame['height'] / height


def scaled_size(frame, maximum=1440):
    if frame['width'] <= 0 or frame['height'] <= 0:
        fail('EMPTY_CAPTURE', 'The capture region is empty.')
    scale = min(max(320, min(int(maximum), 3840)) / frame['width'], 2)
    return max(1, int(frame['width'] * scale)), max(1, int(frame['height'] * scale))


def same_geometry(a, b):
    return all(abs(a[k] - b[k]) < 2 for k in ('x', 'y', 'width', 'height'))


class References:
    def __init__(self, capacity=8, lifetime=120, clock=time.monotonic):
        self.capacity, self.lifetime, self.clock = capacity, lifetime, clock
        self.items = OrderedDict()

    def add(self, value):
        key = str(uuid.uuid4())
        self.items[key] = (self.clock(), value)
        while len(self.items) > self.capacity:
            self.items.popitem(last=False)
        return key

    def get(self, key, code):
        if key not in self.items:
            fail(code, 'Use a reference from a recent observation in this helper session.')
        created, value = self.items[key]
        if self.clock() - created > self.lifetime:
            del self.items[key]
            fail('STALE_SNAPSHOT', 'Observe the app again; this reference has expired.')
        return value
