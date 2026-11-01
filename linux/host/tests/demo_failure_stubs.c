/* Link the real demo main against controlled host/renderer outcomes. No GPU. */
#include "nx2linux/vulkan_renderer.h"
#include <SDL3/SDL.h>
#include <stdlib.h>
#include <string.h>

static unsigned polls;
static const char *mode(void) { const char *s = getenv("NX2LINUX_TEST_MODE"); return s ? s : "quit"; }
int nx2linux_host_init(Nx2LinuxHost *host, const Nx2LinuxHostConfig *config)
{ (void)config; memset(host, 0, sizeof(*host)); return SDL_Init(0) ? 0 : -1; }
void nx2linux_host_shutdown(Nx2LinuxHost *host) { (void)host; SDL_Quit(); }
SDL_Window *nx2linux_host_window(const Nx2LinuxHost *host) { (void)host; return NULL; }
int nx2linux_host_poll_event(Nx2LinuxHost *host, Nx2LinuxInputEvent *event)
{
    (void)host;
    if (strcmp(mode(), "poll-failure") == 0) return -1;
    if (polls++ == 0) {
        event->type = strcmp(mode(), "quit") == 0 ? NX2LINUX_EVENT_QUIT : NX2LINUX_EVENT_WINDOW_RESIZED;
        return 1;
    }
    if (strcmp(mode(), "suspended") == 0 && polls > 2) {
        event->type = NX2LINUX_EVENT_QUIT;
        return polls == 3 ? 1 : 0;
    }
    return 0;
}
int nx2linux_vulkan_init(Nx2LinuxVulkanRenderer *renderer, SDL_Window *window, const char *path)
{ (void)window; (void)path; memset(renderer, 0, sizeof(*renderer)); atomic_init(&renderer->validation_errors, 0); return 0; }
int nx2linux_vulkan_recreate_swapchain(Nx2LinuxVulkanRenderer *renderer)
{
    (void)renderer;
    if (strcmp(mode(), "suspended") == 0) return 1;
    if (strcmp(mode(), "draw-recreate-failure") == 0 && polls <= 2) return 0;
    return -1;
}
int nx2linux_vulkan_draw_triangle(Nx2LinuxVulkanRenderer *renderer)
{ (void)renderer; polls = 10; return 1; }
void nx2linux_vulkan_shutdown(Nx2LinuxVulkanRenderer *renderer) { (void)renderer; }
const char *nx2linux_vulkan_gpu_name(const Nx2LinuxVulkanRenderer *renderer)
{ (void)renderer; return "test stub (no GPU)"; }
