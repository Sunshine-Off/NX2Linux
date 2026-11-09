"""tools/ab_bench.py (NX2Linux issue 19): the order of the runs, the arithmetic, the verdict, the checks before a run, and a whole protocol on fake commands. No game: the commands print the lines horizon_run.py prints."""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import ab_bench as ab

FAKE = ("import os, random; v = os.environ['VARIANT']; r = random.Random(os.environ['SEED']); "
        "base = {'A': 1000, 'B': float(os.environ.get('B_FRAMES', 1000))}[v]; "
        "m = os.environ['NX2LINUX_FRAME_MARKS'].split(','); "
        "print('[frames] at %s s: 200' % m[0]); print('[frames] at %s s: %d' % (m[1], 200 + base + r.randint(-5, 5)))")


def silent(*a, **k): pass


class Order(unittest.TestCase):
    def test_a_b_b_a_blocks(self):
        self.assertEqual(ab.order(1), list('ABBA')); self.assertEqual(''.join(ab.order(3)), 'ABBAABBAABBA')
        self.assertEqual(ab.order(3).count('A'), 6); self.assertEqual(ab.order(3).count('B'), 6)

    def test_the_command_line_prints_it(self):
        r = subprocess.run([sys.executable, str(TOOLS / 'ab_bench.py'), 'order'], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), 'A B B A A B B A A B B A')


class Arithmetic(unittest.TestCase):
    def test_summary_by_hand(self):
        s = ab.summary([10, 12, 14, 16])       # mean 13, variance (9 + 1 + 1 + 9) / 3 = 20/3
        self.assertAlmostEqual(s['mean'], 13); self.assertAlmostEqual(s['sd'], math.sqrt(20 / 3)); self.assertAlmostEqual(s['se'], math.sqrt(20 / 3) / 2)

    def test_one_value_has_no_deviation(self):
        self.assertTrue(math.isnan(ab.summary([5])['sd']))

    def test_the_difference_and_its_error(self):
        a, b = [100, 102, 98, 101, 99, 100], [110, 112, 108, 111, 109, 110]   # sd of each = sqrt(2), se = sqrt(2)/sqrt(6)
        c = ab.compare(a, b)
        self.assertAlmostEqual(c['diff'], 10); self.assertAlmostEqual(c['se_diff'], math.sqrt(2 * (2 / 6)))
        self.assertAlmostEqual(c['diff_percent'], 10); self.assertEqual(c['result'], 'difference')

    def test_under_two_standard_errors_is_not_a_result(self):
        a, b = [100, 110, 90, 105, 95, 100], [101, 111, 91, 106, 96, 101]    # 1 against a spread of ~7
        c = ab.compare(a, b)
        self.assertLess(c['diff_in_se'], 2); self.assertEqual(c['result'], 'not a result')

    def test_exactly_two_standard_errors_counts(self):
        c = ab.compare([0, 2, 0, 2, 0, 2], [0, 2, 0, 2, 0, 2]); self.assertEqual(c['result'], 'not a result')   # diff 0
        a = [10, 12, 10, 12, 10, 12]; se = ab.summary(a)['se']; b = [x + 2 * math.sqrt(2) * se for x in a]
        self.assertAlmostEqual(ab.compare(a, b)['diff_in_se'], 2); self.assertEqual(ab.compare(a, b)['result'], 'difference')

    def test_no_standard_error_is_no_result(self):
        self.assertEqual(ab.compare([1], [2])['result'], 'not a result'); self.assertEqual(ab.compare([], [])['result'], 'not a result')


class Reading(unittest.TestCase):
    def test_frames_between_two_marks(self):
        text = 'noise\n[frames] at 30 s: 412\nmore\n[frames] at 110 s: 1980\nframes presented: 2100\n'
        self.assertEqual(ab.window_frames(text, 30, 110), 1568)

    def test_a_missing_mark_gives_none(self):
        self.assertIsNone(ab.window_frames('[frames] at 30 s: 412\n', 30, 110)); self.assertIsNone(ab.window_frames('', 30, 110))

    def test_busy_fraction_from_two_samples(self):
        samples = iter([(1000, 900), (2000, 1700)])     # 1000 ticks passed, 800 of them idle
        self.assertAlmostEqual(ab.busy_fraction(1, read=lambda: next(samples), sleep=silent), 0.2)

    def test_other_runs_are_found_by_their_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            for pid, cmd in ((101, b'python\0horizon_run.py\0--modules\0x'), (102, b'bash\0-c\0ls'), (os.getpid(), b'horizon_run.py')):
                (Path(d) / str(pid)).mkdir(); (Path(d) / str(pid) / 'cmdline').write_bytes(cmd)
            self.assertEqual(ab.other_runs(proc=d), [101])


