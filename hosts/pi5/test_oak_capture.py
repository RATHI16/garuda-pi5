import time
import unittest
from unittest.mock import patch
import oak_capture


class Response:
    def __init__(self, stale=False, truncated=False):
        self.headers = {'X-Oak-Sequence':'7','X-Oak-Epoch':'session',
            'X-Oak-Capture-Ns':str(time.monotonic_ns()-(2_000_000_000 if stale else 0)),
            'X-Oak-Width':'1280','X-Oak-Height':'720','X-Oak-Format':'BGR888',
            'Content-Length':str(1280*720*3)}
        self.data = b'x'*(10 if truncated else 1280*720*3)
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, n): return self.data[:n]


class CaptureTests(unittest.TestCase):
    def run_once(self, **kwargs):
        backend = oak_capture.OakRGBBackend()
        backend.configure(1280,720,15)
        accepted = []
        response = Response(**kwargs)
        def fetch(*args, **kw):
            # End the loop after one response, whether accepted or rejected.
            backend._stop.set()
            return response
        with patch.object(oak_capture, 'urlopen', side_effect=fetch):
            backend._run(lambda meta,pixels: accepted.append((meta,len(pixels))))
        return accepted

    def test_fresh_frame_preserves_clock_and_session(self):
        frames = self.run_once()
        self.assertEqual(len(frames),1)
        meta,size=frames[0]
        self.assertEqual((meta.sensor,meta.clock_domain,meta.frame_id),('oak-rgb:session','mono',7))
        self.assertEqual(size,1280*720*3)

    def test_stale_frame_is_not_delivered(self):
        self.assertEqual(self.run_once(stale=True),[])

    def test_truncated_frame_is_not_delivered(self):
        self.assertEqual(self.run_once(truncated=True),[])


if __name__ == '__main__':
    unittest.main()
