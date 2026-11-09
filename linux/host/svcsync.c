/* Kernel synchronisation calls in C (NX2Linux Phase 3, issue 19), loaded by engine/dm/tools/horizon_sync.py into the process that runs the game.
 *
 * The recompiled game makes about 200 kernel calls per frame to lock, wait and wake. Each one used to go through a Python callback under the GIL. The four calls on mutex words and condition keys are
 * answered here, the same way horizon_hle.py answers them (ArbitrateLock, ArbitrateUnlock, WaitProcessWideKeyAtomic, SignalProcessWideKey: read the Python first, the comments there say why each step is what it is).
 * They share their waiter queues, so they are one group: all four are here or none is (horizon_sync.py decides once, when the run starts). WaitSynchronization and the events are not here (step a3).
 *
 * What it keeps in step with the Python path:
 *   - the mutex word is only ever changed by compare-exchange (a plain write could undo a change the guest makes at the same time: patch 0084),
 *   - a thread that waits is woken by its own condition variable, so a signal wakes the threads concerned and not every waiter,
 *   - one kernel lock guards the queues and the per-thread state, as KernelLock does. It is never held while calling out of this file, except the two functions the host gave for guest memory (they never block),
 *   - a call is counted, and the last call of a thread (number, address of the svc, x29, x30, time) and its last 16 calls are kept for the hang report, as Horizon.svc does.
 *
 * A logical thread is what Horizon calls a thread. A fiber runs on a host thread of its own but acts for the logical thread that resumed it, so the binding of a host thread to a logical thread changes at every
 * fiber switch (Horizon.bind in horizon_hle.py is the one place that does it). A host thread that was never bound acts for the main thread (tid 1), as horizon_hle.py does with `self.current() or self.main_thread_obj`.
 * Records of logical threads are never freed: a few hundred bytes per thread that was ever bound.
 *
 * nx2sync_svc is what the AOT runtime calls first for an svc number nx2sync_handles accepts: 1 when the call is done (the registers hold the results), 0 when it is not (this library is not initialised) and Python must. */
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef uint32_t (*nx2sync_cas32_fn)(uint64_t addr, uint32_t expected, uint32_t desired);
typedef int (*nx2sync_peek_fn)(uint64_t addr, uint8_t *dst, uint64_t n);

#define SVC_ARBITRATE_LOCK 0x1a
#define SVC_ARBITRATE_UNLOCK 0x1b
#define SVC_WAIT_KEY 0x1c
#define SVC_SIGNAL_KEY 0x1d
#define LISTENERS 0x40000000u        /* a Horizon mutex word: the owner's handle, plus this bit while threads wait for it */
#define R_INVALID_ADDRESS 0xCC01u    /* rc(1, 102) */
#define R_TIMEOUT 0xEA01u            /* rc(1, 117) */
#define RECENT 16
#define SPIN_LIMIT 1000000           /* a compare-exchange loop on a word that cannot be read: give up instead of spinning forever */

struct nx2sync_rec;
struct queue;
typedef struct node { struct node *prev, *next; struct queue *q; struct nx2sync_rec *rec; } node;
typedef struct queue { uint64_t addr; node *head, *tail; struct queue *hnext, **bucket; } queue;

typedef struct { uint32_t num; uint32_t pad; uint64_t at, x0, x1; double t; } nx2sync_call;
typedef struct { uint32_t num; uint32_t pad; uint64_t at, fp, lr; double t; } nx2sync_last_t;

typedef struct nx2sync_rec {
    uint32_t tid, handle;
    struct nx2sync_rec *next;     /* in the bucket */
    pthread_cond_t cv;            /* the thread waits here, on g_k */
    int granted, signaled;        /* a mutex was handed to it, a key was signalled */
    uint32_t tag;                 /* the handle it asked the mutex with */
    node mn, kn;                  /* its place in the queue of the mutex it waits for, in the queue of the key it waits for */
    nx2sync_last_t last; int has_last;
    nx2sync_call recent[RECENT]; uint64_t nrecent;
} nx2sync_rec;

#define BUCKETS 256
static pthread_mutex_t g_k = PTHREAD_MUTEX_INITIALIZER;   /* the kernel lock: the records, the queues, the per-thread state */
static nx2sync_rec *g_bucket[BUCKETS];
static nx2sync_rec *g_main;
static nx2sync_peek_fn g_peek;
static nx2sync_cas32_fn g_cas32;
static queue *g_mq[BUCKETS], *g_kq[BUCKETS];              /* the queues of the mutex words and of the keys, by address */
static _Atomic uint64_t g_count[256], g_invalid;
/* What each call did, counted as Horizon.detail does in horizon_hle.py (the names are in DETAIL there, in this order) */
enum { D_LOCK_QUEUED, D_LOCK_AT_ONCE, D_UNLOCK_HANDED, D_UNLOCK_NONE, D_WAIT_RELEASE_HANDED, D_WAIT_SIGNALLED, D_WAIT_TIMEOUT, D_RELOCK_QUEUED, D_SIGNAL_WOKE, D_SIGNAL_NOBODY, D_N };
static _Atomic uint64_t g_detail[D_N];
static void detail(int i, uint64_t n) { atomic_fetch_add_explicit(&g_detail[i], n, memory_order_relaxed); }
static __thread nx2sync_rec *t_rec;

