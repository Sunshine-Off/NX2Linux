#include "nx2linux/sdl_host.h"

#include <SDL3/SDL.h>

#include <assert.h>
#include <stdio.h>

int main(void)
{
    Nx2LinuxHost host = {0};
    Nx2LinuxInputEvent event = {0};
    const Nx2LinuxHostConfig config = {
        .title = "NX2Linux SDL test",
        .width = 640,
        .height = 360,
        .hidden = true,
    };

    assert(nx2linux_host_poll_event(&host, &event) == -1);
    assert(nx2linux_host_init(&host, &config) == 0);
    assert(nx2linux_host_window(&host) != NULL);

    /* SDL may enqueue lifecycle events while creating the hidden window. */
    while (nx2linux_host_poll_event(&host, &event) == 1) {
    }

    SDL_Event raw = {0};
    raw.type = SDL_EVENT_KEY_DOWN;
    raw.key.windowID = SDL_GetWindowID(nx2linux_host_window(&host));
    raw.key.key = SDLK_A;
    raw.key.scancode = SDL_SCANCODE_A;
    assert(SDL_PushEvent(&raw) == 1);
    assert(nx2linux_host_poll_event(&host, &event) == 1);
    assert(event.type == NX2LINUX_EVENT_KEY_DOWN);
    assert(event.key == SDLK_A);
    assert(event.scancode == SDL_SCANCODE_A);

    int window_width = 0;
    int window_height = 0;
    SDL_GetWindowSize(nx2linux_host_window(&host), &window_width, &window_height);
    assert(window_width == config.width);
    assert(window_height == config.height);

    assert(nx2linux_host_poll_event(&host, &event) == 0);

    nx2linux_host_shutdown(&host);
    assert(nx2linux_host_window(&host) == NULL);

    puts("SDL host smoke test passed");
    return 0;
}
