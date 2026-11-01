#include "nx2linux/sdl_host.h"

#include <SDL3/SDL.h>

/* The smoke test must exercise its checks and setup in Release builds too. */
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdio.h>

static Nx2LinuxInputEvent await_event(Nx2LinuxHost *host, Nx2LinuxInputEventType type,
                                     SDL_JoystickID id)
{
    Nx2LinuxInputEvent event = {0};
    Uint64 deadline = SDL_GetTicks() + 2000;
    while (SDL_GetTicks() < deadline) {
        int status = nx2linux_host_poll_event(host, &event);
        assert(status >= 0);
        if (status == 1 && event.type == type && event.gamepad_id == id) return event;
        SDL_Delay(1);
    }
    fprintf(stderr, "missing input event %d for %u: %s\n", type, id, SDL_GetError());
    assert(0);
    return event;
}

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
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
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

    SDL_VirtualJoystickDesc description;
    SDL_INIT_INTERFACE(&description);
    description.type = SDL_JOYSTICK_TYPE_GAMEPAD;
    description.naxes = SDL_GAMEPAD_AXIS_COUNT;
    description.nbuttons = SDL_GAMEPAD_BUTTON_COUNT;
    description.axis_mask = (1u << SDL_GAMEPAD_AXIS_COUNT) - 1;
    description.button_mask = (1u << SDL_GAMEPAD_BUTTON_COUNT) - 1;
    description.name = "NX2Linux test gamepad";
    SDL_JoystickID id = SDL_AttachVirtualJoystick(&description);
    assert(id != 0);
    await_event(&host, NX2LINUX_EVENT_GAMEPAD_ADDED, id);
    assert(host.gamepad_count >= 1);
    SDL_Gamepad *pad = SDL_GetGamepadFromID(id);
    assert(pad != NULL); /* Would fail if the host only forwarded ADDED. */
    SDL_Joystick *joystick = SDL_GetGamepadJoystick(pad);
    assert(SDL_SetJoystickVirtualButton(joystick, SDL_GAMEPAD_BUTTON_SOUTH, true));
    event = await_event(&host, NX2LINUX_EVENT_GAMEPAD_BUTTON_DOWN, id);
    assert(event.gamepad_button == SDL_GAMEPAD_BUTTON_SOUTH);
    assert(SDL_SetJoystickVirtualButton(joystick, SDL_GAMEPAD_BUTTON_SOUTH, false));
    await_event(&host, NX2LINUX_EVENT_GAMEPAD_BUTTON_UP, id);
    assert(SDL_SetJoystickVirtualAxis(joystick, SDL_GAMEPAD_AXIS_LEFTX, 12345));
    event = await_event(&host, NX2LINUX_EVENT_GAMEPAD_AXIS, id);
    assert(event.gamepad_axis == SDL_GAMEPAD_AXIS_LEFTX && event.gamepad_axis_value == 12345);
    assert(SDL_DetachVirtualJoystick(id));
    await_event(&host, NX2LINUX_EVENT_GAMEPAD_REMOVED, id);
    assert(SDL_GetGamepadFromID(id) == NULL);
    /* Reconnect and leave the second device open to exercise shutdown cleanup. */
    id = SDL_AttachVirtualJoystick(&description);
    assert(id != 0);
    await_event(&host, NX2LINUX_EVENT_GAMEPAD_ADDED, id);
    nx2linux_host_shutdown(&host);
    assert(nx2linux_host_window(&host) == NULL);
    assert(host.gamepads == NULL && host.gamepad_count == 0);
    nx2linux_host_shutdown(&host);

    puts("SDL host smoke test passed");
    return 0;
}
