# NX2Linux SDL host

This directory contains the S2 host layer for Linux x86_64. It owns the SDL3
window and translates SDL keyboard, gamepad, resize and quit events into a
stable NX2Linux event contract.

The layer does not create a Vulkan surface yet. That is deliberately deferred
to S3 so the window and input contract can be tested independently with SDL's
dummy video driver.

## Build and test

Dependencies are SDL3 development headers, CMake, a C compiler and `pkg-config`.

```sh
cmake -S linux/host -B build/linux-host -DCMAKE_BUILD_TYPE=Debug
cmake --build build/linux-host
ctest --test-dir build/linux-host --output-on-failure
```

The smoke test uses `SDL_VIDEODRIVER=dummy` and does not require a display
server. SDL itself selects Wayland or X11 for normal visible windows.
