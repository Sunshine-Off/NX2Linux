#include "nx2linux/vulkan_renderer.h"

#include <SDL3/SDL.h>

#include <stdio.h>

#ifndef NX2LINUX_SHADER_DIR
#define NX2LINUX_SHADER_DIR "."
#endif

/* The pixel mapping of a presented frame: scaling picks the nearest source pixel, BGRA swaps red and blue, alpha is forced opaque */
static int check_scaling(void)
{
    const uint8_t src[2 * 2 * 4] = {
        10, 11, 12, 0,   20, 21, 22, 0,
        30, 31, 32, 0,   40, 41, 42, 0,
    };
    uint8_t out[4 * 4 * 4];
    nx2linux_scale_rgba(src, 2, 2, 8, out, 4, 4, false);
    if (out[0] != 10 || out[4 * 2] != 20 || out[(4 * 2 + 3) * 4] != 40 || out[3] != 255 || out[1] != 11) {
        fprintf(stderr, "RGBA scaling is wrong\n");
        return 1;
    }
    nx2linux_scale_rgba(src, 2, 2, 8, out, 2, 2, true);
    if (out[0] != 12 || out[2] != 10 || out[1] != 11 || out[4 + 0] != 22) {
        fprintf(stderr, "BGRA swizzle is wrong\n");
        return 1;
    }
    return 0;
}

int main(void)
{
    if (check_scaling() != 0) {
        return 1;
    }
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
        SDL_SetWindowSize(nx2linux_host_window(&host), sizes[i][0], sizes[i][1]);
        if (nx2linux_vulkan_recreate_swapchain(&renderer) != 0 ||
            nx2linux_vulkan_draw_triangle(&renderer) != 0) {
            fprintf(stderr, "Vulkan swapchain recreation failed at %dx%d: %s\n",
                    sizes[i][0], sizes[i][1], SDL_GetError());
            nx2linux_vulkan_shutdown(&renderer);
            nx2linux_host_shutdown(&host);
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
    /* a frame from the guest: a 64x36 gradient shown on the swapchain */
    uint8_t frame[64 * 36 * 4];
    for (int y = 0; y < 36; ++y) {
        for (int x = 0; x < 64; ++x) {
            uint8_t *px = frame + (y * 64 + x) * 4;
            px[0] = (uint8_t)(x * 4); px[1] = (uint8_t)(y * 7); px[2] = 128; px[3] = 0;
        }
    }
    const uint64_t before = nx2linux_vulkan_frames_rendered(&renderer);
    for (int i = 0; i < 3; ++i) {
        int shown = nx2linux_vulkan_present_rgba(&renderer, frame, 64, 36, 0);
        if (shown == 1) {
            shown = nx2linux_vulkan_recreate_swapchain(&renderer);
        }
        if (shown != 0) {
            fprintf(stderr, "presenting a guest frame failed: %s\n", SDL_GetError());
            nx2linux_vulkan_shutdown(&renderer);
            nx2linux_host_shutdown(&host);
            return 1;
        }
    }
    if (nx2linux_vulkan_frames_rendered(&renderer) < before + 2) {
        fprintf(stderr, "guest frames were not counted\n");
        return 1;
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
    return 0;
}
