# NX2Linux SDL host

This directory contains the SDL3 host layer and the first Vulkan renderer for
Linux x86_64. The host owns the SDL3 window and translates SDL keyboard,
gamepad, resize and quit events into a stable NX2Linux event contract.

The Vulkan layer creates the SDL surface, selects a graphics/present queue,
creates a swapchain and renders a shader-generated triangle. It is deliberately
isolated from the future NVN translation layer.

## Build and test

Dependencies are SDL3 development headers, CMake, a C compiler and `pkg-config`.
The triangle test also needs Vulkan headers, a Vulkan loader, a working video
backend and `glslc`. Mesa lavapipe is recommended for headless CI.

```sh
cmake -S linux/host -B build/linux-host -DCMAKE_BUILD_TYPE=Debug
cmake --build build/linux-host
ctest --test-dir build/linux-host --output-on-failure
```

To open a visible window and inspect the triangle manually:

```sh
./build/linux-host/nx2linux_vulkan_demo
```

The demo runs for ten seconds or until the window is closed.

The SDL smoke test uses `SDL_VIDEODRIVER=dummy` and does not require a display
server. The Vulkan triangle test requires a real SDL video backend because a
Vulkan surface is platform-owned. SDL itself selects Wayland or X11 for normal
visible windows.