static int64_t now_ns(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return (int64_t)ts.tv_sec * 1000000000ll + ts.tv_nsec; }

/* ---- records ---- */
static nx2sync_rec *rec_of(uint32_t tid, uint32_t handle, int create) {   /* the caller holds g_k */
    nx2sync_rec **b = &g_bucket[(tid * 2654435761u) % BUCKETS];
    for (nx2sync_rec *r = *b; r; r = r->next) if (r->tid == tid) { if (handle) r->handle = handle; return r; }
    if (!create) return NULL;
    nx2sync_rec *r = calloc(1, sizeof *r);
    if (!r) return NULL;
    pthread_condattr_t ca; pthread_condattr_init(&ca); pthread_condattr_setclock(&ca, CLOCK_MONOTONIC);
    int bad = pthread_cond_init(&r->cv, &ca); pthread_condattr_destroy(&ca);
    if (bad) { free(r); return NULL; }
    r->tid = tid; r->handle = handle; r->mn.rec = r->kn.rec = r;
    r->next = *b; *b = r;
    return r;
}

/* ---- queues: FIFO by address, freed when they empty ---- */
static queue **bucket_of(queue **tab, uint64_t a) { return &tab[((a >> 2) * 0x9E3779B97F4A7C15ull) >> 56]; }

static queue *q_find(queue **tab, uint64_t addr, int create) {
    queue **b = bucket_of(tab, addr);
    for (queue *q = *b; q; q = q->hnext) if (q->addr == addr) return q;
    if (!create) return NULL;
    queue *q = calloc(1, sizeof *q);
    if (!q) return NULL;
    q->addr = addr; q->bucket = b; q->hnext = *b; *b = q;
    return q;
}

static void n_remove(node *n) {
    queue *q = n->q;
    if (!q) return;
    if (n->prev) n->prev->next = n->next; else q->head = n->next;
    if (n->next) n->next->prev = n->prev; else q->tail = n->prev;
    n->q = NULL; n->prev = n->next = NULL;
    if (!q->head) {
        for (queue **p = q->bucket; *p; p = &(*p)->hnext) if (*p == q) { *p = q->hnext; break; }
        free(q);
    }
}

static int n_push(queue **tab, uint64_t addr, node *n) {   /* 0, or 1 when there is no memory */
    queue *q = q_find(tab, addr, 1);
    if (!q) return 1;
    n->q = q; n->next = NULL; n->prev = q->tail;
    if (q->tail) q->tail->next = n; else q->head = n;
    q->tail = n;
    return 0;
}

/* ---- guest memory ---- */
static int rd32(uint64_t a, uint32_t *v) { uint8_t b[4]; if (g_peek(a, b, 4)) return 1; memcpy(v, b, 4); return 0; }

/* the word changes by compare-exchange only: 0, or 1 when it cannot be read */
static int cas_to(uint64_t addr, uint32_t new_value) {
    for (int i = 0; i < SPIN_LIMIT; i++) {
        uint32_t v;
        if (rd32(addr, &v)) return 1;
        if (g_cas32(addr, v, new_value) == v) return 0;
    }
    return 1;
}

static void park(nx2sync_rec *me, int64_t deadline) {   /* wait on the thread's own condition, g_k is released meanwhile; the caller checks its condition again */
    if (deadline < 0) pthread_cond_wait(&me->cv, &g_k);
    else { struct timespec ts = {(time_t)(deadline / 1000000000ll), (long)(deadline % 1000000000ll)}; pthread_cond_timedwait(&me->cv, &g_k, &ts); }
}

/* ArbitrateUnlock: hand the mutex word to the first waiter (keeping the listeners bit while others wait), or clear it. 0, or 1 when the word cannot be read */
static int unlock_mutex(uint64_t addr, int *handed) {
    queue *q = q_find(g_mq, addr, 0);
    nx2sync_rec *t = NULL; int more = 0;
    if (q) { node *n = q->head; t = n->rec; more = n->next != NULL; n_remove(n); }
    uint32_t nv = t ? (t->tag | (more ? LISTENERS : 0)) : 0;
    *handed = t != NULL;
    if (cas_to(addr, nv)) return 1;
    if (t) { t->granted = 1; pthread_cond_signal(&t->cv); }
    return 0;
}

