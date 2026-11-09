#include "nx2linux/sdl_host.h"

#include <SDL3/SDL.h>

/* The smoke test must exercise its checks and setup in Release builds too. */
#ifdef NDEBUG
#undef NDEBUG
#endif
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

    /* The test window is hidden and never has the focus: without this SDL keeps the events of a gamepad back */
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
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

    /* What the Python side reads: SDL events become Nx2LinuxInputEvent (nx2linux_event_from_sdl), then four numbers (nx2linux_event_pack).
       SDL drops a gamepad or mouse event pushed by hand when no such device exists, so these go through the conversion and not through the queue */
    int32_t out[4];
    Nx2LinuxInputEvent ev;
    SDL_Event raw2 = {0};
    raw2.type = SDL_EVENT_GAMEPAD_BUTTON_DOWN;
    raw2.gbutton.which = 7;
    raw2.gbutton.button = SDL_GAMEPAD_BUTTON_EAST;
    nx2linux_event_from_sdl(&raw2, &ev);
    nx2linux_event_pack(&ev, out);
    assert(out[0] == NX2LINUX_EVENT_GAMEPAD_BUTTON_DOWN && out[1] == SDL_GAMEPAD_BUTTON_EAST && out[2] == 0 && out[3] == 0);

    raw2 = (SDL_Event){0};
    raw2.type = SDL_EVENT_GAMEPAD_AXIS_MOTION;
    raw2.gaxis.axis = SDL_GAMEPAD_AXIS_RIGHTY;
    raw2.gaxis.value = -20000;
    nx2linux_event_from_sdl(&raw2, &ev);
    nx2linux_event_pack(&ev, out);
    assert(out[0] == NX2LINUX_EVENT_GAMEPAD_AXIS && out[1] == SDL_GAMEPAD_AXIS_RIGHTY && out[2] == -20000 && out[3] == 0);

    raw2 = (SDL_Event){0};
    raw2.type = SDL_EVENT_GAMEPAD_ADDED;
    raw2.gdevice.which = 7;
    nx2linux_event_from_sdl(&raw2, &ev);
    nx2linux_event_pack(&ev, out);
    assert(out[0] == NX2LINUX_EVENT_GAMEPAD_ADDED && out[1] == 7);

    raw2 = (SDL_Event){0};
    raw2.type = SDL_EVENT_MOUSE_BUTTON_DOWN;
    raw2.button.button = SDL_BUTTON_LEFT;
    raw2.button.x = 100.4f;
    raw2.button.y = 50.6f;
    nx2linux_event_from_sdl(&raw2, &ev);
    nx2linux_event_pack(&ev, out);
    assert(out[0] == NX2LINUX_EVENT_MOUSE_BUTTON_DOWN && out[1] == 100 && out[2] == 51 && out[3] == SDL_BUTTON_LEFT);
    raw2.type = SDL_EVENT_MOUSE_BUTTON_UP;
    nx2linux_event_from_sdl(&raw2, &ev);
    assert(ev.type == NX2LINUX_EVENT_MOUSE_BUTTON_UP);

    raw2 = (SDL_Event){0};
    raw2.type = SDL_EVENT_MOUSE_MOTION;
    raw2.motion.x = 640.0f;
    raw2.motion.y = 360.0f;
    nx2linux_event_from_sdl(&raw2, &ev);
    nx2linux_event_pack(&ev, out);
    assert(out[0] == NX2LINUX_EVENT_MOUSE_MOTION && out[1] == 640 && out[2] == 360 && out[3] == 0);

    raw2 = (SDL_Event){0};
    raw2.type = SDL_EVENT_USER;
    nx2linux_event_from_sdl(&raw2, &ev);
    assert(ev.type == NX2LINUX_EVENT_NONE);

    /* A key keeps its key, a resize its size, and the numbers of the first types did not move */
    nx2linux_event_pack(&(Nx2LinuxInputEvent){.type = NX2LINUX_EVENT_KEY_DOWN, .key = SDLK_A}, out);
    assert(out[0] == NX2LINUX_EVENT_KEY_DOWN && out[1] == SDLK_A);
    nx2linux_event_pack(&(Nx2LinuxInputEvent){.type = NX2LINUX_EVENT_WINDOW_RESIZED, .window_width = 800, .window_height = 450}, out);
    assert(out[0] == NX2LINUX_EVENT_WINDOW_RESIZED && out[2] == 800 && out[3] == 450);
    assert(NX2LINUX_EVENT_GAMEPAD_AXIS == 9 && NX2LINUX_EVENT_MOUSE_BUTTON_DOWN == 10 && NX2LINUX_EVENT_MOUSE_BUTTON_UP == 11 && NX2LINUX_EVENT_MOUSE_MOTION == 12);

    /* A virtual gamepad stands for a real one: SDL says it was added, the host opens it, and only then do its button and axis reach the queue */
    SDL_VirtualJoystickDesc vdesc;
    SDL_INIT_INTERFACE(&vdesc);
    vdesc.type = SDL_JOYSTICK_TYPE_GAMEPAD;
    vdesc.naxes = SDL_GAMEPAD_AXIS_COUNT;
    vdesc.nbuttons = SDL_GAMEPAD_BUTTON_COUNT;
    vdesc.name = "NX2Linux test pad";
    SDL_JoystickID vid = SDL_AttachVirtualJoystick(&vdesc);
    assert(vid != 0);
    int added = 0;
    for (int i = 0; i < 50 && !added; i++) {
        SDL_PumpEvents();
        SDL_Delay(5);
        while (nx2linux_host_poll_event(&host, &event) == 1) {
            if (event.type == NX2LINUX_EVENT_GAMEPAD_ADDED && event.gamepad_id == vid) {
                added = 1;
            }
        }
    }
    assert(added);
    SDL_Gamepad *vpad = SDL_GetGamepadFromID(vid);
    assert(vpad != NULL); /* the host opened it */
    SDL_Joystick *vjoy = SDL_GetGamepadJoystick(vpad);
    assert(SDL_SetJoystickVirtualButton(vjoy, SDL_GAMEPAD_BUTTON_EAST, true));
    assert(SDL_SetJoystickVirtualAxis(vjoy, SDL_GAMEPAD_AXIS_LEFTX, 12345));
    int got_button = 0, got_axis = 0;
    for (int i = 0; i < 50 && !(got_button && got_axis); i++) {
        SDL_PumpEvents();
        SDL_Delay(5);
        while (nx2linux_host_poll_event(&host, &event) == 1) {
            nx2linux_event_pack(&event, out);
            if (event.type == NX2LINUX_EVENT_GAMEPAD_BUTTON_DOWN && out[1] == SDL_GAMEPAD_BUTTON_EAST) {
                got_button = 1;
            }
            if (event.type == NX2LINUX_EVENT_GAMEPAD_AXIS && out[1] == SDL_GAMEPAD_AXIS_LEFTX && out[2] == 12345) {
                got_axis = 1;
            }
        }
    }
    assert(got_button && got_axis);
    assert(SDL_DetachVirtualJoystick(vid));
    int removed = 0;
    for (int i = 0; i < 50 && !removed; i++) {
        SDL_PumpEvents();
        SDL_Delay(5);
        while (nx2linux_host_poll_event(&host, &event) == 1) {
            if (event.type == NX2LINUX_EVENT_GAMEPAD_REMOVED) {
                removed = 1;
            }
        }
    }
    assert(removed);

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