class Idle(unittest.TestCase):
    def test_returns_at_once_when_idle(self):
        self.assertEqual(ab.wait_until_idle(0.03, 60, 5, silent, busy=lambda s: 0.01, others=lambda: [], sleep=silent), (0.01, True))

    def test_waits_while_busy_then_goes(self):
        levels = iter([0.5, 0.2, 0.02]); clock = [0.0]
        def sleep(s): clock[0] += s
        r = ab.wait_until_idle(0.03, 600, 5, silent, busy=lambda s: next(levels), others=lambda: [], sleep=sleep, now=lambda: clock[0])
        self.assertEqual(r, (0.02, True)); self.assertGreater(clock[0], 0)

    def test_gives_up_and_says_so(self):
        clock = [0.0]
        def sleep(s): clock[0] += s
        r = ab.wait_until_idle(0.03, 30, 5, silent, busy=lambda s: 0.4, others=lambda: [], sleep=sleep, now=lambda: clock[0])
        self.assertEqual(r, (0.4, False))

    def test_another_game_running_is_not_idle_whatever_the_cpu(self):
        clock = [0.0]
        def sleep(s): clock[0] += s
        r = ab.wait_until_idle(0.03, 20, 5, silent, busy=lambda s: 0.0, others=lambda: [4242], sleep=sleep, now=lambda: clock[0])
        self.assertFalse(r[1])


