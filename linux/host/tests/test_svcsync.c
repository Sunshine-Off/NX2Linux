/* libnx2linux_sync (svcsync.c), step a1: the binding of host threads to logical threads and the contract of the handler. No game, no SDL, no Vulkan. */
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>

typedef uint32_t (*cas32_fn)(uint64_t, uint32_t, uint32_t);
typedef int (*peek_fn)(uint64_t, uint8_t *, uint64_t);
int nx2sync_init(peek_fn, cas32_fn);
int nx2sync_bind(uint32_t tid, uint32_t handle);
uint32_t nx2sync_current(void);
int nx2sync_handles(int num);
int nx2sync_svc(int num, uint64_t *regs, uint64_t at, void *ctx);
int nx2sync_abi(void);

static int failures;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static int fake_peek(uint64_t a, uint8_t *d, uint64_t n) { (void)a; (void)d; (void)n; return 0; }
static uint32_t fake_cas(uint64_t a, uint32_t e, uint32_t d) { (void)a; (void)d; return e; }

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

int main(void) {
    CHECK(nx2sync_abi() == 1);
    CHECK(nx2sync_init(NULL, fake_cas) == 1);      /* both ways into guest memory are needed */
    CHECK(nx2sync_init(fake_peek, NULL) == 1);
    CHECK(nx2sync_init(fake_peek, fake_cas) == 0);
    CHECK(nx2sync_init(fake_peek, fake_cas) == 0); /* again: replaces them */
    CHECK(nx2sync_current() == 1);
    pthread_t t[8];
    for (uintptr_t i = 0; i < 8; i++) CHECK(pthread_create(&t[i], NULL, bound_thread, (void *)(i + 2)) == 0);
    for (int i = 0; i < 8; i++) pthread_join(t[i], NULL);
    CHECK(nx2sync_current() == 1);                 /* the bindings of other threads did not reach this one */
    for (int n = 0; n < 256; n++) CHECK(nx2sync_handles(n) == 0);   /* step a1 answers no call */
    uint64_t regs[31] = {5, 6, 7}; int rc = nx2sync_svc(0x1c, regs, 0x1000, NULL);
    CHECK(rc == 0 && regs[0] == 5 && regs[1] == 6 && regs[2] == 7);  /* not handled: nothing changed */
    if (failures) { fprintf(stderr, "%d failures\n", failures); return 1; }
    puts("svcsync: ok");
    return 0;
}
