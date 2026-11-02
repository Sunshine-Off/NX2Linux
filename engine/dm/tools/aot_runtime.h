// What aot_runtime.cpp shares with aot_jit.cpp (the dynarmic fallback): guest memory access, the AOT function table, the error path and the counters.
#pragma once
#include "remill/Arch/AArch64/Runtime/State.h"
#include <cstdint>

typedef Memory *(*AotFn)(State *, uint64_t, Memory *);
// FPSR: the cumulative exception flags live in State.sr (where the lifted floating-point code sets them), the rest of the register in State.fpsr
static inline uint32_t aot_fpsr_get(const State &st) {
    return (uint32_t)(st.fpsr.flat & ~0x9Full) | st.sr.ioc | st.sr.dzc << 1 | st.sr.ofc << 2 | st.sr.ufc << 3 | st.sr.ixc << 4 | st.sr.idc << 7;
}
static inline void aot_fpsr_set(State &st, uint32_t v) {
    st.fpsr.flat = v & ~0x9Fu; st.sr.ioc = v & 1; st.sr.dzc = v >> 1 & 1; st.sr.ofc = v >> 2 & 1; st.sr.ufc = v >> 3 & 1; st.sr.ixc = v >> 4 & 1; st.sr.idc = v >> 7 & 1;
}
// Statistics, in the order aot_lift.py names them (STATS)
enum { AOT_S_CALLS, AOT_S_JUMPS, AOT_S_FALLBACK, AOT_S_READS, AOT_S_WRITES, AOT_S_REGIONS,
       AOT_S_JIT_ENTRIES, AOT_S_JIT_TO_AOT, AOT_S_JIT_INVALIDATIONS, AOT_S_JIT_PAGES, AOT_S_UNLIFTED, AOT_S_SVC, AOT_S_IMPORTS, AOT_S_N };

extern "C" {
AotFn aot_find(uint64_t pc);                                  // the AOT function whose entry is pc, or null
uint8_t *aot_guest_ptr(uint64_t a, uint64_t n);               // host pointer to n guest bytes at a, or null when they are not all inside one mapped region
[[noreturn]] void aot_fail(const char *fmt, ...) __attribute__((format(printf, 1, 2)));   // leave the lifted code with an error (aot_run reports it)
void aot_count(int stat);
void aot_set_tls(uint64_t tpidr_el0, uint64_t tpidrro_el0);   // change the thread pointers of the run this thread executes (a fiber resumed by another guest thread)
uint64_t aot_get_tls(int which);   // 0: tpidr_el0, 1: tpidrro_el0
void aot_request_exit(void);   // the current guest thread ends when its service handler returns (svcExitThread)
uint64_t aot_stat(int stat);
// Run an AOT function and catch its error: 0 when it returned, else 1 with the reason in err (the caller then cleans up and fails in its turn)
int aot_try_call(AotFn f, State *st, uint64_t pc, Memory *m, char *err, uint64_t errn);
// The JIT fallback: when an indirect call or jump targets no AOT function, aot_fallback (if set) runs it. is_jump: a `br` (a tail call) rather than a `blr`.
typedef Memory *(*AotFallback)(State *, uint64_t pc, Memory *, int is_jump);
extern AotFallback aot_fallback;
// The Horizon service handler, with the signature of svc_dispatch (hle_svc.c): svc number, x0..x30 in regs (the handler writes its results back there, x0 first), the address of the svc instruction.
// Its return value is not used: a service answers in the registers, as on the console. Without a handler an svc is an explicit error.
typedef uint64_t (*AotSvcHandler)(int num, uint64_t *regs, uint64_t orig_addr);
extern AotSvcHandler aot_svc_handler;
// Imports of a loaded module (elf_module.py): an address in guest space that is no code but a host function, as the GOT of a module points at the HLE functions of the real loader. Calling it
// (blr, br, or a call from JIT code) runs fn(regs, ctx) with x0..x30 (it writes its answers back there); fn null: the import is unresolved and calling it is an explicit error naming it.
typedef void (*AotImportFn)(uint64_t *regs, void *ctx);
int aot_register_import(uint64_t addr, AotImportFn fn, void *ctx, const char *name);   // 0, or 1 when addr is taken or the table is full
// Called after any write to guest memory by AOT code or the harness, so that the JIT can drop the code it compiled from those bytes
typedef void (*AotWriteHook)(uint64_t addr, uint64_t n);
extern AotWriteHook aot_write_hook;
}
