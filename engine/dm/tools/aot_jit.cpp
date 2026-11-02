// The JIT fallback of the ahead-of-time recompiled code (NX2Linux T4), built on dynarmic (0BSD, the AArch64 JIT the Switch emulators use). It takes over when AOT code reaches an address that is no AOT function:
// an indirect call or jump (blr, br) to code the translator never saw, such as code the game generated or a function the survey did not find. It then
//   · loads the guest registers of the lifted code's State into a dynarmic Jit and runs the code found in guest memory from that address;
//   · ends the run when the code returns to the address the AOT caller left in x30 (that address is refused as code, which stops the run: x30 keeps its real value, so the return addresses JIT'd code
//     saves on the stack are the true ones, as an unwinder expects), and gives the registers back;
//   · hands control back to the AOT code when the JIT'd code branches to an AOT function's entry (that entry is refused as code, which stops the run), then resumes at x30 when it returns;
//   · drops the code it compiled from a page when the guest writes to that page (AOT stores, JIT stores, aot_poke): generated code that changes is recompiled;
//   · counts and logs every entry and every transition, and stops with an explicit error on: an entry budget, a cycle budget, a nesting depth, no code at an address, an access outside guest memory,
//     an instruction dynarmic cannot translate, an exception. An `svc` goes to the same Horizon service handler as in AOT code (aot_svc_handler), with the registers in and out;
//   · invalidates across threads: every Jit that compiled code from a page is told when ANY thread writes to it (or runs `ic ivau`/`ic ialluis`), halts, and recompiles (hypotheses.md H15).
// One Jit per host thread (dynarmic's Run is not reentrant); the exclusive monitor is shared by up to 64 of them. A callback never longjmps (dynarmic's state would be left mid-run): it records the
// fault, halts the Jit, and the error is raised once Run has returned.
#include "aot_runtime.h"
#include "dynarmic/interface/A64/a64.h"
#include "dynarmic/interface/A64/config.h"
#include "dynarmic/interface/exclusive_monitor.h"
#include <atomic>
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <vector>

using namespace Dynarmic;

namespace {
struct Config { uint64_t max_entries = 0, ticks = 1ull << 40; int max_depth = 8; } g_cfg;   // max_entries 0: unlimited
ExclusiveMonitor g_monitor(64);
std::atomic<size_t> g_next_cpu{0};
std::mutex g_log_mu;
struct LogRow { uint64_t count, first; const char *kind; };
std::map<std::pair<uint64_t, const char *>, LogRow> g_log;   // (target, kind) -> how often, and the sequence number of the first time
uint64_t g_seq;

enum Stop { NONE, RETURN, TO_AOT, FAULT };
constexpr uint64_t kSlice = 1 << 18;

struct Cpu;
// Which Jits compiled code from which 4 KiB page, so that a write by any thread reaches them all. g_filter counts the registered pages per hash bucket: a write to a page whose bucket is 0 (nearly every
// write) costs one atomic load and takes no lock.
std::mutex g_pages_mu;
std::map<uint64_t, std::set<Cpu *>> g_owners;
std::atomic<uint32_t> g_filter[4096];
inline unsigned bucket(uint64_t pg) { return (unsigned)((pg * 0x9E3779B97F4A7C15ull) >> 52); }

struct Cpu final : A64::UserCallbacks {
    std::unique_ptr<A64::Jit> jit;
    size_t id = 0;
    uint64_t tpidr = 0, tpidrro = 0, ticks_left = 0, stop_pc = 0, ret_addr = 0;   // ret_addr: where this run ends, the caller's x30
    Stop stop = NONE;
    char fault[300] = {0};
    std::set<uint64_t> code_pages;   // 4 KiB pages some compiled code was read from (this Cpu's own thread only)
    std::mutex pending_mu;
    std::vector<uint64_t> pending;   // pages other threads wrote to: this thread drops its compiled code from them (InvalidateCacheRange is not safe against its own Run)
    uint32_t svc_num = 0;

    explicit Cpu(size_t id_) : id(id_) {
        A64::UserConfig c;
        c.callbacks = this; c.global_monitor = &g_monitor; c.processor_id = id;
        c.tpidr_el0 = &tpidr; c.tpidrro_el0 = &tpidrro;
        c.cntfrq_el0 = 19200000;                 // the Switch's counter frequency
        c.check_halt_on_memory_access = true;    // a failed access halts at once, not at the end of the block
        c.code_cache_size = 16 << 20;
        jit = std::make_unique<A64::Jit>(c);
    }

