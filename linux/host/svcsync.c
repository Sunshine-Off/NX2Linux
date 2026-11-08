/* Kernel synchronisation calls in C (NX2Linux Phase 3, issue 19), loaded by engine/dm/tools/horizon_sync.py into the process that runs the game.
 *
 * The recompiled game makes about 200 kernel calls per frame to lock, wait and wake (WaitProcessWideKeyAtomic, SignalProcessWideKey, ArbitrateLock, ArbitrateUnlock, WaitSynchronization). Each one
 * goes through a Python callback under the GIL. This library is where they will be answered without Python, behind the switch NX2LINUX_SVC_NATIVE.
 *
 * Step a1 (this file): no call is answered here yet. The library has what the next steps stand on and nothing else:
 *   - the access to guest memory (a peek and a 32-bit compare-exchange, given by the host at init: the AOT runtime's own in production, a fake in a test),
 *   - the record of a logical thread and the binding of the calling host thread to it. A logical thread is what Horizon calls a thread. A fiber runs on a host thread of its own but acts for the logical
 *     thread that resumed it, so the binding changes at every fiber switch (Horizon.bind in horizon_hle.py is the one place that does it). A host thread that was never bound acts for the main thread (tid 1),
 *     as horizon_hle.py does with `self.current() or self.main_thread_obj`,
 *   - nx2sync_svc, the function the AOT runtime calls first for an svc number this library handles (nx2sync_handles): 1 when it did the call, 0 when it did not and Python must.
 *
 * Records are never freed: a few dozen bytes per logical thread that was ever bound. */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t (*nx2sync_cas32_fn)(uint64_t addr, uint32_t expected, uint32_t desired);
typedef int (*nx2sync_peek_fn)(uint64_t addr, uint8_t *dst, uint64_t n);

typedef struct nx2sync_rec {
    uint32_t tid, handle;
    struct nx2sync_rec *next;   /* in the bucket */
} nx2sync_rec;

#define BUCKETS 256
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static nx2sync_rec *g_bucket[BUCKETS];
static nx2sync_rec *g_main;
static nx2sync_peek_fn g_peek;
static nx2sync_cas32_fn g_cas32;
static __thread nx2sync_rec *t_rec;

static nx2sync_rec *rec_of(uint32_t tid, uint32_t handle, int create) {   /* the caller holds g_mu */
    nx2sync_rec **b = &g_bucket[(tid * 2654435761u) % BUCKETS];
    for (nx2sync_rec *r = *b; r; r = r->next) if (r->tid == tid) { if (handle) r->handle = handle; return r; }
    if (!create) return NULL;
    nx2sync_rec *r = calloc(1, sizeof *r);
    if (!r) return NULL;
    r->tid = tid; r->handle = handle; r->next = *b; *b = r;
    return r;
}

/* The host gives the two ways into guest memory. 0, or 1 when one is missing or there is no memory for the main thread's record. Calling it again replaces them (the tests reuse the library). */
int nx2sync_init(nx2sync_peek_fn peek, nx2sync_cas32_fn cas32) {
    if (!peek || !cas32) return 1;
    pthread_mutex_lock(&g_mu);
    g_peek = peek; g_cas32 = cas32;
    if (!g_main) g_main = rec_of(1, 0, 1);
    int bad = !g_main;
    pthread_mutex_unlock(&g_mu);
    return bad;
}

/* The calling host thread acts for logical thread tid from now on (handle: its Horizon handle, 0 when not known). 0, or 1 when there is no memory. */
int nx2sync_bind(uint32_t tid, uint32_t handle) {
    pthread_mutex_lock(&g_mu);
    nx2sync_rec *r = rec_of(tid ? tid : 1, handle, 1);
    pthread_mutex_unlock(&g_mu);
    if (!r) return 1;
    t_rec = r;
    return 0;
}

/* The logical thread the calling host thread acts for: the one bound to it, else the main thread (1). */
uint32_t nx2sync_current(void) { return t_rec ? t_rec->tid : 1; }

/* 1 when this library answers svc number num. None yet (step a1). */
int nx2sync_handles(int num) { (void)num; return 0; }

/* The function the AOT runtime calls for an svc number nx2sync_handles accepted: 1 when the call is done (the registers hold the results), 0 when it is not, with nothing changed. */
int nx2sync_svc(int num, uint64_t *regs, uint64_t at, void *ctx) { (void)num; (void)regs; (void)at; (void)ctx; return 0; }

/* The number of the interface, for the host to check before it registers anything. */
int nx2sync_abi(void) { return 1; }
