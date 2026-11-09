/* The window a recompiled game draws into (NX2Linux S7bis). One process-wide display: Python's ctypes calls
 * these from the thread that runs the guest, the SDL window and the Vulkan swapchain live behind them.
 * nx2_display_present shows an RGBA8 frame (the game's framebuffer as the NVN layer fills it). */
#include "nx2linux/sdl_host.h"
#include "nx2linux/vulkan_renderer.h"

#include <SDL3/SDL.h>

#include <stdio.h>
#include <string.h>

#ifndef NX2LINUX_SHADER_DIR
#define NX2LINUX_SHADER_DIR "."
#endif

static Nx2LinuxHost g_host;
static Nx2LinuxVulkanRenderer g_renderer;
static int g_open;
static char g_error[256];

static void remember_error(const char *where)
{
    snprintf(g_error, sizeof(g_error), "%s: %s", where, SDL_GetError());
}

const char *nx2_display_error(void) { return g_error; }

/* 0 on success, -1 on error (nx2_display_error says why) */
int nx2_display_open(const char *title, int width, int height, int hidden, const char *shader_dir)
{
    if (g_open) {
        return 0;
    }
    const Nx2LinuxHostConfig config = {
        .title = title ? title : "NX2Linux",
        .width = width,
        .height = height,
        .hidden = hidden != 0,
        .vulkan = true,
    };
    if (nx2linux_host_init(&g_host, &config) != 0) {
        remember_error("window");
        return -1;
    }
    if (nx2linux_vulkan_init(&g_renderer, nx2linux_host_window(&g_host),
                             shader_dir && *shader_dir ? shader_dir : NX2LINUX_SHADER_DIR) != 0) {
        remember_error("vulkan");
        nx2linux_vulkan_shutdown(&g_renderer);
        nx2linux_host_shutdown(&g_host);
        memset(&g_renderer, 0, sizeof(g_renderer));
        return -1;
    }
    g_open = 1;
    return 0;
}

/* 0 on success, -1 on error. A lost swapchain is rebuilt and the frame shown again. */
int nx2_display_present(const uint8_t *rgba, uint32_t width, uint32_t height, uint32_t stride)
{
    if (!g_open) {
        snprintf(g_error, sizeof(g_error), "display not open");
        return -1;
    }
    for (int attempt = 0; attempt < 3; ++attempt) {
        int shown = nx2linux_vulkan_present_rgba(&g_renderer, rgba, width, height, stride);
        if (shown == 0) {
            return 0;
        }
        if (shown < 0) {
            remember_error("present");
            return -1;
        }
        if (nx2linux_vulkan_recreate_swapchain(&g_renderer) != 0) {
            remember_error("swapchain");
            return -1;
        }
    }
    snprintf(g_error, sizeof(g_error), "swapchain kept going out of date");
    return -1;
}

/* Next input event as four numbers (nx2linux_event_pack in sdl_host.h says what they are for each type); 0 when none is waiting, 1 otherwise */
int nx2_display_poll(int32_t out[4])
{
    Nx2LinuxInputEvent event;
    if (!g_open || nx2linux_host_poll_event(&g_host, &event) != 1) {
        return 0;
    }
    nx2linux_event_pack(&event, out);
    return 1;
}

uint64_t nx2_display_frames(void) { return g_open ? nx2linux_vulkan_frames_rendered(&g_renderer) : 0; }

const char *nx2_display_gpu(void) { return g_open ? nx2linux_vulkan_gpu_name(&g_renderer) : ""; }

void nx2_display_close(void)
{
    if (!g_open) {
        return;
    }
    nx2linux_vulkan_shutdown(&g_renderer);
    nx2linux_host_shutdown(&g_host);
    memset(&g_renderer, 0, sizeof(g_renderer));
    g_open = 0;
}
