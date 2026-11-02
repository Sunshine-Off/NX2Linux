"""The Unicorn reference (arm64_ref.py) against the independent Python model of `arith`, on the NZCV edge cases: carry out, signed overflow, zero, negative. Skipped without unicorn."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arm64_ref as ref

try: import unicorn   # noqa: F401
except ImportError: unicorn = None

M = ref.MASK
CASES = [(0, 0), (1, 2), (M, 1), (M, M), (1 << 63, 1 << 63), ((1 << 63) - 1, 1), (1 << 63, M), (0x7FFFFFFF, 0x80000000), (5, M - 4), (3, 1 << 63), (1, (1 << 63) - 1)]   # the last one sets V


@unittest.skipUnless(unicorn, 'needs unicorn (pip install unicorn)')
class Reference(unittest.TestCase):
    def test_arith_matches_the_independent_model(self):
        for a, b in CASES:
            got = ref.run('arith', {'x0': a, 'x1': b})
            self.assertEqual((got['x0'], got['nzcv']), ref.model_arith(a, b), (hex(a), hex(b)))

    def test_the_cases_reach_every_flag(self):
        seen = 0
        for a, b in CASES: seen |= ref.model_arith(a, b)[1]
        self.assertEqual(seen, 0b1111, 'the edge cases must set N, Z, C and V at least once')

    def test_run_is_deterministic_and_leaves_sp(self):
        a = ref.run('arith', {'x0': 7, 'x1': 9}); b = ref.run('arith', {'x0': 7, 'x1': 9})
        self.assertEqual(a, b); self.assertEqual(a['sp'], ref.STACK)

    def test_nested_calls_return_through_the_stack(self):
        got = ref.run('caller', {'x0': 4, 'x1': 6})   # bl arith, bl branch
        self.assertEqual(got['sp'], ref.STACK); self.assertIn(got['x0'], (1, 2))


if __name__ == '__main__':
    unittest.main()
