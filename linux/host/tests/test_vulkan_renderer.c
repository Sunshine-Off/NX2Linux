#include "nx2linux/vulkan_renderer.h"

#include <SDL3/SDL.h>

#include <stdio.h>

#ifndef NX2LINUX_SHADER_DIR
#define NX2LINUX_SHADER_DIR "."
#endif

int main(void)
{
    Nx2LinuxHost host = {0};
    const Nx2LinuxHostConfig config = {
        .title = "NX2Linux Vulkan test",
        .width = 640,
        .height = 360,
        .hidden = true,
        .vulkan = true,
    };
    if (nx2linux_host_init(&host, &config) != 0) {
        fprintf(stderr, "SKIP: Vulkan window unavailable: %s\n", SDL_GetError());
        return 77;
    }
    Nx2LinuxVulkanRenderer renderer = {0};
    if (nx2linux_vulkan_init(&renderer, nx2linux_host_window(&host), NX2LINUX_SHADER_DIR) != 0) {
        fprintf(stderr, "SKIP: Vulkan renderer unavailable: %s\n", SDL_GetError());
        nx2linux_vulkan_shutdown(&renderer);
        nx2linux_host_shutdown(&host);
        return 77;
    }
    int result = nx2linux_vulkan_draw_triangle(&renderer);
    if (result != 0) {
        fprintf(stderr, "Vulkan triangle failed: %s\n", SDL_GetError());
        nx2linux_vulkan_shutdown(&renderer);
        nx2linux_host_shutdown(&host);
        return 1;
    }
    printf("Vulkan triangle rendered on %s\n", nx2linux_vulkan_gpu_name(&renderer));
    nx2linux_vulkan_shutdown(&renderer);
    nx2linux_host_shutdown(&host);
    return 0;
}