/* Take the mutex word, or wait until an unlock hands it to this thread. 0, or 1 when the word cannot be read */
static int lock_mutex(nx2sync_rec *me, uint64_t addr, uint32_t tag) {
    for (int i = 0; i < SPIN_LIMIT; i++) {
        uint32_t v;
        if (rd32(addr, &v)) return 1;
        if (v == 0) { if (g_cas32(addr, 0, tag) == 0) return 0; continue; }
        if (!(v & LISTENERS)) { if (g_cas32(addr, v, v | LISTENERS) != v) continue; }
        me->granted = 0; me->tag = tag;
        if (n_push(g_mq, addr, &me->mn)) return 1;
        detail(D_RELOCK_QUEUED, 1);
        while (!me->granted) park(me, -1);
        return 0;
    }
    return 1;
}

/* ---- the calls ---- */
static void note(nx2sync_rec *me, int num, const uint64_t *regs, uint64_t at, double t) {   /* g_k held */
    me->last.num = (uint32_t)num; me->last.at = at; me->last.fp = regs[29]; me->last.lr = regs[30]; me->last.t = t; me->has_last = 1;
    nx2sync_call *c = &me->recent[me->nrecent++ % RECENT];
    c->num = (uint32_t)num; c->at = at; c->x0 = regs[0]; c->x1 = regs[1]; c->t = t;
}

static uint64_t do_arbitrate_lock(nx2sync_rec *me, uint64_t *r) {   /* (owner thread handle, mutex addr, requesting thread's tag) */
    uint32_t owner = (uint32_t)r[0], tag = (uint32_t)r[2], v; uint64_t addr = r[1];
    if (rd32(addr, &v)) return R_INVALID_ADDRESS;
    if (v != (owner | LISTENERS)) { detail(D_LOCK_AT_ONCE, 1); return 0; }   /* the word changed under the caller: nothing to wait for */
    me->granted = 0; me->tag = tag;
    if (n_push(g_mq, addr, &me->mn)) return R_INVALID_ADDRESS;
    detail(D_LOCK_QUEUED, 1);
    while (!me->granted) park(me, -1);
    return 0;
}

static uint64_t do_wait_key(nx2sync_rec *me, uint64_t *r) {   /* (mutex addr, key addr, tag, timeout): release the mutex, wait for the key, take the mutex again */
    uint64_t mutex = r[0], key = r[1], timeout = r[3]; uint32_t tag = (uint32_t)r[2];
    int64_t deadline = (timeout >= (1ull << 62)) ? -1 : now_ns() + (int64_t)timeout;   /* 2^62 ns is 146 years: the same as for ever */
    int handed = 0;
    if (unlock_mutex(mutex, &handed)) return R_INVALID_ADDRESS;
    if (handed) detail(D_WAIT_RELEASE_HANDED, 1);
    me->signaled = 0;
    if (n_push(g_kq, key, &me->kn)) return R_INVALID_ADDRESS;
    uint64_t result = 0;
    while (!me->signaled) {
        if (deadline >= 0 && now_ns() >= deadline) { n_remove(&me->kn); result = R_TIMEOUT; detail(D_WAIT_TIMEOUT, 1); break; }
        park(me, deadline);
    }
    if (result == 0) detail(D_WAIT_SIGNALLED, 1);
    if (lock_mutex(me, mutex, tag)) return R_INVALID_ADDRESS;
    return result;
}

static uint64_t do_signal_key(uint64_t *r) {   /* (key addr, count): wake that many waiters, all when count <= 0 */
    uint64_t key = r[0]; uint32_t count = (uint32_t)r[1];
    if (count >= 0x80000000u) count = 0;
    queue *q = q_find(g_kq, key, 0);
    uint32_t woken = 0;
    for (; q && q->head && (count == 0 || woken < count); woken++) {
        nx2sync_rec *t = q->head->rec;
        node *n = q->head;
        int last = n->next == NULL;
        n_remove(n);   /* frees the queue with its last waiter */
        t->signaled = 1; pthread_cond_signal(&t->cv);
        if (last) { woken++; break; }
    }
    if (woken) detail(D_SIGNAL_WOKE, woken); else detail(D_SIGNAL_NOBODY, 1);
    return 0;
}

/* ---- the interface ---- */

/* The host gives the two ways into guest memory. 0, or 1 when one is missing or there is no memory for the main thread's record. Calling it again replaces them (the tests reuse the library). */
int nx2sync_init(nx2sync_peek_fn peek, nx2sync_cas32_fn cas32) {
    if (!peek || !cas32) return 1;
    pthread_mutex_lock(&g_k);
    g_peek = peek; g_cas32 = cas32;
    if (!g_main) g_main = rec_of(1, 0, 1);
    int bad = !g_main;
    pthread_mutex_unlock(&g_k);
    return bad;
}

