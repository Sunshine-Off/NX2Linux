"""A synthetic, fully controlled AArch64 NSO for the recompilation work (T1): a handful of small functions assembled here word by word, a MOD0 header and an .eh_frame_hdr that
describe them the way a retail module does. No game data: the bytes are made by this file. build() returns the NSO file, so tools and tests load it through nso.load like any module.

Layout (the text segment starts at 0):
  0x00 b _start; 0x04 the MOD0 offset (the module header every tool finds the unwind data by); 0x08..0x10 padding (nop)
  then the functions below, back to back. svc_setheap has no FDE, as the SDK's system call wrappers have none (nso_match.py's `svc #n; ret` case)
ro holds MOD0, the .eh_frame (one CIE, one FDE per function that has one) and the .eh_frame_hdr's binary search table."""
import hashlib, os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from nso_asm import B, ADDR, STP_PRE, LDP_POST, SUBI, BR, BLR, MRS_TPIDRRO, NOP

PAGE = 0x1000
RET = 0xD65F03C0
def ADDS(d, n, m): return 0xAB000000 | (m << 16) | (n << 5) | d
def SUBS(d, n, m): return 0xEB000000 | (m << 16) | (n << 5) | d
def SUBSI(d, n, imm12): return 0xF1000000 | (imm12 << 10) | (n << 5) | d
def MUL(d, n, m): return 0x9B007C00 | (m << 16) | (n << 5) | d
def MOVZ(d, imm16): return 0xD2800000 | (imm16 << 5) | d
def MOV(d, m): return 0xAA0003E0 | (m << 16) | d   # orr xd, xzr, xm
def BL(frm, to):
    off = to - frm; assert -(1 << 27) <= off < (1 << 27) and off % 4 == 0
    return 0x94000000 | ((off >> 2) & 0x3FFFFFF)
def BCOND(frm, to, cond):
    off = to - frm; assert -(1 << 20) <= off < (1 << 20) and off % 4 == 0
    return 0x54000000 | (((off >> 2) & 0x7FFFF) << 5) | cond
def CBZ(t, frm, to):
    off = to - frm; assert -(1 << 20) <= off < (1 << 20) and off % 4 == 0
    return 0xB4000000 | (((off >> 2) & 0x7FFFF) << 5) | t
def SVC(n): return 0xD4000001 | (n << 5)
def MRS_TPIDR(t): return 0xD53BD040 | t
EQ = 0


# name -> (has FDE, function(at, me) → words); `at` maps names to their start, `me` is the function's own start. Order is the layout order
def _arith(at, me): return [ADDR(2, 0, 1), ADDS(3, 2, 0), SUBS(4, 3, 1), MUL(0, 2, 4), RET]
def _branch(at, me): return [SUBSI(31, 0, 0), BCOND(me + 4, me + 16, EQ), MOVZ(0, 1), RET, MOVZ(0, 2), RET]
def _loop(at, me): return [CBZ(0, me, me + 12), SUBI(0, 0, 1), B(me + 8, me), RET]
def _caller(at, me): return [STP_PRE(29, 30, 31, -16), BL(me + 4, at['arith']), BL(me + 8, at['branch']), LDP_POST(29, 30, 31, 16), RET]
def _indirect(at, me): return [STP_PRE(29, 30, 31, -16), BLR(1), LDP_POST(29, 30, 31, 16), BR(2)]
def _tls(at, me): return [MRS_TPIDR(0), MRS_TPIDRRO(3), MOV(1, 18), RET]
def _svc(at, me): return [SVC(1), RET]
FUNCTIONS = [('arith', True, _arith), ('branch', True, _branch), ('loop', True, _loop), ('caller', True, _caller),
             ('indirect', True, _indirect), ('tls', True, _tls), ('svc_setheap', False, _svc)]
START = 0x10


def text_layout():
    """(words of the text segment, {name: (start, end)}, {name: has FDE}): the text is assembled twice, the first time to learn the sizes the branches between functions need"""
    sizes = {name: len(fn({n: 0 for n, _, _ in FUNCTIONS}, 0)) * 4 for name, _, fn in FUNCTIONS}
    at, o = {}, START
    for name, _, _ in FUNCTIONS: at[name] = o; o += sizes[name]
    words = [B(0, START), 0, NOP, NOP]   # word 1 (MOD0 offset) is filled in by build()
    for name, _, fn in FUNCTIONS: words += fn(at, at[name])
    return words, {n: (at[n], at[n] + sizes[n]) for n, _, _ in FUNCTIONS}, {n: fde for n, fde, _ in FUNCTIONS}


