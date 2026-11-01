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
        fprintf(stderr, "FAIL: Vulkan renderer initialization: %s\n", SDL_GetError());
        nx2linux_vulkan_shutdown(&renderer);
        nx2linux_host_shutdown(&host);
        return 1;
    }
    int result = nx2linux_vulkan_draw_triangle(&renderer);
    if (result == 1) {
        result = nx2linux_vulkan_recreate_swapchain(&renderer);
        if (result == 0) {
            result = nx2linux_vulkan_draw_triangle(&renderer);
        }
    }
    if (result != 0) {
        fprintf(stderr, "Vulkan triangle failed: %s\n", SDL_GetError());
        nx2linux_vulkan_shutdown(&renderer);
        nx2linux_host_shutdown(&host);
        return 1;
    }
    const int sizes[][2] = {{800, 450}, {1024, 576}, {640, 360}};
    for (size_t i = 0; i < sizeof(sizes) / sizeof(sizes[0]); ++i) {
        if (!SDL_SetWindowSize(nx2linux_host_window(&host), sizes[i][0], sizes[i][1])) return 1;
        SDL_SyncWindow(nx2linux_host_window(&host));
        SDL_PumpEvents();
        VkPipeline pipeline = renderer.pipeline;
        VkFormat format = renderer.swapchain_format;
        if (nx2linux_vulkan_recreate_swapchain(&renderer) != 0 ||
            nx2linux_vulkan_draw_triangle(&renderer) != 0) {
            fprintf(stderr, "Vulkan swapchain recreation failed at %dx%d: %s\n",
                    sizes[i][0], sizes[i][1], SDL_GetError());
            nx2linux_vulkan_shutdown(&renderer);
            nx2linux_host_shutdown(&host);
            return 1;
        }
        if (renderer.swapchain_format == format && renderer.pipeline != pipeline) {
            fprintf(stderr, "Size-only resize unnecessarily recreated the pipeline\n");
            return 1;
        }
    }
    for (int i = 0; i < 60; ++i) {
        if (nx2linux_vulkan_draw_triangle(&renderer) != 0) {
            fprintf(stderr, "Vulkan frame %d failed: %s\n", i, SDL_GetError());
            nx2linux_vulkan_shutdown(&renderer);
            nx2linux_host_shutdown(&host);
            return 1;
        }
    }
    if (nx2linux_vulkan_frames_rendered(&renderer) < 64) {
        fprintf(stderr, "Vulkan frame counter is too low\n");
        nx2linux_vulkan_shutdown(&renderer);
        nx2linux_host_shutdown(&host);
        return 1;
    }
    printf("Vulkan triangle rendered on %s\n", nx2linux_vulkan_gpu_name(&renderer));
    nx2linux_vulkan_shutdown(&renderer);
    nx2linux_host_shutdown(&host);
    return atomic_load(&renderer.validation_errors) == 0 ? 0 : 1;
}
