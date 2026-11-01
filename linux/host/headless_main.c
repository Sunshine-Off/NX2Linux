#include "nx2linux/posix_host.h"
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    if (argc != 3 || strcmp(argv[1], "--module") != 0) {
        fprintf(stderr, "Usage: nx2linux_headless --module /absolute/path/to/trusted-native-module.so\n");
        return 64;
    }
    char error[512];
    int status = nx2linux_run_native_module(argv[2], error, sizeof(error));
    if (status != 0) fprintf(stderr, "headless: %s\n", error);
    else puts("headless: native module completed (not a Switch runtime)");
    return status;
}