/* The calling host thread acts for logical thread tid from now on (handle: its Horizon handle, 0 when not known). 0, or 1 when there is no memory. */
int nx2sync_bind(uint32_t tid, uint32_t handle) {
    pthread_mutex_lock(&g_k);
    nx2sync_rec *r = rec_of(tid ? tid : 1, handle, 1);
    pthread_mutex_unlock(&g_k);
    if (!r) return 1;
    t_rec = r;
    return 0;
}

/* The logical thread the calling host thread acts for: the one bound to it, else the main thread (1). */
uint32_t nx2sync_current(void) { return t_rec ? t_rec->tid : 1; }

/* 1 when this library answers svc number num: the four calls on mutex words and keys, together. */
int nx2sync_handles(int num) { return num >= SVC_ARBITRATE_LOCK && num <= SVC_SIGNAL_KEY; }

/* The function the AOT runtime calls for an svc number nx2sync_handles accepted: 1 when the call is done (the registers hold the results), 0 when it is not, with nothing changed. */
int nx2sync_svc(int num, uint64_t *regs, uint64_t at, void *ctx) {
    (void)ctx;
    if (!g_peek || !g_cas32 || !nx2sync_handles(num)) return 0;
    nx2sync_rec *me = t_rec ? t_rec : g_main;
    if (!me) return 0;
    atomic_fetch_add_explicit(&g_count[num], 1, memory_order_relaxed);
    uint64_t result;
    pthread_mutex_lock(&g_k);
    double t = (double)now_ns() / 1e9;
    note(me, num, regs, at, t);
    switch (num) {
    case SVC_ARBITRATE_LOCK: result = do_arbitrate_lock(me, regs); break;
    case SVC_ARBITRATE_UNLOCK: { int handed = 0; result = unlock_mutex(regs[0], &handed) ? R_INVALID_ADDRESS : 0; if (result == 0) detail(handed ? D_UNLOCK_HANDED : D_UNLOCK_NONE, 1); break; }
    case SVC_WAIT_KEY: result = do_wait_key(me, regs); break;
    default: result = do_signal_key(regs); break;
    }
    pthread_mutex_unlock(&g_k);
    if (result == R_INVALID_ADDRESS) atomic_fetch_add_explicit(&g_invalid, 1, memory_order_relaxed);
    regs[0] = result;
    return 1;
}

/* The numbers the host reads for its reports. */
uint64_t nx2sync_count(int num) { return num >= 0 && num < 256 ? atomic_load_explicit(&g_count[num], memory_order_relaxed) : 0; }
uint64_t nx2sync_invalid(void) { return atomic_load_explicit(&g_invalid, memory_order_relaxed); }

/* What the calls did, in the order of DETAIL in horizon_hle.py: the count at index i (0 to 9), 0 for any other. */
uint64_t nx2sync_detail(int i) { return i >= 0 && i < D_N ? atomic_load_explicit(&g_detail[i], memory_order_relaxed) : 0; }

/* The last call of a logical thread: 1 and *out filled, or 0 when it made none through this library. */
int nx2sync_last(uint32_t tid, nx2sync_last_t *out) {
    int ok = 0;
    pthread_mutex_lock(&g_k);
    nx2sync_rec *r = rec_of(tid ? tid : 1, 0, 0);
    if (r && r->has_last) { *out = r->last; ok = 1; }
    pthread_mutex_unlock(&g_k);
    return ok;
}

/* Up to max of the last calls of a logical thread, oldest first. Returns how many. */
int nx2sync_recent(uint32_t tid, nx2sync_call *out, int max) {
    int n = 0;
    pthread_mutex_lock(&g_k);
    nx2sync_rec *r = rec_of(tid ? tid : 1, 0, 0);
    if (r) {
        uint64_t total = r->nrecent, have = total < RECENT ? total : RECENT;
        for (uint64_t i = total - have; i < total && n < max; i++) out[n++] = r->recent[i % RECENT];
    }
    pthread_mutex_unlock(&g_k);
    return n;
}

/* How many logical threads wait on the mutex word at addr, or on the key at addr (for the tests and the hang report). */
int nx2sync_waiters(int on_key, uint64_t addr) {
    int n = 0;
    pthread_mutex_lock(&g_k);
    queue *q = q_find(on_key ? g_kq : g_mq, addr, 0);
    if (q) for (node *p = q->head; p; p = p->next) n++;
    pthread_mutex_unlock(&g_k);
    return n;
}

/* The number of the interface, for the host to check before it registers anything. */
int nx2sync_abi(void) { return 3; }
