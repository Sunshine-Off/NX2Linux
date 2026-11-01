# NX2Linux native and graphics hosts

## Native headless foundation (S1a)

The native executable loads a trusted ELF fixture without SDL, Vulkan or a
display. It is not a Switch runtime or a recompiler. See [the native contract](NATIVE_ABI.md).

```sh
python3 linux/cli/nx2linux.py build --headless -j2
python3 linux/cli/nx2linux.py verify --headless
./build/linux-headless/nx2linux_headless --module "$PWD/build/linux-headless/libnative_fixture.so"
```

This configuration requires Linux x86_64, CMake/CTest 3.21+, a C compiler and
POSIX threads. The native executable and fixture have no SDL/Vulkan dependencies.
The CI workflow `linux-host.yml` runs the native tests in Debug and Release.

## Graphics foundation (S2–S4a)

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

The SDL host opens existing and hot-plugged gamepads, closes them on removal and
shutdown, and forwards button/axis input. Tests use an actual virtual SDL gamepad,
not just injected button events. A physical controller still needs acceptance testing.

For a strict desktop check, do not rely on plain CTest's treatment of skipped tests:

```sh
SDL_VIDEODRIVER=wayland python3 linux/cli/nx2linux.py verify --require-vulkan
SDL_VIDEODRIVER=wayland python3 linux/cli/nx2linux.py verify --require-validation
```

The second command requires `VK_LAYER_KHRONOS_validation`, debug utils and the
validation-features extension. It enables synchronization validation and fails
on missing validation support or any reported validation error. The direct
renderer equivalent is `NX2LINUX_VK_VALIDATION=1`.

The renderer uses one graphics frame in flight and a presentation semaphore per
swapchain image. Size-only resizes retain the pipeline when the surface format
does not change. Resizes wait for the device to become idle. This is a correctness
baseline, not a benchmarked high-performance game renderer. There is no guest
shader translation, texture system, depth rendering or NVN command integration.

The native module lifecycle, SDL input and demo exit-status regressions do not
need GPU access. Vulkan still requires a working display and driver. A renderer
initialization failure is now an error, not an ignored test. An unavailable SDL
Vulkan window may be skipped only in non-strict test mode.
