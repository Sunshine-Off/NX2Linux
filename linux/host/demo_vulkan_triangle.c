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
        .title = "NX2Linux Vulkan triangle",
        .width = 1280,
        .height = 720,
        .hidden = false,
        .vulkan = true,
    };
    if (nx2linux_host_init(&host, &config) != 0) {
        fprintf(stderr, "Could not create SDL Vulkan window: %s\n", SDL_GetError());
        return 1;
    }

    Nx2LinuxVulkanRenderer renderer = {0};
    if (nx2linux_vulkan_init(&renderer, nx2linux_host_window(&host), NX2LINUX_SHADER_DIR) != 0) {
        fprintf(stderr, "Could not initialize Vulkan: %s\n", SDL_GetError());
        nx2linux_vulkan_shutdown(&renderer);
        nx2linux_host_shutdown(&host);
        return 1;
    }
    printf("Rendering triangle on %s for 10 seconds\n", nx2linux_vulkan_gpu_name(&renderer));

    Uint64 deadline = SDL_GetTicks() + 10000;
    bool running = true;
    while (running && SDL_GetTicks() < deadline) {
        Nx2LinuxInputEvent event;
        while (nx2linux_host_poll_event(&host, &event) == 1) {
            if (event.type == NX2LINUX_EVENT_QUIT) {
                running = false;
            } else if (event.type == NX2LINUX_EVENT_WINDOW_RESIZED &&
                       nx2linux_vulkan_recreate_swapchain(&renderer) != 0) {
                fprintf(stderr, "Could not recreate Vulkan swapchain: %s\n", SDL_GetError());
                running = false;
            }
        }
        if (running) {
            int result = nx2linux_vulkan_draw_triangle(&renderer);
            if (result == 1 && nx2linux_vulkan_recreate_swapchain(&renderer) != 0) {
                fprintf(stderr, "Could not recreate Vulkan swapchain: %s\n", SDL_GetError());
                running = false;
            } else if (result < 0) {
                fprintf(stderr, "Could not draw Vulkan triangle: %s\n", SDL_GetError());
                nx2linux_vulkan_shutdown(&renderer);
                nx2linux_host_shutdown(&host);
                return 1;
            }
        }
        SDL_Delay(16);
    }

    nx2linux_vulkan_shutdown(&renderer);
    nx2linux_host_shutdown(&host);
    return 0;
}
