"""tools/soak_summary.py (NX2Linux issue 22): the numbers of a long run from its file. No game: files written here."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import soak_summary as ss

HEADER = ','.join(ss.COLUMNS)


def line(t, rss=2_000_000, threads=40, fds=60, frames=0, fps=18.0, idle=0, faults=0, stall=0):
    return f'{t:.1f},{rss},{threads},{fds},{frames},{fps:.2f},{idle},{faults},{stall}'


def write(tmp, lines, header=True):
    p = Path(tmp) / 'soak.csv'; p.write_text(('\n'.join(([HEADER] if header else []) + lines)) + '\n'); return str(p)


class Read(unittest.TestCase):
    def test_rows_are_read_and_a_cut_line_is_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, [line(0), line(10, frames=180), '20.0,2000000,40'])
            rows, bad = ss.read(path)
        self.assertEqual(len(rows), 2); self.assertEqual(bad, 1); self.assertEqual(rows[1]['frames'], 180.0)

    def test_an_empty_cell_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, bad = ss.read(write(tmp, ['0.0,,,,0,0.00,0,0,0']))
        self.assertIsNone(rows[0]['rss_kb']); self.assertEqual(bad, 0)

    def test_a_file_without_a_header_is_read_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = ss.read(write(tmp, [line(0), line(10)], header=False))
        self.assertEqual(len(rows), 2)


class Numbers(unittest.TestCase):
    def test_slope_of_a_straight_line(self):
        pts = [(t, 1000 + t * 2) for t in range(0, 3600, 60)]
        self.assertAlmostEqual(ss.slope_per_hour(pts), 7200, places=3)

    def test_slope_needs_three_points_and_some_time(self):
        self.assertIsNone(ss.slope_per_hour([(0, 1), (1, 2)])); self.assertIsNone(ss.slope_per_hour([(5, 1), (5, 2), (5, 3)]))

    def test_a_steady_run(self):
        rows = [dict(zip(ss.COLUMNS, map(float, line(t, frames=t * 18).split(',')))) for t in range(0, 3601, 10)]
        s = ss.summarize(rows)
        self.assertEqual(s['rows'], 361); self.assertAlmostEqual(s['fps_mean'], 18.0); self.assertEqual(s['stalls'], 0)
        self.assertAlmostEqual(s['rss_slope_kb_per_hour'], 0.0, places=6); self.assertEqual(s['rss_kb'][:2], (2_000_000, 2_000_000))
        self.assertEqual(len(s['fps_blocks']), 6); self.assertAlmostEqual(s['fps_blocks'][0][1], 18.0)

    def test_growing_memory_shows_as_a_slope_and_the_warmup_is_left_out(self):
        # 1 GB during the first 10 percent, then +10 MB per minute
        rows = []
        for t in range(0, 3601, 60):
            rss = 1_000_000 if t < 360 else 1_000_000 + (t - 360) // 60 * 10_240
            rows.append(dict(zip(ss.COLUMNS, [float(t), float(rss), 40.0, 60.0, t * 18.0, 18.0, 0.0, 0.0, 0.0])))
        s = ss.summarize(rows)
        self.assertAlmostEqual(s['rss_slope_kb_per_hour'] / 1024, 600, delta=15)   # 10 MB per minute is 600 MB per hour

    def test_stalls_are_counted_by_episode_and_the_longest_idle_is_kept(self):
        base = [0, 0, 1, 1, 1, 0, 0, 1, 0]; idle = [0, 0, 60, 90, 120, 0, 0, 60, 0]
        rows = [dict(zip(ss.COLUMNS, [t * 10.0, 1.0, 1.0, 1.0, t * 10.0, 1.0, float(i), 0.0, float(st)])) for t, (st, i) in enumerate(zip(base, idle))]
        s = ss.summarize(rows)
        self.assertEqual(s['stalls'], 2); self.assertEqual(s['longest_idle_s'], 120)

    def test_no_rows(self):
        self.assertIsNone(ss.summarize([])); self.assertEqual(ss.text(None), 'no rows in the file')

    def test_a_missing_memory_column_says_no_data(self):
        rows = [dict(zip(ss.COLUMNS, [float(t), None, None, None, 0.0, 0.0, 0.0, 0.0, 0.0])) for t in range(0, 100, 10)]
        out = ss.text(ss.summarize(rows))
        self.assertIn('memory (MB): no data', out); self.assertIn('memory slope after the first 10 percent: not enough rows', out)


class Command(unittest.TestCase):
    def test_the_text_for_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, [line(t, frames=t * 18) for t in range(0, 61, 10)] + ['70.0,12'])
            r = subprocess.run([sys.executable, '-I', str(TOOLS / 'soak_summary.py'), path], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('7 rows over 60 s', r.stdout)
        self.assertIn('1 line(s) skipped', r.stdout); self.assertIn('18.00 per second', r.stdout); self.assertIn('stalls: 0', r.stdout)

    def test_a_missing_file_is_a_message_not_a_traceback(self):
        r = subprocess.run([sys.executable, '-I', str(TOOLS / 'soak_summary.py'), '/nonexistent/soak.csv'], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertIn('cannot read', r.stderr); self.assertNotIn('Traceback', r.stderr)


if __name__ == '__main__': unittest.main()
