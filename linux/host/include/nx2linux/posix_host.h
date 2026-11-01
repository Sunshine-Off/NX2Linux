#ifndef NX2LINUX_POSIX_HOST_H
#define NX2LINUX_POSIX_HOST_H

#include <stddef.h>
#include <stdint.h>

/* Native fixture contract, NOT a Switch guest ABI. No C++ types across this ABI. */
#define NX2LINUX_NATIVE_ABI 1u
typedef struct Nx2LinuxNativeHost {
    uint32_t abi_version;
    uint32_t struct_size;
    void *memory;
    size_t memory_size;
    uint64_t (*monotonic_ns)(void);
    void (*log)(const char *message);
} Nx2LinuxNativeHost;

typedef struct Nx2LinuxNativeModule {
    uint32_t abi_version;
    uint32_t struct_size;
    /* Must join all threads before returning. Host memory is valid only here. */
    int (*run)(const Nx2LinuxNativeHost *host);
} Nx2LinuxNativeModule;

/* Modules export a const Nx2LinuxNativeModule named nx2linux_native_module. */
enum {
    NX2LINUX_OK = 0,
    NX2LINUX_LOAD_ERROR = 1,
    NX2LINUX_ABI_ERROR = 2,
    NX2LINUX_MEMORY_ERROR = 3,
    NX2LINUX_MODULE_ERROR = 4
};

/* Trusted native ELF only. dlopen executes constructors, this is not a sandbox. */
int nx2linux_run_native_module(const char *path, char *error, size_t error_size);

#endif
