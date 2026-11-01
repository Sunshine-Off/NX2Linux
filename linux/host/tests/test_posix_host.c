#include "nx2linux/posix_host.h"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    assert(argc == 4);
    char error[512];
    for (int i = 0; i < 3; i++) {
        assert(nx2linux_run_native_module(argv[1], error, sizeof(error)) == NX2LINUX_OK);
        assert(error[0] == '\0');
    }
    assert(nx2linux_run_native_module(argv[2], error, sizeof(error)) == NX2LINUX_ABI_ERROR);
    assert(strstr(error, "descriptor") != NULL);
    assert(nx2linux_run_native_module(argv[3], error, sizeof(error)) == NX2LINUX_MODULE_ERROR);
    assert(strstr(error, "17") != NULL);
    assert(nx2linux_run_native_module("/nonexistent/nx2linux.so", error, sizeof(error)) == NX2LINUX_LOAD_ERROR);
    assert(nx2linux_run_native_module(NULL, NULL, 0) == NX2LINUX_LOAD_ERROR);
    puts("POSIX native module lifecycle and rejection tests passed");
    return 0;
}
