#ifndef NX2LINUX_SDL_HOST_H
#define NX2LINUX_SDL_HOST_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct SDL_Window SDL_Window;
typedef struct SDL_Gamepad SDL_Gamepad;

typedef enum Nx2LinuxInputEventType {
    NX2LINUX_EVENT_NONE = 0,
    NX2LINUX_EVENT_QUIT,
    NX2LINUX_EVENT_WINDOW_RESIZED,
    NX2LINUX_EVENT_KEY_DOWN,
    NX2LINUX_EVENT_KEY_UP,
    NX2LINUX_EVENT_GAMEPAD_ADDED,
    NX2LINUX_EVENT_GAMEPAD_REMOVED,
    NX2LINUX_EVENT_GAMEPAD_BUTTON_DOWN,
    NX2LINUX_EVENT_GAMEPAD_BUTTON_UP,
    NX2LINUX_EVENT_GAMEPAD_AXIS
} Nx2LinuxInputEventType;

typedef struct Nx2LinuxInputEvent {
    Nx2LinuxInputEventType type;
    uint32_t window_width;
    uint32_t window_height;
    int32_t key;
    int32_t scancode;
    uint32_t gamepad_id;
    uint8_t gamepad_button;
    uint8_t gamepad_axis;
    int16_t gamepad_axis_value;
} Nx2LinuxInputEvent;

typedef struct Nx2LinuxHostConfig {
    const char *title;
    int width;
    int height;
    bool hidden;
    bool vulkan;
} Nx2LinuxHostConfig;

typedef struct Nx2LinuxHost {
    SDL_Window *window;
    SDL_Gamepad **gamepads;
    size_t gamepad_count;
    bool initialized;
} Nx2LinuxHost;

/* Returns 0 on success, or -1 and leaves an SDL error available to the caller. */
int nx2linux_host_init(Nx2LinuxHost *host, const Nx2LinuxHostConfig *config);

/* Safe to call more than once and safe after a failed initialization. */
void nx2linux_host_shutdown(Nx2LinuxHost *host);

/* Returns 1 for an event, 0 when empty, -1 for invalid host or device-open failure. */
int nx2linux_host_poll_event(Nx2LinuxHost *host, Nx2LinuxInputEvent *event);

SDL_Window *nx2linux_host_window(const Nx2LinuxHost *host);

#endif
