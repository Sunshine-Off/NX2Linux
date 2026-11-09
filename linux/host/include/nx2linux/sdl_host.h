#ifndef NX2LINUX_SDL_HOST_H
#define NX2LINUX_SDL_HOST_H

#include <stdbool.h>
#include <stdint.h>

typedef struct SDL_Window SDL_Window;

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
    NX2LINUX_EVENT_GAMEPAD_AXIS,
    NX2LINUX_EVENT_MOUSE_BUTTON_DOWN,
    NX2LINUX_EVENT_MOUSE_BUTTON_UP,
    NX2LINUX_EVENT_MOUSE_MOTION
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
    int32_t mouse_x;      /* window coordinates, rounded */
    int32_t mouse_y;
    uint8_t mouse_button; /* SDL's: 1 left, 2 middle, 3 right */
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
    bool initialized;
} Nx2LinuxHost;

/* Returns 0 on success, or -1 and leaves an SDL error available to the caller. */
int nx2linux_host_init(Nx2LinuxHost *host, const Nx2LinuxHostConfig *config);

/* Safe to call more than once and safe after a failed initialization. */
void nx2linux_host_shutdown(Nx2LinuxHost *host);

/* Returns 1 for an event, 0 when the queue is empty, or -1 for an invalid host. */
int nx2linux_host_poll_event(Nx2LinuxHost *host, Nx2LinuxInputEvent *event);

/* Fills `event` from one SDL event (an unknown type gives NX2LINUX_EVENT_NONE). The queue is not involved, so a test can call it. */
union SDL_Event;
void nx2linux_event_from_sdl(const union SDL_Event *raw, Nx2LinuxInputEvent *event);

/* The event as the four numbers the Python side reads: out[0] the type, then
 *   key events:            key, window width, window height (the last two 0)
 *   gamepad button events: the SDL gamepad button, 0, 0
 *   gamepad axis events:   the SDL gamepad axis, its value (-32768 to 32767), 0
 *   gamepad added/removed: the gamepad id, 0, 0
 *   mouse events:          x, y, the button (0 for a motion)
 *   anything else:         key, window width, window height */
void nx2linux_event_pack(const Nx2LinuxInputEvent *event, int32_t out[4]);

SDL_Window *nx2linux_host_window(const Nx2LinuxHost *host);

#endif
