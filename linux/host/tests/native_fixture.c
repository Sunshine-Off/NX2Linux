#include "nx2linux/posix_host.h"
#include <pthread.h>
#include <stdint.h>

static _Thread_local unsigned tls_value;
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static unsigned total;

static void *worker(void *unused)
{
    (void)unused;
    if (tls_value != 0) return (void *)(uintptr_t)1;
    tls_value = 7;
    pthread_mutex_lock(&mutex);
    total += tls_value;
    pthread_mutex_unlock(&mutex);
    return NULL;
}

static int run(const Nx2LinuxNativeHost *host)
{
    if (host->abi_version != NX2LINUX_NATIVE_ABI || host->struct_size < sizeof(*host) ||
        host->memory == NULL || host->memory_size < 4096) return 1;
    unsigned char *memory = host->memory;
    for (size_t i = 0; i < host->memory_size; i++) if (memory[i] != 0) return 2;
    memory[0] = 42;
    memory[host->memory_size - 1] = 24;
    uint64_t before = host->monotonic_ns();
    if (before == 0 || host->monotonic_ns() < before) return 3;
    total = 0;
    tls_value = 99;
    pthread_t threads[2];
    if (pthread_create(&threads[0], NULL, worker, NULL) != 0) return 4;
    if (pthread_create(&threads[1], NULL, worker, NULL) != 0) {
        pthread_join(threads[0], NULL);
        return 4;
    }
    void *first = NULL, *second = NULL;
    pthread_join(threads[0], &first);
    pthread_join(threads[1], &second);
    if (first != NULL || second != NULL || total != 14 || tls_value != 99) return 5;
    host->log("mmap, clock, native TLS, threads and mutex fixture passed");
#ifdef FAIL_ENTRY
    return 17;
#else
    return 0;
#endif
}

#ifdef BAD_ABI
#define FIXTURE_ABI 999u
#else
#define FIXTURE_ABI NX2LINUX_NATIVE_ABI
#endif
const Nx2LinuxNativeModule nx2linux_native_module = {
    .abi_version = FIXTURE_ABI, .struct_size = sizeof(Nx2LinuxNativeModule), .run = run,
};
