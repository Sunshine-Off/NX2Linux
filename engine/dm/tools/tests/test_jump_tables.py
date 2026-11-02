"""jump_tables.py, on hand-assembled code in the shapes compilers use for a switch: byte and halfword offset tables (LLVM: `add x10, x10, x11, lsl #2`), a signed byte table with an extended-register add (GCC:
`add x3, x3, w1, sxtb #2`), a word table relative to itself, a guard that bounds the table, a table nothing bounds, entries that point outside the function, and the indirect jumps that are not tables.
No Remill needed."""
import struct, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import jump_tables as jt
from nso_asm import ADRP, ADDI, BR, NOP
from module_fixture import ADR

F, TABLE, SIZE = 0x1000, 0x2000, 0x3000
RET = 0xD65F03C0
CMP = lambda r, k: 0x7100001F | (k << 10) | (r << 5)   # cmp wr, #k
LDRB_X = lambda t, n, m: 0x38606800 | (m << 16) | (n << 5) | t             # ldrb wt, [xn, xm]
LDRH_X = lambda t, n, m: 0x78607800 | (m << 16) | (n << 5) | t             # ldrh wt, [xn, xm, lsl #1]
LDRB_W = lambda t, n, m: 0x38604800 | (m << 16) | (n << 5) | t             # ldrb wt, [xn, wm, uxtw]
LDRSW = lambda t, n, m: 0xB8A07800 | (m << 16) | (n << 5) | t              # ldrsw xt, [xn, xm, lsl #2]
ADD_LSL2 = lambda d, n, m: 0x8B000000 | (m << 16) | (2 << 10) | (n << 5) | d   # add xd, xn, xm, lsl #2
ADD_SXTB2 = lambda d, n, m: 0x8B208800 | (m << 16) | (n << 5) | d              # add xd, xn, wm, sxtb #2
ADD = lambda d, n, m: 0x8B000000 | (m << 16) | (n << 5) | d                    # add xd, xn, xm
BHI = lambda frm, to: 0x54000008 | (((to - frm) >> 2 & 0x7FFFF) << 5)
BLS = lambda frm, to: 0x54000009 | (((to - frm) >> 2 & 0x7FFFF) << 5)


def image(words, table=b''):
    img = bytearray(SIZE)
    img[F:F + 4 * len(words)] = struct.pack('<%dI' % len(words), *words)
    img[TABLE:TABLE + len(table)] = table
    return img, F + 4 * len(words)


def llvm(load, entries, shift_table=0, guard=True):
    """cmp ; b.hi ; adrp/add the table ; adr the first case ; load the entry ; add lsl #2 ; br ; then n 2-word cases"""
    n = len(entries)
    head = ([CMP(0, n - 1), BHI(F + 4, F + 32 + 8 * n)] if guard else [NOP, NOP]) + [ADRP(9, F + 8, TABLE), ADDI(9, 9, TABLE & 0xFFF), ADR(10, F + 16, F + 32), load(11, 9, 0), ADD_LSL2(10, 10, 11), BR(10)]
    return head + [NOP, RET] * n + [NOP, RET]


