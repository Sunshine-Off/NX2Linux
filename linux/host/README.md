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

## OpenGL on Vulkan

Some games render with OpenGL, not NVN: Balatro's LOVE 11.4 asks for a core profile
context through EGL. `gl_vulkan.c` answers it with a GL 4.5 core context that Mesa
Zink runs on Vulkan, so the draws reach the same Vulkan driver as the renderer
above. The context is surfaceless: the host renders into a framebuffer object and
presents that. `nx2linux_gl_get_proc` is the resolver for the guest's GL imports,
and `nx2linux_gl_required` lists the functions a core-profile LOVE reaches.

The `nx2linux_gl_on_vulkan` test checks that the renderer is Zink, that every listed
function resolves, and that a GLSL 330 core shader draws the expected pixel. It
needs Mesa's EGL and Zink; without them it skips (exit 77). On a machine with no
GPU the layer allows lavapipe (`allow_software`). The Metal layer
(`engine/dm/gfx/gl/hle_gl.m`) is a behavioural reference only.

## Extracting a game and the smoke test (S7)

```sh
python3 linux/cli/nx2linux.py extract "<game>.nsp" --keys <prod.keys or its folder>
python3 linux/cli/nx2linux.py smoke --runs 3 --title-id <id> -- <host command>
```

`extract` needs `dotnet` and writes to the XDG cache, never inside the repository.
`smoke` starts the host command several times and fails on a non-zero exit or any
`[RED]` line; its report holds the title ID, version, profile, GPU and driver, and
no game data.
