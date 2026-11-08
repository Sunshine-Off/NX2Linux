/* libnx2linux_sync (svcsync.c): the binding of host threads to logical threads, the four calls on mutex words and keys, and a mutex and a condition variable built from them with the algorithms of libnx,
 * under contention. A fake guest memory stands in for the AOT runtime's. No game, no SDL, no Vulkan. */
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef uint32_t (*cas32_fn)(uint64_t, uint32_t, uint32_t);
typedef int (*peek_fn)(uint64_t, uint8_t *, uint64_t);
typedef struct { uint32_t num, pad; uint64_t at, fp, lr; double t; } last_t;
typedef struct { uint32_t num, pad; uint64_t at, x0, x1; double t; } call_t;
int nx2sync_init(peek_fn, cas32_fn);
int nx2sync_bind(uint32_t tid, uint32_t handle);
uint32_t nx2sync_current(void);
int nx2sync_handles(int num);
int nx2sync_svc(int num, uint64_t *regs, uint64_t at, void *ctx);
uint64_t nx2sync_count(int num);
uint64_t nx2sync_invalid(void);
uint64_t nx2sync_detail(int i);
int nx2sync_last(uint32_t tid, last_t *out);
int nx2sync_recent(uint32_t tid, call_t *out, int max);
int nx2sync_waiters(int on_key, uint64_t addr);
int nx2sync_abi(void);

#define LOCK 0x1a
#define UNLOCK 0x1b
#define WAIT 0x1c
#define SIGNAL 0x1d
#define LIS 0x40000000u
#define R_INVALID_ADDRESS 0xCC01u
#define R_TIMEOUT 0xEA01u
#define FOREVER (~0ull)
#define BASE 0x1000u
#define WORDS 64

static int failures;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #c); __atomic_fetch_add(&failures, 1, __ATOMIC_RELAXED); } } while (0)

/* ---- a fake guest memory of 64 words at BASE ---- */
static _Atomic uint32_t mem[WORDS];
static int in_range(uint64_t a) { return a >= BASE && a < BASE + 4 * WORDS && !(a & 3); }
static int fake_peek(uint64_t a, uint8_t *d, uint64_t n) { if (n != 4 || !in_range(a)) return 1; uint32_t v = atomic_load(&mem[(a - BASE) / 4]); memcpy(d, &v, 4); return 0; }
static uint32_t fake_cas(uint64_t a, uint32_t e, uint32_t d) {
    if (!in_range(a)) return e ^ 1u;   /* what the runtime answers for a word that is not mapped */
    uint32_t exp = e; atomic_compare_exchange_strong(&mem[(a - BASE) / 4], &exp, d); return exp;
}
static uint64_t W(int i) { return BASE + 4u * (unsigned)i; }
static uint32_t load(uint64_t a) { return atomic_load(&mem[(a - BASE) / 4]); }

static uint64_t svc(int num, uint64_t x0, uint64_t x1, uint64_t x2, uint64_t x3) {
    uint64_t r[31] = {x0, x1, x2, x3}; r[29] = 0xF0; r[30] = 0xE0;
    CHECK(nx2sync_svc(num, r, 0x4000 + (uint64_t)num, NULL) == 1);
    return r[0];
}

static void wait_until(int (*cond)(void *), void *arg) {
    for (int i = 0; i < 20000 && !cond(arg); i++) usleep(500);
    CHECK(cond(arg));
}
typedef struct { int on_key; uint64_t addr; int n; } wcond;
static int has_waiters(void *p) { wcond *w = p; return nx2sync_waiters(w->on_key, w->addr) == w->n; }
static void wait_waiters(int on_key, uint64_t addr, int n) { wcond w = {on_key, addr, n}; wait_until(has_waiters, &w); }

/* ---- the mutex and the condition variable of libnx, on the calls ---- */
static void mx_lock(uint64_t m, uint32_t self) {
    for (;;) {
        uint32_t cur = fake_cas(m, 0, self);
        if (cur == 0) return;
        if (!(cur & LIS)) { if (fake_cas(m, cur, cur | LIS) != cur) continue; }
        svc(LOCK, cur & ~LIS, m, self, 0);
        if ((load(m) & ~LIS) == self) return;
    }
}
static void mx_unlock(uint64_t m, uint32_t self) { if (fake_cas(m, self, 0) != self) svc(UNLOCK, m, 0, 0, 0); }

