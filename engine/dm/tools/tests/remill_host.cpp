// Host side of a function Remill lifted from the fixture (NX2Linux T2): the lifted code takes a guest CPU state, a program counter and a memory token, and calls `__remill_*` intrinsics the
// host defines. Compiled together with the lifted IR (test_recompile_arith.py) into one x86_64 shared library. ENTRY is the lifted function's name (sub_<address>).
// The flag intrinsics only mark where a flag is computed for Remill's own analyses: the value passed first is already the flag, so they return it.
#include "remill/Arch/AArch64/Runtime/State.h"
#include <cstdint>
#include <cstring>
extern "C" {
Memory *ENTRY(State *, uint64_t, Memory *);
bool __remill_flag_computation_sign(bool r, ...) { return r; }
bool __remill_flag_computation_zero(bool r, ...) { return r; }
bool __remill_flag_computation_carry(bool r, ...) { return r; }
bool __remill_flag_computation_overflow(bool r, ...) { return r; }
// the condition markers (ccmp, csel, b.cond...): likewise the condition is already a bool, the intrinsic only labels which comparison it stands for
#define COMPARE(n) bool __remill_compare_##n(bool r) { return r; }
COMPARE(eq) COMPARE(neq) COMPARE(slt) COMPARE(sle) COMPARE(sgt) COMPARE(sge) COMPARE(ult) COMPARE(ule) COMPARE(ugt) COMPARE(uge)
Memory *__remill_function_return(State &, uint64_t, Memory *m) { return m; }   // `ret`: the run ends here
// Every other intrinsic (memory access, indirect jumps, svc...) is absent on purpose: the library then fails to link instead of translating something it cannot do

// run `arith`: x0, x1 in; x0 and NZCV (bits 3..0) out
uint64_t run_lifted(uint64_t x0, uint64_t x1, uint8_t *nzcv, uint64_t pc) {
    State st; memset(&st, 0, sizeof st);
    st.gpr.x0.qword = x0; st.gpr.x1.qword = x1;
    ENTRY(&st, pc, nullptr);
    *nzcv = st.sr.n << 3 | st.sr.z << 2 | st.sr.c << 1 | st.sr.v;
    return st.gpr.x0.qword;
}

// run a lifted instruction sequence from the given x0..x3 and NZCV (bits 3..0); x0..x3 and the NZCV come back
void run_state(uint64_t *x, uint8_t nzcv_in, uint8_t *nzcv_out, uint64_t pc) {
    State st; memset(&st, 0, sizeof st);
    st.gpr.x0.qword = x[0]; st.gpr.x1.qword = x[1]; st.gpr.x2.qword = x[2]; st.gpr.x3.qword = x[3];
    st.sr.n = nzcv_in >> 3 & 1; st.sr.z = nzcv_in >> 2 & 1; st.sr.c = nzcv_in >> 1 & 1; st.sr.v = nzcv_in & 1;
    ENTRY(&st, pc, nullptr);
    x[0] = st.gpr.x0.qword; x[1] = st.gpr.x1.qword; x[2] = st.gpr.x2.qword; x[3] = st.gpr.x3.qword;
    *nzcv_out = st.sr.n << 3 | st.sr.z << 2 | st.sr.c << 1 | st.sr.v;
}
}