class Tables(unittest.TestCase):
    def find(self, words, table, end=None):
        img, e = image(words, table)
        return jt.find_jump_tables(img, F, end or e)

    def test_llvm_byte_table(self):
        words = llvm(LDRB_X, [0, 2, 4, 6])
        (t,) = self.find(words, bytes([0, 2, 4, 6]))
        self.assertEqual((t.br, t.table, t.targets), (F + 28, TABLE, tuple(F + 32 + 8 * k for k in range(4))))

    def test_llvm_halfword_table(self):
        words = llvm(LDRH_X, [0, 2, 4])
        (t,) = self.find(words, struct.pack('<3H', 0, 2, 4))
        self.assertEqual(t.targets, tuple(F + 32 + 8 * k for k in range(3)))

    def test_gcc_signed_byte_table_with_an_extended_register_add(self):
        # adr x3, case1 ; ldrb w1, [x2, w1, uxtw] ; add x3, x3, w1, sxtb #2 ; br x3: entries are signed word offsets from the label (one is negative)
        words = [CMP(1, 2), BHI(F + 4, F + 56), ADRP(2, F + 8, TABLE), ADDI(2, 2, TABLE & 0xFFF), ADR(3, F + 16, F + 40), LDRB_W(1, 2, 1), ADD_SXTB2(3, 3, 1), BR(3)] + [NOP, RET] * 3 + [NOP, RET]
        (t,) = self.find(words, bytes([0xFE, 0x00, 0x02]))   # -2, 0, +2 words around the label at F + 40
        self.assertEqual(t.targets, (F + 32, F + 40, F + 48))

    def test_word_table_relative_to_itself(self):
        # ldrsw x11, [x9, x8, lsl #2] ; add x10, x9, x11 ; br x10: the entries are byte offsets from the table
        words = [CMP(0, 1), BHI(F + 4, F + 44), ADRP(9, F + 8, TABLE), ADDI(9, 9, TABLE & 0xFFF), LDRSW(11, 9, 0), ADD(10, 9, 11), BR(10)]
        words += [NOP, RET, NOP, RET, NOP, RET]
        a, b = F + 4 * 7, F + 4 * 9
        (t,) = self.find(words, struct.pack('<2i', a - TABLE, b - TABLE))
        self.assertEqual(t.targets, (a, b))

    def test_a_table_nothing_bounds_is_read_while_the_targets_stay_inside_the_function(self):
        words = llvm(LDRB_X, [0, 2, 4], guard=False)
        (t,) = self.find(words, bytes([0, 2, 4, 200, 1]))   # 200 points far outside: the scan stops there
        self.assertEqual(t.targets, tuple(F + 32 + 8 * k for k in range(3)))

    def test_a_guard_bounds_the_table_and_targets_outside_the_function_are_dropped(self):
        words = llvm(LDRB_X, [0, 2, 4, 6])   # cmp w0, #3: four entries, plus one of slack
        (t,) = self.find(words, bytes([0, 2, 4, 6, 0, 2, 255, 255]))
        self.assertEqual(t.targets, tuple(F + 32 + 8 * k for k in range(4)))
        (u,) = self.find(words, bytes([0, 2, 100, 6]))
        self.assertEqual(u.targets, (F + 32, F + 40, F + 56))   # entry 2 points past the function

    def test_indirect_jumps_that_are_not_tables_are_left_alone(self):
        # a function pointer loaded from memory, a tail call through x16, and a register nothing is known about
        words = [ADRP(8, F, TABLE), ADDI(8, 8, 0), 0xF9400101, BR(1), BR(16), BR(17), BR(5), RET]
        self.assertEqual(self.find(words, bytes(16)), [])

    def test_a_table_address_overwritten_before_the_load_is_not_a_table(self):
        words = llvm(LDRB_X, [0, 2, 4, 6])
        words[5:5] = [0xD28000A9]   # mov x9, #5 between the adrp/add and the ldrb: the load no longer reads a known table
        self.assertEqual(self.find(words, bytes([0, 2, 4, 6])), [])

    def test_llvm_jumps_through_x16_with_a_word_table_relative_to_itself(self):
        # Xenoblade 2's csvb::CCsvbUtility: ldrsw x16, [x15, x17, lsl #2] ; add x16, x16, x15 ; br x16 (the table base kept in x15 since the prologue)
        words = [CMP(0, 1), BHI(F + 4, F + 44), ADRP(15, F + 8, TABLE), ADDI(15, 15, TABLE & 0xFFF), LDRSW(16, 15, 0), ADD(16, 16, 15), BR(16)]
        words += [NOP, RET, NOP, RET, NOP, RET]
        a, b = F + 4 * 7, F + 4 * 9
        (t,) = self.find(words, struct.pack('<2i', a - TABLE, b - TABLE))
        self.assertEqual(t.targets, (a, b))

    def test_a_plt_stub_through_x17_is_still_not_a_table(self):
        words = [ADRP(16, F, TABLE), 0xF9400211, ADDI(16, 16, 0), BR(17)]   # adrp x16 ; ldr x17, [x16] ; add x16, x16, #0 ; br x17
        self.assertEqual(self.find(words, bytes(16)), [])


    def test_a_compound_guard_with_ccmp_bounds_the_table_by_its_larger_immediate(self):
        # Xenoblade 2's fw::PadManager::makePadData: cmp w4, #0 ; ccmp w8, #3, #2, ne ; b.hi default ; ... br. Reading only the cmp gave a two-entry table
        words = [0x7100009F, 0x7A431902, BHI(F + 8, F + 52), ADRP(9, F + 12, TABLE), ADDI(9, 9, TABLE & 0xFFF), LDRSW(11, 9, 0), ADD(10, 11, 9), BR(10)]
        words += [NOP, RET] * 5
        base = F + 4 * 8
        entries = [base + 8 * k - TABLE for k in range(4)]
        (t,) = self.find(words, struct.pack('<4i', *entries))
        self.assertEqual(t.targets, tuple(base + 8 * k for k in range(4)))

    def test_an_incoming_guard_bounds_a_second_dispatch_after_another_table(self):
        # Xenoblade 2 branches over a small switch to a second dispatch that reuses
        # w8.  The nearest linear cmp belongs to the skipped switch; the second
        # table is bounded by the cmp immediately before the incoming b.ls.
        table1, table2 = TABLE + 0x100, TABLE
        second = F + 9 * 4
        words = [CMP(8, 33), BLS(F + 4, second), CMP(8, 4),
                 ADRP(9, F + 12, table1), ADDI(9, 9, table1 & 0xFFF),
                 LDRSW(8, 9, 8), ADD(8, 8, 9), BR(8), RET,
                 ADRP(9, second, table2), ADDI(9, 9, table2 & 0xFFF),
                 LDRSW(8, 9, 8), ADD(8, 8, 9), BR(8)]
        cases = F + 14 * 4
        words += [NOP, RET] * 4
        data = bytearray(0x200)
        struct.pack_into('<5i', data, 0x100, *([cases - table1] * 5))
        targets = [cases, cases + 8, cases + 16, cases + 24]
        values = [targets[0]] * 34
        values[12], values[32], values[33] = targets[1], targets[2], targets[3]
        struct.pack_into('<34i', data, 0, *(x - table2 for x in values))
        tables = self.find(words, data)
        self.assertEqual(tables[1].targets, tuple(targets))


if __name__ == '__main__': unittest.main()
