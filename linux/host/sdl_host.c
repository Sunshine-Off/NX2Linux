#include "nx2linux/sdl_host.h"

#include <SDL3/SDL.h>

#include <string.h>

static void clear_event(Nx2LinuxInputEvent *event)
{
    memset(event, 0, sizeof(*event));
    event->type = NX2LINUX_EVENT_NONE;
}

int nx2linux_host_init(Nx2LinuxHost *host, const Nx2LinuxHostConfig *config)
{
    if (host == NULL || config == NULL || config->width <= 0 || config->height <= 0) {
        SDL_SetError("invalid NX2Linux host configuration");
        return -1;
    }

    memset(host, 0, sizeof(*host));
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMEPAD)) {
        return -1;
    }

    SDL_WindowFlags flags = SDL_WINDOW_RESIZABLE;
    if (config->hidden) {
        flags |= SDL_WINDOW_HIDDEN;
    }
    if (config->vulkan) {
        flags |= SDL_WINDOW_VULKAN;
    }
    host->window = SDL_CreateWindow(config->title != NULL ? config->title : "NX2Linux",
                                    config->width, config->height, flags);
    if (host->window == NULL) {
        SDL_Quit();
        return -1;
    }
    host->initialized = true;
    return 0;
}

void nx2linux_host_shutdown(Nx2LinuxHost *host)
{
    if (host == NULL) {
        return;
    }
    if (host->window != NULL) {
        SDL_DestroyWindow(host->window);
    }
    if (host->initialized) {
        SDL_Quit();
    }
    memset(host, 0, sizeof(*host));
}

int nx2linux_host_poll_event(Nx2LinuxHost *host, Nx2LinuxInputEvent *event)
{
    if (host == NULL || event == NULL || !host->initialized) {
        return -1;
    }
    clear_event(event);

    SDL_Event raw;
    if (!SDL_PollEvent(&raw)) {
        return 0;
    }

    nx2linux_event_from_sdl(&raw, event);
    /* SDL sends the buttons and axes of a gamepad only once it is opened, and says that one exists with an ADDED event (also for those plugged in before SDL_Init) */
    if (event->type == NX2LINUX_EVENT_GAMEPAD_ADDED) {
        (void)SDL_OpenGamepad((SDL_JoystickID)event->gamepad_id);
    } else if (event->type == NX2LINUX_EVENT_GAMEPAD_REMOVED) {
        SDL_Gamepad *gamepad = SDL_GetGamepadFromID((SDL_JoystickID)event->gamepad_id);
        if (gamepad != NULL) {
            SDL_CloseGamepad(gamepad);
        }
    }
    return 1;
}

void nx2linux_event_from_sdl(const union SDL_Event *rawp, Nx2LinuxInputEvent *event)
{
    const SDL_Event raw = *rawp;
    clear_event(event);

    switch (raw.type) {
    case SDL_EVENT_QUIT:
        event->type = NX2LINUX_EVENT_QUIT;
        break;
    case SDL_EVENT_WINDOW_RESIZED:
        event->type = NX2LINUX_EVENT_WINDOW_RESIZED;
        event->window_width = (uint32_t)raw.window.data1;
        event->window_height = (uint32_t)raw.window.data2;
        break;
    case SDL_EVENT_KEY_DOWN:
        event->type = NX2LINUX_EVENT_KEY_DOWN;
        event->key = raw.key.key;
        event->scancode = raw.key.scancode;
        break;
    case SDL_EVENT_KEY_UP:
        event->type = NX2LINUX_EVENT_KEY_UP;
        event->key = raw.key.key;
        event->scancode = raw.key.scancode;
        break;
    case SDL_EVENT_GAMEPAD_ADDED:
        event->type = NX2LINUX_EVENT_GAMEPAD_ADDED;
        event->gamepad_id = raw.gdevice.which;
        break;
    case SDL_EVENT_GAMEPAD_REMOVED:
        event->type = NX2LINUX_EVENT_GAMEPAD_REMOVED;
        event->gamepad_id = raw.gdevice.which;
        break;
    case SDL_EVENT_GAMEPAD_BUTTON_DOWN:
        event->type = NX2LINUX_EVENT_GAMEPAD_BUTTON_DOWN;
        event->gamepad_id = raw.gbutton.which;
        event->gamepad_button = raw.gbutton.button;
        break;
    case SDL_EVENT_GAMEPAD_BUTTON_UP:
        event->type = NX2LINUX_EVENT_GAMEPAD_BUTTON_UP;
        event->gamepad_id = raw.gbutton.which;
        event->gamepad_button = raw.gbutton.button;
        break;
    case SDL_EVENT_GAMEPAD_AXIS_MOTION:
        event->type = NX2LINUX_EVENT_GAMEPAD_AXIS;
        event->gamepad_id = raw.gaxis.which;
        event->gamepad_axis = raw.gaxis.axis;
        event->gamepad_axis_value = raw.gaxis.value;
        break;
    case SDL_EVENT_MOUSE_BUTTON_DOWN:
    case SDL_EVENT_MOUSE_BUTTON_UP:
        event->type = raw.type == SDL_EVENT_MOUSE_BUTTON_DOWN ? NX2LINUX_EVENT_MOUSE_BUTTON_DOWN : NX2LINUX_EVENT_MOUSE_BUTTON_UP;
        event->mouse_x = (int32_t)(raw.button.x + (raw.button.x < 0 ? -0.5f : 0.5f));
        event->mouse_y = (int32_t)(raw.button.y + (raw.button.y < 0 ? -0.5f : 0.5f));
        event->mouse_button = raw.button.button;
        break;
    case SDL_EVENT_MOUSE_MOTION:
        event->type = NX2LINUX_EVENT_MOUSE_MOTION;
        event->mouse_x = (int32_t)(raw.motion.x + (raw.motion.x < 0 ? -0.5f : 0.5f));
        event->mouse_y = (int32_t)(raw.motion.y + (raw.motion.y < 0 ? -0.5f : 0.5f));
        break;
    default:
        break;
    }
}

void nx2linux_event_pack(const Nx2LinuxInputEvent *event, int32_t out[4])
{
    out[0] = (int32_t)event->type;
    out[1] = event->key;
    out[2] = (int32_t)event->window_width;
    out[3] = (int32_t)event->window_height;
    switch (event->type) {
    case NX2LINUX_EVENT_GAMEPAD_BUTTON_DOWN:
    case NX2LINUX_EVENT_GAMEPAD_BUTTON_UP:
        out[1] = event->gamepad_button;
        out[2] = out[3] = 0;
        break;
    case NX2LINUX_EVENT_GAMEPAD_AXIS:
        out[1] = event->gamepad_axis;
        out[2] = event->gamepad_axis_value;
        out[3] = 0;
        break;
    case NX2LINUX_EVENT_GAMEPAD_ADDED:
    case NX2LINUX_EVENT_GAMEPAD_REMOVED:
        out[1] = (int32_t)event->gamepad_id;
        out[2] = out[3] = 0;
        break;
    case NX2LINUX_EVENT_MOUSE_BUTTON_DOWN:
    case NX2LINUX_EVENT_MOUSE_BUTTON_UP:
        out[1] = event->mouse_x;
        out[2] = event->mouse_y;
        out[3] = event->mouse_button;
        break;
    case NX2LINUX_EVENT_MOUSE_MOTION:
        out[1] = event->mouse_x;
        out[2] = event->mouse_y;
        out[3] = 0;
        break;
    default:
        break;
    }
}

SDL_Window *nx2linux_host_window(const Nx2LinuxHost *host)
{
    return host != NULL ? host->window : NULL;
}