def _uleb_pad(b, to): return b + bytes(to - len(b))


def eh_frame(fdes, ro_off, cie_at=0x40):
    """(.eh_frame, .eh_frame_hdr, offset of the hdr in ro) for the functions [(start, end)] of a module whose ro starts at ro_off and holds the .eh_frame at cie_at:
    a CIE (zR, code align 4, data align -8, return register x30, FDE pointers pc-relative sdata4) and one FDE per function, and the binary search table of the hdr"""
    eh = bytearray()
    eh += struct.pack('<IIB', 20, 0, 1) + b'zR\0' + bytes([4, 0x78, 30, 1, 0x1b, 0x0c, 31, 0]); eh = bytearray(_uleb_pad(bytes(eh), 24))
    table = []   # (function start, FDE offset) for the binary search table
    for s, e in sorted(fdes):
        fde = ro_off + cie_at + len(eh)
        pos = fde + 8   # where pc_begin is stored; the encoding is relative to it
        eh += _uleb_pad(struct.pack('<IIiiB', 20, (fde + 4) - (ro_off + cie_at), s - pos, e - s, 0), 24)
        table.append((s, fde))
    eh += bytes(4)   # terminator
    hdr_at = (cie_at + len(eh) + 7) & ~7
    ehs = ro_off + hdr_at
    hdr = struct.pack('<BBBBi', 1, 0x1b, 0x03, 0x3b, (ro_off + cie_at) - (ehs + 4)) + struct.pack('<I', len(table))   # version, eh_frame_ptr pcrel sdata4, count udata4, table datarel sdata4
    hdr += b''.join(struct.pack('<ii', s - ehs, f - ehs) for s, f in table)
    return bytes(eh), hdr, hdr_at


def build(words=None):
    """The fixture NSO file (bytes, uncompressed, segment hash flags cleared as a modified main has them) and {name: (start, end)}. words replaces the text words (a test corrupts one)."""
    text_words, ranges, has_fde = text_layout()
    text_words = list(words) if words is not None else text_words
    text = bytearray(struct.pack('<%dI' % len(text_words), *text_words))
    ro_off = (len(text) + PAGE - 1) & ~(PAGE - 1)
    mod0, cie_at = 0, 0x40   # ro: MOD0 at 0, the .eh_frame at 0x40
    eh, hdr, hdr_at = eh_frame([ranges[n] for n in sorted(ranges, key=lambda n: ranges[n]) if has_fde[n]], ro_off, cie_at)
    ro = bytearray(hdr_at + len(hdr)); ro[cie_at:cie_at + len(eh)] = eh; ro[hdr_at:] = hdr
    # MOD0: magic, dynamic (none: an empty one is at the end of ro), bss start/end, eh_frame_hdr start/end (relative to MOD0), library name offset
    dyn_at = (len(ro) + 15) & ~15; ro += bytes(dyn_at + 16 - len(ro))
    struct.pack_into('<4siiiiii', ro, 0, b'MOD0', dyn_at, 0, 0, hdr_at, hdr_at + len(hdr), 0)
    if not struct.unpack_from('<I', text, 4)[0]: struct.pack_into('<I', text, 4, ro_off + mod0)   # a text that brings its own MOD0 pointer keeps it
    data = bytes(8)
    data_off = (ro_off + len(ro) + PAGE - 1) & ~(PAGE - 1)
    hdr_nso = bytearray(0x100); hdr_nso[:4] = b'NSO0'; hdr_nso[0x40:0x60] = hashlib.sha256(b'nx2linux-arm64-fixture').digest()
    body, fo = b'', 0x100
    for i, (seg, mo) in enumerate(((bytes(text), 0), (bytes(ro), ro_off), (data, data_off))):
        struct.pack_into('<III', hdr_nso, 0x10 + 0x10 * i, fo + len(body), mo, len(seg)); struct.pack_into('<I', hdr_nso, 0x60 + 4 * i, len(seg))
        hdr_nso[0xa0 + 0x20 * i:0xc0 + 0x20 * i] = hashlib.sha256(seg).digest(); body += seg
    return bytes(hdr_nso + body), ranges