/* ---- 1. the binding ---- */
static void *bound_thread(void *arg) {
    uint32_t want = (uint32_t)(uintptr_t)arg;
    CHECK(nx2sync_current() == 1);                 /* never bound: the main thread, like horizon_hle.py */
    CHECK(nx2sync_bind(want, 0x1000 + want) == 0);
    CHECK(nx2sync_current() == want);
    CHECK(nx2sync_bind(want + 100, 0) == 0);       /* a fiber switch: the same host thread now acts for another logical thread */
    CHECK(nx2sync_current() == want + 100);
    CHECK(nx2sync_bind(0, 0) == 0);                /* tid 0 is the main thread, as Horizon.bind(None) means */
    CHECK(nx2sync_current() == 1);
    return NULL;
}
static void test_binding(void) {
    pthread_t t[8];
    for (uintptr_t i = 0; i < 8; i++) CHECK(pthread_create(&t[i], NULL, bound_thread, (void *)(i + 2)) == 0);
    for (int i = 0; i < 8; i++) pthread_join(t[i], NULL);
    CHECK(nx2sync_current() == 1);                 /* the bindings of other threads did not reach this one */
}

/* ---- 2. deterministic cases, the same as test_horizon_sync.py runs on both implementations ---- */
static void test_no_waiter_cases(void) {
    memset((void *)mem, 0, sizeof mem);
    atomic_store(&mem[0], 0xD001);                 /* a mutex held by 0xD001 */
    CHECK(svc(UNLOCK, W(0), 0, 0, 0) == 0 && load(W(0)) == 0);                 /* unlock with no waiter clears it */
    atomic_store(&mem[0], 0xD001);
    CHECK(svc(LOCK, 0xD009, W(0), 0xD002, 0) == 0 && load(W(0)) == 0xD001);   /* the word is not (owner | listeners): nothing to wait for, nothing changed */
    CHECK(svc(SIGNAL, W(5), 1, 0, 0) == 0);                                    /* signal with nobody waiting */
    CHECK(svc(UNLOCK, 0x9000, 0, 0, 0) == R_INVALID_ADDRESS);
    CHECK(svc(LOCK, 1, 0x9000, 2, 0) == R_INVALID_ADDRESS);
    CHECK(svc(WAIT, 0x9000, W(5), 1, FOREVER) == R_INVALID_ADDRESS);
    CHECK(nx2sync_waiters(1, W(5)) == 0 && nx2sync_waiters(0, 0x9000) == 0);
}

enum { LOCK_QUEUED, LOCK_AT_ONCE, UNLOCK_HANDED, UNLOCK_NONE, WAIT_RELEASE_HANDED, WAIT_SIGNALLED, WAIT_TIMEOUT, RELOCK_QUEUED, SIGNAL_WOKE, SIGNAL_NOBODY };
static uint64_t D[10];
static void snap(void) { for (int i = 0; i < 10; i++) D[i] = nx2sync_detail(i); }
static uint64_t since(int i) { return nx2sync_detail(i) - D[i]; }

static void test_detail_of_the_simple_cases(void) {
    memset((void *)mem, 0, sizeof mem);
    snap();
    atomic_store(&mem[0], 0xD001);
    svc(UNLOCK, W(0), 0, 0, 0);                    /* nobody waits */
    svc(UNLOCK, W(0), 0, 0, 0);
    svc(LOCK, 0xD009, W(0), 0xD002, 0);            /* the word is not owner + listeners: returned at once */
    svc(SIGNAL, W(5), 1, 0, 0);                    /* nobody to wake */
    atomic_store(&mem[1], 0xD001);
    svc(WAIT, W(1), W(5), 0xD001, 0);              /* timed out at once */
    CHECK(since(UNLOCK_NONE) == 2 && since(UNLOCK_HANDED) == 0);
    CHECK(since(LOCK_AT_ONCE) == 1 && since(LOCK_QUEUED) == 0);
    CHECK(since(SIGNAL_NOBODY) == 1 && since(SIGNAL_WOKE) == 0);
    CHECK(since(WAIT_TIMEOUT) == 1 && since(WAIT_SIGNALLED) == 0);
    svc(UNLOCK, 0x9000, 0, 0, 0);                  /* an invalid address is counted by neither */
    CHECK(since(UNLOCK_NONE) == 2);
}

