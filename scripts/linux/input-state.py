"""Test-only input cleanup probe; reports no key values or window contents."""
import hashlib
import json
from Xlib import X, display

connection = display.Display()
info = connection.display.info
mapping = connection.get_keyboard_mapping(info.min_keycode, info.max_keycode - info.min_keycode + 1)
print(json.dumps(dict(keymap=hashlib.sha256(repr(mapping).encode()).hexdigest(),
                      heldKeys=sum(bin(value).count('1') for value in connection.query_keymap()),
                      mouseButtons=connection.screen().root.query_pointer().mask &
                      (X.Button1Mask | X.Button2Mask | X.Button3Mask | X.Button4Mask | X.Button5Mask))))
connection.close()
