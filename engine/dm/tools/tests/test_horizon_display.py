"""horizon_display.py: a frame goes through libnx2linux_display.so to a Vulkan swapchain (lavapipe or a GPU). Skipped when the library is not built or no Vulkan window can be made."""
import os, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import horizon_display as hd


@unittest.skipUnless(hd.library_path(), 'libnx2linux_display.so is not built')
class Display(unittest.TestCase):
    def open(self):
        try: return hd.Display('test', 320, 180, hidden=True)
        except hd.DisplayError as e: self.skipTest(f'no Vulkan window: {e}')

    def test_frames_are_presented_and_counted(self):
        with self.open() as d:
            w, h = 64, 36
            frame = bytes(v for y in range(h) for x in range(w) for v in (x * 4 & 255, y * 7 & 255, 128, 255))
            for _ in range(5): d.present(frame, w, h)
            self.assertEqual(d.frames, 5)
            self.assertTrue(d.gpu)

    def test_a_frame_too_small_for_its_size_is_refused(self):
        with self.open() as d:
            with self.assertRaises(hd.DisplayError): d.present(b'\0' * 10, 64, 36)

    def test_events_can_be_polled(self):
        with self.open() as d: self.assertIsInstance(d.events(), list)


if __name__ == '__main__': unittest.main()