static void test_timeouts(void) {
    memset((void *)mem, 0, sizeof mem);
    atomic_store(&mem[0], 0xD001);
    struct timespec a, b; clock_gettime(CLOCK_MONOTONIC, &a);
    CHECK(svc(WAIT, W(0), W(5), 0xD001, 0) == R_TIMEOUT);                      /* timeout 0: at once */
    CHECK(load(W(0)) == 0xD001);                                               /* the mutex is taken again */
    CHECK(svc(WAIT, W(0), W(5), 0xD001, 20 * 1000000ull) == R_TIMEOUT);
    clock_gettime(CLOCK_MONOTONIC, &b);
    CHECK((b.tv_sec - a.tv_sec) * 1000 + (b.tv_nsec - a.tv_nsec) / 1000000 >= 20);
    CHECK(load(W(0)) == 0xD001 && nx2sync_waiters(1, W(5)) == 0);
}

typedef struct { uint64_t mutex, key; uint32_t tag, tid; uint64_t result; _Atomic int done; } waiter;
static void *waiter_main(void *p) {
    waiter *w = p;
    nx2sync_bind(w->tid, w->tag);
    w->result = svc(WAIT, w->mutex, w->key, w->tag, FOREVER);
    atomic_store(&w->done, 1);
    return NULL;
}
static int is_done(void *p) { return atomic_load(&((waiter *)p)->done); }

static void test_signal_order_and_count(void) {
    memset((void *)mem, 0, sizeof mem);
    waiter w[3]; pthread_t t[3];
    for (int i = 0; i < 3; i++) {
        w[i] = (waiter){W(i), W(10), 0xD100u + (uint32_t)i, 10u + (uint32_t)i, 0, 0};
        atomic_store(&mem[i], w[i].tag);           /* each waiter holds its own mutex */
        pthread_create(&t[i], NULL, waiter_main, &w[i]);
        wait_waiters(1, W(10), i + 1);             /* in the queue before the next one starts: the order is known */
    }
    CHECK(load(W(0)) == 0 && load(W(1)) == 0 && load(W(2)) == 0);              /* each released its mutex */
    CHECK(svc(SIGNAL, W(10), 2, 0, 0) == 0);                                   /* the first two, in order */
    wait_until(is_done, &w[0]); wait_until(is_done, &w[1]);
    usleep(20000);
    CHECK(!atomic_load(&w[2].done) && nx2sync_waiters(1, W(10)) == 1);
    CHECK(w[0].result == 0 && w[1].result == 0 && load(W(0)) == w[0].tag && load(W(1)) == w[1].tag);   /* back with their mutex */
    CHECK(svc(SIGNAL, W(10), 0, 0, 0) == 0);                                   /* count 0: all that are left */
    wait_until(is_done, &w[2]);
    CHECK(w[2].result == 0 && load(W(2)) == w[2].tag && nx2sync_waiters(1, W(10)) == 0);
    for (int i = 0; i < 3; i++) pthread_join(t[i], NULL);
    /* a negative count (as an int32) is all as well */
    waiter v = {W(3), W(11), 0xD200, 20, 0, 0}; pthread_t tv; atomic_store(&mem[3], v.tag);
    pthread_create(&tv, NULL, waiter_main, &v); wait_waiters(1, W(11), 1);
    CHECK(svc(SIGNAL, W(11), 0xFFFFFFFFull, 0, 0) == 0);
    wait_until(is_done, &v); pthread_join(tv, NULL);
}

struct locker { uint32_t tag, tid; _Atomic int got; };
static void *locker_main(void *p) { struct locker *x = p; nx2sync_bind(x->tid, x->tag); mx_lock(W(0), x->tag); atomic_store(&x->got, 1); return NULL; }