    ~Cpu() {
        std::lock_guard<std::mutex> g(g_pages_mu);
        for (uint64_t pg : code_pages) unregister_page(pg);
    }
    void unregister_page(uint64_t pg) {   // g_pages_mu held
        auto it = g_owners.find(pg);
        if (it != g_owners.end() && it->second.erase(this)) { g_filter[bucket(pg)]--; if (it->second.empty()) g_owners.erase(it); }
    }

    void fail_with(const char *fmt, ...) __attribute__((format(printf, 2, 3))) {
        if (stop == FAULT) return;   // the first fault is the one that explains the rest
        va_list ap; va_start(ap, fmt); vsnprintf(fault, sizeof fault, fmt, ap); va_end(ap);
        stop = FAULT; jit->HaltExecution();
    }

    // ---- code and data ----
    std::optional<uint32_t> MemoryReadCode(A64::VAddr a) override {
        if (a == ret_addr || aot_find(a)) return std::nullopt;   // not code to compile: the run ends or goes back to the AOT code (ExceptionRaised)
        uint8_t *p = aot_guest_ptr(a, 4);
        if (!p) return std::nullopt;
        if (code_pages.insert(a >> 12).second) {
            aot_count(AOT_S_JIT_PAGES);
            std::lock_guard<std::mutex> g(g_pages_mu);
            if (g_owners[a >> 12].insert(this).second) g_filter[bucket(a >> 12)]++;
        }
        uint32_t v; memcpy(&v, p, 4); return v;
    }
    template <class T> T rd(A64::VAddr a) {
        uint8_t *p = aot_guest_ptr(a, sizeof(T));
        if (!p) { fail_with("JIT: read of %u bytes at %#llx is outside every mapped region", (unsigned)sizeof(T), (unsigned long long)a); return T{}; }
        T v; memcpy(&v, p, sizeof v); return v;
    }
    template <class T> void wr(A64::VAddr a, T v) {
        uint8_t *p = aot_guest_ptr(a, sizeof(T));
        if (!p) { fail_with("JIT: write of %u bytes at %#llx is outside every mapped region", (unsigned)sizeof(T), (unsigned long long)a); return; }
        memcpy(p, &v, sizeof v);
        written(this, a, sizeof(T));
    }
    // This thread's own compiled code from page pg is stale (in a callback InvalidateCacheRange halts the Jit; the run loop resumes it).
    void drop(uint64_t pg) {
        if (!code_pages.erase(pg)) return;
        jit->InvalidateCacheRange(pg << 12, 0x1000);
        aot_count(AOT_S_JIT_INVALIDATIONS);
    }
    // Other threads wrote to some pages: take their notes. Called by the owner thread, outside Run.
    void drain() {
        std::vector<uint64_t> pgs;
        { std::lock_guard<std::mutex> g(pending_mu); pgs.swap(pending); }
        for (uint64_t pg : pgs) {
            drop(pg);
            std::lock_guard<std::mutex> g(g_pages_mu);
            unregister_page(pg);
        }
    }
    // The bytes [a, a+n) changed (by this thread, whichever it is): every Jit with code compiled from those pages drops it.
    static void written(Cpu *self, uint64_t a, uint64_t n) {
        for (uint64_t pg = a >> 12; pg <= (a + n - 1) >> 12; pg++) {
            if (!g_filter[bucket(pg)].load(std::memory_order_relaxed)) continue;
            std::vector<Cpu *> others; bool mine = false;
            {
                std::lock_guard<std::mutex> g(g_pages_mu);
                auto it = g_owners.find(pg);
                if (it == g_owners.end()) continue;
                for (Cpu *c : it->second) {
                    if (c == self) { mine = true; continue; }
                    { std::lock_guard<std::mutex> g2(c->pending_mu); c->pending.push_back(pg); }
                    c->jit->HaltExecution(HaltReason::CacheInvalidation);   // callable from any thread: the other thread leaves Run, drains and recompiles
                }
                if (mine) self->unregister_page(pg);
            }
            if (mine) self->drop(pg);
        }
    }
    uint8_t MemoryRead8(A64::VAddr a) override { return rd<uint8_t>(a); }
    uint16_t MemoryRead16(A64::VAddr a) override { return rd<uint16_t>(a); }
    uint32_t MemoryRead32(A64::VAddr a) override { return rd<uint32_t>(a); }
    uint64_t MemoryRead64(A64::VAddr a) override { return rd<uint64_t>(a); }
    A64::Vector MemoryRead128(A64::VAddr a) override { return rd<A64::Vector>(a); }
    void MemoryWrite8(A64::VAddr a, uint8_t v) override { wr(a, v); }
    void MemoryWrite16(A64::VAddr a, uint16_t v) override { wr(a, v); }
    void MemoryWrite32(A64::VAddr a, uint32_t v) override { wr(a, v); }
    void MemoryWrite64(A64::VAddr a, uint64_t v) override { wr(a, v); }
    void MemoryWrite128(A64::VAddr a, A64::Vector v) override { wr(a, v); }
    // An exclusive store is a compare-and-swap against the value the exclusive load read, as in the AOT code; true when it wrote.
    template <class T> bool cas(A64::VAddr a, T v, T expected) {
        if (a % sizeof(T)) { fail_with("JIT: exclusive access of %u bytes at %#llx is not naturally aligned", (unsigned)sizeof(T), (unsigned long long)a); return false; }
        uint8_t *p = aot_guest_ptr(a, sizeof(T));
        if (!p) { fail_with("JIT: exclusive write of %u bytes at %#llx is outside every mapped region", (unsigned)sizeof(T), (unsigned long long)a); return false; }
        bool ok = __atomic_compare_exchange_n((T *)p, &expected, v, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
        if (ok) written(this, a, sizeof(T));
        return ok;
    }
    bool MemoryWriteExclusive8(A64::VAddr a, uint8_t v, uint8_t e) override { return cas(a, v, e); }
    bool MemoryWriteExclusive16(A64::VAddr a, uint16_t v, uint16_t e) override { return cas(a, v, e); }
    bool MemoryWriteExclusive32(A64::VAddr a, uint32_t v, uint32_t e) override { return cas(a, v, e); }
    bool MemoryWriteExclusive64(A64::VAddr a, uint64_t v, uint64_t e) override { return cas(a, v, e); }
    bool MemoryWriteExclusive128(A64::VAddr a, A64::Vector, A64::Vector) override { fail_with("JIT: 128-bit exclusive access at %#llx is not supported", (unsigned long long)a); return false; }

    // ---- events ----
    void InterpreterFallback(A64::VAddr pc, size_t n) override { fail_with("JIT: %zu instruction(s) at %#llx are not translated by dynarmic, which has no interpreter here", n, (unsigned long long)pc); }
    // An svc ends the block (dynarmic's CheckHalt terminal): the run loop answers it outside Run, where the registers can be read and written safely, as emulators do
    void CallSVC(uint32_t swi) override { svc_num = swi; jit->HaltExecution(HaltReason::UserDefined2); }
    void InstructionCacheOperationRaised(A64::InstructionCacheOperation op, A64::VAddr value) override {
        if (op == A64::InstructionCacheOperation::InvalidateByVAToPoU) { written(this, value & ~63ull, 64); return; }
        std::vector<uint64_t> all;   // ic ialluis / iallu: every page any Jit compiled from
        { std::lock_guard<std::mutex> g(g_pages_mu); for (auto &[pg, _] : g_owners) all.push_back(pg); }
        for (uint64_t pg : all) written(this, pg << 12, 1);
    }
    void ExceptionRaised(A64::VAddr pc, A64::Exception e) override {
        if (stop == FAULT) { jit->HaltExecution(); return; }   // a fault already recorded (a failed access) is what to report, not the return that follows it
        if (e == A64::Exception::NoExecuteFault) {
            if (pc == ret_addr) { stop = RETURN; stop_pc = pc; }
            else if (aot_find(pc)) { stop = TO_AOT; stop_pc = pc; }
            else fail_with("JIT: no code to run at %#llx (outside every mapped region)", (unsigned long long)pc);
        } else {
            static const char *names[] = {"unallocated encoding", "reserved value", "unpredictable instruction", "wait for interrupt", "wait for event", "send event", "send event (local)", "yield", "breakpoint", "no-execute fault"};
            fail_with("JIT: %s at %#llx", (unsigned)e < 10 ? names[(unsigned)e] : "exception", (unsigned long long)pc);
        }
        jit->HaltExecution();
    }
    void AddTicks(uint64_t t) override { ticks_left = t >= ticks_left ? 0 : ticks_left - t; }
    // Run is given at most kSlice cycles at a time: dynarmic checks the halt flag between linked blocks only when its cycle counter runs out, so a tight loop (`b .`) would never see a halt that another thread
    // requested (a rewrite of its code). The run loop treats the end of a slice as a chance to look at what other threads asked for.
    uint64_t GetTicksRemaining() override { return ticks_left < kSlice ? ticks_left : kSlice; }
    uint64_t GetCNTPCT() override {
        using namespace std::chrono;
        return (uint64_t)((double)duration_cast<nanoseconds>(steady_clock::now().time_since_epoch()).count() * 0.0192);   // 19.2 MHz
    }
};

thread_local std::unique_ptr<Cpu> t_cpu;
thread_local int t_depth;

Cpu &cpu() {
    if (!t_cpu) {
        size_t id = g_next_cpu.fetch_add(1);
        if (id >= 64) { fprintf(stderr, "[RED] aot: more than 64 threads used the JIT\n"); abort(); }
        t_cpu = std::make_unique<Cpu>(id);
    }
    return *t_cpu;
}

#define XREGS(X) X(0) X(1) X(2) X(3) X(4) X(5) X(6) X(7) X(8) X(9) X(10) X(11) X(12) X(13) X(14) X(15) X(16) X(17) X(18) X(19) X(20) X(21) X(22) X(23) X(24) X(25) X(26) X(27) X(28) X(29) X(30)

void load(Cpu &c, const State *st) {   // State -> Jit
#define X(i) c.jit->SetRegister(i, st->gpr.x##i.qword);
    XREGS(X)
#undef X
    c.jit->SetSP(st->gpr.sp.qword);
    c.jit->SetPstate((uint32_t)((st->sr.n << 31) | (st->sr.z << 30) | (st->sr.c << 29) | (st->sr.v << 28)));
    for (int i = 0; i < 32; i++) { A64::Vector v; memcpy(&v, &st->simd.v[i], 16); c.jit->SetVector(i, v); }
    c.jit->SetFpcr((uint32_t)st->fpcr.flat); c.jit->SetFpsr(aot_fpsr_get(*st));
    c.tpidr = st->sr.tpidr_el0.qword; c.tpidrro = st->sr.tpidrro_el0.qword;
}

void store(Cpu &c, State *st) {   // Jit -> State
#define X(i) st->gpr.x##i.qword = c.jit->GetRegister(i);
    XREGS(X)
#undef X
    st->gpr.sp.qword = c.jit->GetSP();
    uint32_t ps = c.jit->GetPstate();
    st->sr.n = ps >> 31 & 1; st->sr.z = ps >> 30 & 1; st->sr.c = ps >> 29 & 1; st->sr.v = ps >> 28 & 1;
    for (int i = 0; i < 32; i++) { A64::Vector v = c.jit->GetVector(i); memcpy(&st->simd.v[i], &v, 16); }
    st->fpcr.flat = c.jit->GetFpcr(); aot_fpsr_set(*st, c.jit->GetFpsr());
    st->sr.tpidr_el0.qword = c.tpidr;
}

void log_entry(uint64_t target, const char *kind) {
    std::lock_guard<std::mutex> g(g_log_mu);
    auto &row = g_log[{target, kind}];
    if (!row.count++) { row.first = ++g_seq; row.kind = kind; }
}

// Everything but the final raise lives here, in a frame with no destructor a longjmp could skip. Returns 0, or 1 with err.
int run_jit(State *st, uint64_t pc, Memory *m, int is_jump, char *err, size_t errn) {
    Cpu &c = cpu();
    const uint64_t outer_ret = c.ret_addr;   // this Cpu may be suspended in an outer run, waiting for an AOT function that entered the JIT again
    c.ret_addr = st->gpr.x30.qword;
    c.drain();
    load(c, st);
    c.jit->SetPC(pc);
    c.ticks_left = g_cfg.ticks; c.stop = NONE; c.fault[0] = 0;
    int rc = 0;
    for (;;) {
        HaltReason hr = c.jit->Run();
        if (c.stop == FAULT) { snprintf(err, errn, "%s", c.fault); rc = 1; break; }
        if (c.stop == RETURN) break;
        if (c.stop == TO_AOT) {
            const uint64_t target = c.stop_pc;
            store(c, st);
            aot_count(AOT_S_JIT_TO_AOT); log_entry(target, "jit->aot");
            c.stop = NONE;
            if (aot_try_call(aot_find(target), st, target, m, err, errn)) { rc = 1; break; }
            load(c, st);
            c.jit->SetPC(st->gpr.x30.qword);   // the AOT function returned: carry on after the call, where x30 says
            continue;
        }
        if (Dynarmic::Has(hr, HaltReason::UserDefined2)) {   // svc: the number was recorded by CallSVC, the registers are the guest's
            c.jit->ClearHalt(HaltReason::UserDefined2);
            if (!aot_svc_handler) { snprintf(err, errn, "svc #%u at %#llx: no Horizon service handler is set", c.svc_num, (unsigned long long)c.jit->GetPC() - 4); rc = 1; break; }
            aot_count(AOT_S_SVC);
            uint64_t regs[31];
            for (int i = 0; i < 31; i++) regs[i] = c.jit->GetRegister(i);
            aot_svc_handler((int)c.svc_num, regs, c.jit->GetPC() - 4);
            for (int i = 0; i < 31; i++) c.jit->SetRegister(i, regs[i]);
            continue;
        }
        if (Dynarmic::Has(hr, HaltReason::CacheInvalidation)) { c.jit->ClearHalt(HaltReason::CacheInvalidation); c.drain(); continue; }   // code this Jit compiled changed (its own stores, or another thread's): recompile   // the JIT'd code wrote to code it had compiled: go on, recompiling
        if (hr == HaltReason{} && c.ticks_left) { c.drain(); continue; }   // the end of a slice (see GetTicksRemaining)
        if (c.ticks_left == 0) { snprintf(err, errn, "JIT budget exceeded: the code at %#llx ran more than %llu cycles without returning", (unsigned long long)pc, (unsigned long long)g_cfg.ticks); rc = 1; break; }
        snprintf(err, errn, "JIT stopped for an unknown reason (halt %#x) at %#llx", (unsigned)hr, (unsigned long long)c.jit->GetPC()); rc = 1; break;
    }
    store(c, st);
    c.ret_addr = outer_ret;
    (void)is_jump;
    return rc;
}

Memory *fallback(State *st, uint64_t pc, Memory *m, int is_jump) {
    if (g_cfg.max_entries && aot_stat(AOT_S_JIT_ENTRIES) >= g_cfg.max_entries)
        aot_fail("JIT budget exceeded: %llu entries already, and %#llx would be one more", (unsigned long long)g_cfg.max_entries, (unsigned long long)pc);
    if (t_depth >= g_cfg.max_depth) aot_fail("JIT entered %d levels deep (%#llx): AOT and JIT code keep calling each other", t_depth, (unsigned long long)pc);
    aot_count(AOT_S_JIT_ENTRIES); log_entry(pc, is_jump ? "jump" : "call");
    char err[300];
    t_depth++;
    int rc = run_jit(st, pc, m, is_jump, err, sizeof err);
    t_depth--;
    if (rc) aot_fail("%s", err);
    return m;
}

void on_write(uint64_t a, uint64_t n) { Cpu::written(t_cpu.get(), a, n); }
}  // namespace

extern "C" {
// Turn the fallback on: at most max_entries entries (0: no limit), ticks cycles per entry, max_depth nested entries
void aot_jit_enable(uint64_t max_entries, uint64_t ticks, int max_depth) {
    g_cfg.max_entries = max_entries; g_cfg.ticks = ticks ? ticks : 1ull << 40; g_cfg.max_depth = max_depth > 0 ? max_depth : 8;
    aot_fallback = fallback; aot_write_hook = on_write;
}
void aot_jit_disable() { aot_fallback = nullptr; aot_write_hook = nullptr; }
// The log, one line per (target, kind): "#seq pc=0x... kind count=N"
int aot_jit_log(char *buf, uint64_t n) {
    std::lock_guard<std::mutex> g(g_log_mu);
    std::string out;
    std::map<uint64_t, std::string> by_seq;
    for (auto &[k, row] : g_log) { char line[100]; snprintf(line, sizeof line, "#%llu pc=%#llx %s count=%llu\n", (unsigned long long)row.first, (unsigned long long)k.first, row.kind, (unsigned long long)row.count); by_seq[row.first] = line; }
    for (auto &[_, l] : by_seq) out += l;
    snprintf(buf, n, "%s", out.c_str());
    return (int)out.size();
}
void aot_jit_log_clear() { std::lock_guard<std::mutex> g(g_log_mu); g_log.clear(); g_seq = 0; }
}
