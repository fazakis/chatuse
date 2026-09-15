import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux'))
from core import ChatuseError, References, screen_point, scaled_size, same_geometry, number


class CoreTests(unittest.TestCase):
    def test_scaled_coordinates_with_negative_display_origin(self):
        self.assertEqual(screen_point(400, 200, 800, 600, dict(x=-1600, y=-200, width=1600, height=1200)), (-800, 200))

    def test_out_of_bounds_and_nonfinite_coordinates(self):
        frame = dict(x=0, y=0, width=100, height=100)
        for x in [-1, 100, float('nan'), float('inf'), True, None, '5']:
            with self.subTest(x=x), self.assertRaises(ChatuseError):
                screen_point(x, 0, 100, 100, frame)

    def test_capture_scaling_and_empty_regions(self):
        self.assertEqual(scaled_size(dict(width=600, height=400), 900), (900, 600))
        self.assertEqual(scaled_size(dict(width=100, height=50), 900), (200, 100))
        with self.assertRaises(ChatuseError):
            scaled_size(dict(width=0, height=400))

    def test_reference_expiry_and_bounded_cache(self):
        now = [0]
        cache = References(capacity=2, clock=lambda: now[0])
        first = cache.add({'value': 1})
        second = cache.add({'value': 2})
        cache.add({'value': 3})
        with self.assertRaises(ChatuseError) as missing:
            cache.get(first, 'UNKNOWN_ELEMENT')
        self.assertEqual(missing.exception.code, 'UNKNOWN_ELEMENT')
        now[0] = 120
        self.assertEqual(cache.get(second, 'UNKNOWN_ELEMENT')['value'], 2)
        now[0] = 120.01
        with self.assertRaises(ChatuseError) as expired:
            cache.get(second, 'UNKNOWN_ELEMENT')
        self.assertEqual(expired.exception.code, 'STALE_SNAPSHOT')

    def test_reference_ids_are_session_local(self):
        one, two = References(), References()
        key = one.add('only in first session')
        with self.assertRaises(ChatuseError):
            two.get(key, 'UNKNOWN_SCREENSHOT')

    def test_stale_geometry(self):
        a = dict(x=-10, y=20, width=400, height=300)
        self.assertTrue(same_geometry(a, dict(a)))
        self.assertFalse(same_geometry(a, dict(a, width=402)))
        self.assertFalse(same_geometry(a, dict(a, x=0)))


if __name__ == '__main__':
    unittest.main()