static void test_mutex_handoff(void) {
    memset((void *)mem, 0, sizeof mem);
    atomic_store(&mem[0], 0xD001);                                             /* the owner */
    struct locker a[2] = {{0xD002, 31, 0}, {0xD003, 32, 0}};
    pthread_t t[2];
    pthread_create(&t[0], NULL, locker_main, &a[0]); wait_waiters(0, W(0), 1);
    pthread_create(&t[1], NULL, locker_main, &a[1]); wait_waiters(0, W(0), 2);
    CHECK(load(W(0)) == (0xD001 | LIS));
    svc(UNLOCK, W(0), 0, 0, 0);                                                /* to the first, with the listeners bit while another waits */
    while (!atomic_load(&a[0].got)) usleep(200);
    CHECK(load(W(0)) == (0xD002 | LIS) && !atomic_load(&a[1].got));
    nx2sync_bind(31, 0xD002); mx_unlock(W(0), 0xD002);                          /* this thread stands for the first: its word has the bit, so the kernel call hands it on */
    while (!atomic_load(&a[1].got)) usleep(200);
    CHECK(load(W(0)) == 0xD003);                                               /* the last waiter: no bit */
    nx2sync_bind(0, 0);
    mx_unlock(W(0), 0xD003);
    CHECK(load(W(0)) == 0);
    pthread_join(t[0], NULL); pthread_join(t[1], NULL);
}

static void test_counters_and_last_call(void) {
    uint64_t before = nx2sync_count(SIGNAL), inv = nx2sync_invalid();
    nx2sync_bind(77, 0xD077);
    svc(SIGNAL, W(40), 1, 0, 0);
    CHECK(nx2sync_count(SIGNAL) == before + 1);
    last_t l; CHECK(nx2sync_last(77, &l) == 1 && l.num == SIGNAL && l.at == 0x4000 + SIGNAL && l.fp == 0xF0 && l.lr == 0xE0 && l.t > 0);
    for (int i = 0; i < 20; i++) svc(SIGNAL, W(40), (uint64_t)(100 + i), 0, 0);   /* more than it keeps */
    call_t c[32]; int n = nx2sync_recent(77, c, 32);
    CHECK(n == 16 && c[0].x1 == 104 && c[15].x1 == 119);   /* the last 16 of the 21 calls, oldest first */
    CHECK(nx2sync_recent(7777, c, 32) == 0);
    nx2sync_bind(0, 0);
    CHECK(nx2sync_last(7777, &l) == 0);            /* a thread that made no call */
    CHECK(nx2sync_invalid() == inv);
    svc(UNLOCK, 0x9000, 0, 0, 0);
    CHECK(nx2sync_invalid() == inv + 1);
}

/* ---- 3. contention ---- */
#define THREADS 8
#define ROUNDS 4000
static uint64_t g_shared;                          /* changed under the mutex, not atomically: a lost update is a failed exclusion */
static void *mutex_worker(void *p) {
    uintptr_t i = (uintptr_t)p; uint32_t self = 0xD300u + (uint32_t)i;
    nx2sync_bind(100u + (uint32_t)i, self);
    for (int r = 0; r < ROUNDS; r++) { mx_lock(W(20), self); uint64_t v = g_shared; if ((r & 63) == 0) sched_yield(); g_shared = v + 1; mx_unlock(W(20), self); }
    return NULL;
}
static void test_mutex_contention(void) {
    memset((void *)mem, 0, sizeof mem); g_shared = 0;
    pthread_t t[THREADS];
    for (uintptr_t i = 0; i < THREADS; i++) pthread_create(&t[i], NULL, mutex_worker, (void *)i);
    for (int i = 0; i < THREADS; i++) pthread_join(t[i], NULL);
    CHECK(g_shared == (uint64_t)THREADS * ROUNDS);
    CHECK(load(W(20)) == 0 && nx2sync_waiters(0, W(20)) == 0);
}