def fake_exec(b_frames=1000, fail=()):
    """Stands for the game: runs the small program above with the variant's environment"""
    count = [0]
    def execute(command, env, timeout):
        count[0] += 1
        if count[0] in fail: return 3, 'crashed'
        e = {**os.environ, **env, 'SEED': str(count[0]), 'B_FRAMES': str(b_frames)}
        p = subprocess.run([sys.executable, '-c', FAKE], env=e, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout
    return execute


def idle_now(*a, **k): return 0.0, True


class Protocol(unittest.TestCase):
    def bench(self, **kw):
        d = tempfile.mkdtemp()
        args = dict(command=['game'], env_a={'VARIANT': 'A'}, env_b={'VARIANT': 'B'}, marks=(30, 110), pause=0, out=d, say=silent, wait=idle_now, sleep=silent, execute=fake_exec())
        args.update(kw)
        return ab.run_bench(**args), Path(d)

    def test_twelve_runs_in_the_order_with_a_log_each(self):
        res, d = self.bench()
        self.assertEqual(''.join(r['variant'] for r in res['runs']), 'ABBAABBAABBA'); self.assertEqual(len(list(d.glob('run-*.log'))), 12)
        self.assertEqual(res['A']['n'], 6); self.assertEqual(res['B']['n'], 6); self.assertTrue(res['balanced'])
        self.assertTrue((d / 'ab_bench.json').is_file()); self.assertEqual(json.loads((d / 'ab_bench.json').read_text())['schema'], 1)

    def test_equal_variants_are_not_a_result(self):
        res, _ = self.bench(); self.assertEqual(res['result'], 'not a result'); self.assertLess(abs(res['diff_percent']), 1)

    def test_a_real_gap_is_a_result_and_is_measured_between_the_marks(self):
        res, _ = self.bench(execute=fake_exec(b_frames=1100))
        self.assertEqual(res['result'], 'difference'); self.assertAlmostEqual(res['A']['mean'], 1000 / 80, delta=0.2); self.assertAlmostEqual(res['diff_percent'], 10, delta=0.5)   # fps over 80 s

    def test_the_marks_are_given_to_the_command(self):
        seen = []
        def execute(command, env, timeout): seen.append((env['NX2LINUX_FRAME_MARKS'], env['VARIANT'])); return 0, '[frames] at 5 s: 0\n[frames] at 15.5 s: 10\n'
        self.bench(execute=execute, marks=(5, 15.5))
        self.assertEqual({m for m, _ in seen}, {'5,15.5'}); self.assertEqual([v for _, v in seen], list('ABBAABBAABBA'))

    def test_fewer_than_six_runs_of_a_variant_are_refused(self):
        with self.assertRaises(ValueError): self.bench(blocks=2)

    def test_a_failed_run_is_left_out_and_the_series_is_flagged(self):
        res, _ = self.bench(execute=fake_exec(fail={5}))
        self.assertEqual(res['excluded'], [5]); self.assertFalse(res['balanced']); self.assertIn('not balanced', ab.table(res))

    def test_a_run_on_a_machine_that_was_not_idle_is_left_out(self):
        calls = [0]
        def wait(*a, **k):
            calls[0] += 1
            return (0.4, False) if calls[0] == 3 else (0.0, True)
        res, _ = self.bench(wait=wait)
        self.assertEqual(res['excluded'], [3]); self.assertFalse(res['balanced'])

    def test_a_variant_that_did_not_take_effect_is_left_out(self):
        inner = fake_exec()
        def execute(command, env, timeout):
            code, text = inner(command, env, timeout)
            return code, text + ('[svc] native synchronisation: 4 kernel calls in C\n' if env['VARIANT'] == 'B' and not env.get('BROKEN') else '')
        res, _ = self.bench(execute=execute, b_must_match=r'native synchronisation: 4 kernel calls', a_must_not_match=r'native synchronisation')
        self.assertEqual(res['excluded'], []); self.assertTrue(res['balanced'])
        res, _ = self.bench(execute=execute, env_b={'VARIANT': 'B', 'BROKEN': '1'}, b_must_match=r'native synchronisation: 4 kernel calls')   # B ran as A: nothing to compare
        self.assertEqual(res['excluded'], [2, 3, 6, 7, 10, 11]); self.assertFalse(res['balanced']); self.assertIn('not in effect', ab.table(res))
        res, _ = self.bench(execute=lambda c, e, t: (lambda r: (r[0], r[1] + '[svc] native synchronisation\n'))(inner(c, e, t)), a_must_not_match=r'native synchronisation')
        self.assertEqual(res['excluded'], [1, 4, 5, 8, 9, 12])   # A that shows the native path

    def test_a_run_with_no_mark_is_left_out(self):
        def execute(command, env, timeout): return 0, 'no marks here'
        res, _ = self.bench(execute=execute); self.assertEqual(len(res['excluded']), 12)

    def test_a_timeout_is_a_failed_run(self):
        def execute(command, env, timeout): raise subprocess.TimeoutExpired(command, timeout)
        res, d = self.bench(execute=execute, timeout=7)
        self.assertEqual(len(res['excluded']), 12); self.assertIn('timed out after 7 s', (d / 'run-01-A.log').read_text())

    def test_the_table_has_everything_and_report_prints_it_again(self):
        res, d = self.bench(execute=fake_exec(b_frames=1100))
        text = ab.table(res)
        for s in ('variant', 'mean', 'sd', 'se', 'B - A', 'standard error of the difference', 'result: difference', 'in the order they ran', 'run-12-A.log' if False else 'run-12'): self.assertIn(s, text)
        r = subprocess.run([sys.executable, str(TOOLS / 'ab_bench.py'), 'report', str(d / 'ab_bench.json')], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertEqual(r.stdout.strip(), text.strip())


class CommandLine(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, str(TOOLS / 'ab_bench.py'), *args], capture_output=True, text=True, timeout=60)

    def test_it_wants_a_command(self): self.assertNotEqual(self.cli('run', '--env-a', 'X=0', '--env-b', 'X=1').returncode, 0)
    def test_an_empty_first_word_names_the_unset_variable(self):
        r = self.cli('run', '--env-a', 'X=0', '--env-b', 'X=1', '--', '', '-I', 'x.py'); self.assertNotEqual(r.returncode, 0); self.assertIn('NX2_PYTHON', r.stderr)
    def test_a_command_that_does_not_exist_is_named(self):
        r = self.cli('run', '--env-a', 'X=0', '--env-b', 'X=1', '--', '/nonexistent/python', 'x.py'); self.assertNotEqual(r.returncode, 0); self.assertIn('cannot find the command', r.stderr)

    def test_it_wants_two_different_variants(self): self.assertIn('same', self.cli('run', '--env-a', 'X=0', '--env-b', 'X=0', '--', 'true').stderr)
    def test_it_wants_two_marks(self): self.assertIn('--marks', self.cli('run', '--env-a', 'X=0', '--env-b', 'X=1', '--marks', '110,30', '--', 'true').stderr)
    def test_it_refuses_two_blocks(self): self.assertIn('at least 6', self.cli('run', '--env-a', 'X=0', '--env-b', 'X=1', '--blocks', '2', '--', 'true').stderr)

    def test_a_whole_run_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / 'game.py'; script.write_text(FAKE)
            r = subprocess.run([sys.executable, str(TOOLS / 'ab_bench.py'), 'run', '--env-a', 'VARIANT=A', '--env-b', 'VARIANT=B', '--env-a', 'SEED=1', '--env-b', 'SEED=2', '--marks', '30,110', '--pause', '0',
                                '--max-busy', '1.0', '--idle-wait', '1', '--sample', '0.05', '--out', str(Path(d) / 'out'), '--', sys.executable, str(script)], capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            res = json.loads((Path(d) / 'out' / 'ab_bench.json').read_text())
            self.assertEqual(res['A']['n'] + res['B']['n'], 12 - len(res['excluded']))
            self.assertIn('B - A', r.stdout)


if __name__ == '__main__': unittest.main()
