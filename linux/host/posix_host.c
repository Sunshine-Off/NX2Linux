#define _GNU_SOURCE
#include "nx2linux/posix_host.h"

#include <dlfcn.h>
#include <stdio.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    return (uint64_t)now.tv_sec * UINT64_C(1000000000) + (uint64_t)now.tv_nsec;
}

static void log_message(const char *message)
{
    fprintf(stderr, "native-module: %s\n", message != NULL ? message : "");
}

static int failure(char *error, size_t size, int code, const char *message)
{
    if (error != NULL && size != 0) snprintf(error, size, "%s", message);
    return code;
}

int nx2linux_run_native_module(const char *path, char *error, size_t error_size)
{
    if (error != NULL && error_size != 0) error[0] = '\0';
    if (path == NULL || path[0] == '\0')
        return failure(error, error_size, NX2LINUX_LOAD_ERROR, "module path is empty");
    void *module = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (module == NULL) return failure(error, error_size, NX2LINUX_LOAD_ERROR, dlerror());
    dlerror();
    const Nx2LinuxNativeModule *descriptor = dlsym(module, "nx2linux_native_module");
    const char *symbol_error = dlerror();
    if (symbol_error != NULL || descriptor == NULL ||
        descriptor->abi_version != NX2LINUX_NATIVE_ABI ||
        descriptor->struct_size < sizeof(*descriptor) || descriptor->run == NULL) {
        dlclose(module);
        return failure(error, error_size, NX2LINUX_ABI_ERROR, "missing or incompatible native module descriptor");
    }
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0 || page > 1024 * 1024) {
        dlclose(module);
        return failure(error, error_size, NX2LINUX_MEMORY_ERROR, "invalid host page size");
    }
    size_t memory_size = 16 * (size_t)page;
    size_t mapping_size = memory_size + 2 * (size_t)page;
    void *mapping = mmap(NULL, mapping_size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    void *memory = mapping == MAP_FAILED ? NULL : (char *)mapping + page;
    if (mapping == MAP_FAILED || mprotect(memory, memory_size, PROT_READ | PROT_WRITE) != 0) {
        if (mapping != MAP_FAILED) munmap(mapping, mapping_size);
        dlclose(module);
        return failure(error, error_size, NX2LINUX_MEMORY_ERROR, "cannot allocate guarded host memory");
    }
    const Nx2LinuxNativeHost host = {
        .abi_version = NX2LINUX_NATIVE_ABI, .struct_size = sizeof(host),
        .memory = memory, .memory_size = memory_size,
        .monotonic_ns = monotonic_ns, .log = log_message,
    };
    int result = descriptor->run(&host);
    /* Module destructors may still use memory. Unload before releasing it. */
    dlclose(module);
    munmap(mapping, mapping_size);
    if (result != 0) {
        char message[96];
        snprintf(message, sizeof(message), "native module returned %d", result);
        return failure(error, error_size, NX2LINUX_MODULE_ERROR, message);
    }
    return NX2LINUX_OK;
}