/* a bounded queue guarded by the mutex, with a condition for "not full" and one for "not empty" */
#define CAP 4
#define ITEMS 3000
#define PROD 4
#define CONS 4
static int q_n, q_head;
static int q_buf[CAP];
static _Atomic long consumed_sum, consumed_n;
static void cv_wait(uint64_t cv, uint64_t m, uint32_t self) { uint64_t r = svc(WAIT, m, cv, self, FOREVER); CHECK(r == 0); }
static void *producer(void *p) {
    uintptr_t i = (uintptr_t)p; uint32_t self = 0xD400u + (uint32_t)i;
    nx2sync_bind(200u + (uint32_t)i, self);
    for (int k = 1; k <= ITEMS; k++) {
        mx_lock(W(30), self);
        while (q_n == CAP) cv_wait(W(32), W(30), self);
        q_buf[(q_head + q_n++) % CAP] = k;
        svc(SIGNAL, W(31), 1, 0, 0);
        mx_unlock(W(30), self);
    }
    return NULL;
}
static void *consumer(void *p) {
    uintptr_t i = (uintptr_t)p; uint32_t self = 0xD500u + (uint32_t)i;
    nx2sync_bind(300u + (uint32_t)i, self);
    for (int k = 0; k < ITEMS; k++) {
        mx_lock(W(30), self);
        while (q_n == 0) cv_wait(W(31), W(30), self);
        int v = q_buf[q_head]; q_head = (q_head + 1) % CAP; q_n--;
        svc(SIGNAL, W(32), 1, 0, 0);
        mx_unlock(W(30), self);
        atomic_fetch_add(&consumed_sum, v); atomic_fetch_add(&consumed_n, 1);
    }
    return NULL;
}
static void test_condition_contention(void) {
    memset((void *)mem, 0, sizeof mem); q_n = q_head = 0; consumed_sum = consumed_n = 0;
    pthread_t p[PROD], c[CONS];
    for (uintptr_t i = 0; i < PROD; i++) pthread_create(&p[i], NULL, producer, (void *)i);
    for (uintptr_t i = 0; i < CONS; i++) pthread_create(&c[i], NULL, consumer, (void *)i);
    for (int i = 0; i < PROD; i++) pthread_join(p[i], NULL);
    for (int i = 0; i < CONS; i++) pthread_join(c[i], NULL);
    CHECK(consumed_n == (long)PROD * ITEMS);
    CHECK(consumed_sum == (long)PROD * ITEMS * (ITEMS + 1) / 2);
    CHECK(q_n == 0 && load(W(30)) == 0);
    CHECK(nx2sync_waiters(1, W(31)) == 0 && nx2sync_waiters(1, W(32)) == 0 && nx2sync_waiters(0, W(30)) == 0);
}

int main(void) {
    alarm(120);                                    /* a deadlock is a failed test, not a hung one */
    CHECK(nx2sync_abi() == 3);
    uint64_t regs[31] = {5, 6, 7};
    CHECK(nx2sync_svc(WAIT, regs, 0x1000, NULL) == 0 && regs[0] == 5 && regs[1] == 6);   /* not initialised: not handled, nothing changed */
    CHECK(nx2sync_init(NULL, fake_cas) == 1);      /* both ways into guest memory are needed */
    CHECK(nx2sync_init(fake_peek, NULL) == 1);
    CHECK(nx2sync_init(fake_peek, fake_cas) == 0);
    CHECK(nx2sync_init(fake_peek, fake_cas) == 0); /* again: replaces them */
    for (int n = 0; n < 256; n++) CHECK(nx2sync_handles(n) == (n >= LOCK && n <= SIGNAL));
    CHECK(nx2sync_svc(0x18, regs, 0x1000, NULL) == 0 && regs[0] == 5);                    /* WaitSynchronization is not here */
    CHECK(nx2sync_current() == 1);
    test_binding();
    test_no_waiter_cases();
    test_detail_of_the_simple_cases();
    test_timeouts();
    test_signal_order_and_count();
    test_mutex_handoff();
    test_counters_and_last_call();
    test_mutex_contention();
    test_condition_contention();
    if (failures) { fprintf(stderr, "%d failures\n", failures); return 1; }
    puts("svcsync: ok");
    return 0;
}
