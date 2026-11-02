"""dm/tools/arm64_survey.py on the synthetic AArch64 fixture (arm64_fixture.py): no game data, the module is assembled word by word. The expected counts follow from the fixture's functions."""
from pathlib import Path
import struct
import sys
import unittest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'dm/tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import arm64_fixture as fx
import arm64_survey
import nso


def load(words=None):
    d, ranges = fx.build(words)
    return nso.load(d, 'fixture'), ranges


class Fixture(unittest.TestCase):
    def test_encodings_decode_as_intended(self):
        """Each hand-made encoder yields the instruction its name says, checked against Capstone"""
        md = arm64_survey._md()
        cases = {fx.ADDS(3, 2, 0): 'adds x3, x2, x0', fx.SUBS(4, 3, 1): 'subs x4, x3, x1', fx.MUL(0, 2, 4): 'mul x0, x2, x4', fx.MOVZ(0, 2): 'mov x0, #2',
                 fx.MOV(1, 18): 'mov x1, x18', fx.SVC(1): 'svc #1', fx.RET: 'ret', fx.MRS_TPIDR(0): 'mrs x0, tpidr_el0', fx.SUBSI(31, 0, 0): 'cmp x0, #0'}
        for w, want in cases.items():
            i = next(md.disasm(struct.pack('<I', w), 0))
            self.assertEqual(f'{i.mnemonic} {i.op_str}'.strip(), want, hex(w))

    def test_loads_as_a_module(self):
        m, ranges = load()
        self.assertEqual(m.text_end, ranges['svc_setheap'][1])
        self.assertEqual(len(arm64_survey.survey(m).functions), sum(1 for _, fde, _ in fx.FUNCTIONS if fde))


class Survey(unittest.TestCase):
    def setUp(self):
        self.m, self.ranges = load()
        self.s = arm64_survey.survey(self.m)
        self.by = {f.start: f for f in self.s.functions}

    def test_function_boundaries_come_from_the_unwind_data(self):
        self.assertEqual([(f.start, f.end) for f in self.s.functions], [r for n, r in self.ranges.items() if n != 'svc_setheap'])

    def test_direct_branches_stay_inside_or_name_their_target(self):
        br = self.by[self.ranges['branch'][0]].of('branch')
        self.assertEqual([(i.where, i.target) for i in br], [('inside', self.ranges['branch'][0] + 16)])
        loop = self.by[self.ranges['loop'][0]].of('branch')
        self.assertEqual({i.where for i in loop}, {'inside'})

    def test_nested_calls_name_the_called_functions(self):
        calls = self.by[self.ranges['caller'][0]].of('call')
        self.assertEqual([(i.target, i.where) for i in calls],
                         [(self.ranges['arith'][0], 'function:sub_%x' % self.ranges['arith'][0]), (self.ranges['branch'][0], 'function:sub_%x' % self.ranges['branch'][0])])

    def test_indirect_branches_are_listed(self):
        f = self.by[self.ranges['indirect'][0]]
        self.assertEqual([i.text for i in f.of('icall')], ['blr x1']); self.assertEqual([i.text for i in f.of('ibranch')], ['br x2'])

    def test_what_the_rewriter_touches_is_listed(self):
        f = self.by[self.ranges['tls'][0]]
        self.assertEqual([i.text for i in f.of('sysreg')], ['mrs x0, tpidr_el0', 'mrs x3, tpidrro_el0']); self.assertEqual([i.text for i in f.of('x18')], ['mov x1, x18'])

    def test_svc_wrapper_without_unwind_data_is_found(self):
        self.assertEqual(self.s.svc_stubs, ((self.ranges['svc_setheap'][0], 1),))

    def test_the_header_words_are_a_gap_not_a_function(self):
        self.assertEqual(self.s.gaps, ((0, fx.START),))

    def test_deterministic(self):
        self.assertEqual(self.s.report(), arm64_survey.survey(load()[0]).report())


class Errors(unittest.TestCase):
    def test_undecodable_word_names_address_word_and_function(self):
        words, ranges, _ = fx.text_layout()
        at = ranges['arith'][0]
        words[at // 4 + 1] = 0xFFFFFFFF
        m, _ = load(words)
        with self.assertRaises(arm64_survey.SurveyError) as c: arm64_survey.survey(m)
        msg = str(c.exception)
        self.assertIn(f'{at + 4:#x}', msg); self.assertIn('0xffffffff', msg); self.assertIn(f'sub_{at:x}', msg)

    def test_missing_mod0_is_an_error_not_an_empty_survey(self):
        words, _, _ = fx.text_layout()
        words[1] = 0x8000   # the MOD0 pointer leads to no MOD0
        m, _ = load(words)
        with self.assertRaises(arm64_survey.SurveyError): arm64_survey.survey(m)


if __name__ == '__main__':
    unittest.main()
