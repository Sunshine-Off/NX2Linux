# S1–S6 remediation and handoff

## Scope and status

The earlier S1 completion claim was incorrect. Frontend paths and tool discovery
were not a native runtime host. The earlier S3/S4 completion claims also covered
only a synthetic triangle, not a Switch graphics implementation.

This remediation supplies a native headless foundation and fixes concrete SDL,
Vulkan and validation defects. It does not create the missing guest runtime or
game renderer under a new name. S1b and S4b remain explicit integration work.

## Changes by milestone

### S1a — Native POSIX foundation

- Added `nx2linux_headless` and a graphics-independent CMake configuration
- Loads a trusted native ELF module through a versioned descriptor
- Allocates zeroed read/write scratch memory with inaccessible guard pages
- Provides monotonic time and logging callbacks
- Tests native pthreads, TLS, mutexes, unload/reload and failure propagation
- Rejects missing modules and incompatible ABI versions
- Checked executable and fixture are x86_64 ELF, with no graphics dependency

The [native contract](../linux/host/NATIVE_ABI.md) is a fixture boundary, not the
Switch ABI. Modules are trusted native code, not sandboxed. Guest address mapping,
ARM64 TLS, SVC, exception semantics and game loading belong to the agreed runtime
integration, S1b. Do not confuse native pthread tests with ARM64 conformance tests.

### S2 — Window and gamepads

- Opens existing and newly attached gamepads and closes them on removal/shutdown
- Tests a virtual controller's button press/release, axis, detach and reconnect
- Preserves initialization errors across SDL cleanup
- Recognizes pixel-size change events as well as logical resize events

Physical controllers and diverse desktop configurations remain acceptance checks.

### S3/S4a — Vulkan demo correctness

- Command pool explicitly permits individual command-buffer reset
- Checks reset/record/submit errors and prevents further draws after fatal submission errors
- Uses one presentation semaphore per acquired swapchain image
- Adds the external render-pass dependency for color attachment writes
- Handles zero/minimized drawable sizes as suspended, not successful rendered frames
- Clamps extent to supported bounds and selects supported composite alpha
- Keeps the pipeline on size-only resize instead of reloading shaders every time
- Reports resize failures as nonzero process exits, including through CLI/Qt
- Adds five no-GPU exit-status scenarios against the real demo main with test stubs
- Adds opt-in Khronos core and synchronization validation with error accounting
- Initialization errors after SDL surface setup are no longer silently skipped

There is still one graphics frame in flight and a device-idle wait on swapchain
recreation. Correctness comes before adding concurrency. No frame-rate improvement
or emulator-performance comparison is claimed. S4b still needs guest graphics
commands, resources, shaders and synchronization. That is substantial work.

### S5 — Build, diagnostics and verification

- Adds `build --headless` and `verify --headless` with a separate default directory
- Requires the native tests plus SDL/demo tests in the graphics configuration
- Adds `verify --require-validation`, which also requires Vulkan to execute
- Checks compiler and runnable CMake/CTest >= 3.21 in doctor
- Keeps build testing enabled and explicitly selects graphics ON/OFF
- Moves the visible demo out of the `BUILD_TESTING` conditional

### S6 and automation

- Existing Qt interface remains a host-demo interface, with no fictitious game stages
- Rechecks the Qt/CLI path against the corrected host
- Adds Linux CLI/native pre-push checks without requiring a local GPU
- Adds Linux native Debug/Release CI without SDL/Vulkan dependencies
- Updates Swann, Téo and the global plan to distinguish delivered facilities from
  pending guest integration

## Test evidence

The following were exercised locally, separately:

- Qt: 234 tests passed
- CLI: 14 tests passed
- Repository tools: 124 tests passed
- Native-only Debug build: 2 CTest checks passed without SDL/Vulkan
- Native ASan/UBSan build: 2 checks passed with leak detection disabled
- Graphics Debug and Release: 5 CTest checks passed on NVIDIA RTX 4070 / Wayland,
  with no skipped Vulkan test in strict `--require-vulkan` mode
- Linux pre-push helper: CLI tests and disposable native build passed
- Real Qt -> CLI -> doctor/build/verify/run: passed on the corrected Debug host
- Localization generation: unchanged

These are bounded checks, not proof of absence of bugs. Native AddressSanitizer
does not prove correctness of the Vulkan driver or the future guest runtime.

## Open environment gates

Khronos validation is absent on this machine. The strict validation check correctly
fails with `VK_ERROR_LAYER_NOT_PRESENT`, rather than claiming success.

The installed lavapipe driver fails to load because `libLLVM.so.23.1` is missing.
This was confirmed by `vulkaninfo` with the lavapipe ICD selected. No system package
was installed or changed during this work. Validation-layer cleanliness and a
successful lavapipe run remain OPEN. The added native-only CI does not close them.

On Arch Linux, the user can repair the package set with:

```sh
sudo pacman -Syu vulkan-validation-layers vulkan-swrast llvm-libs vulkan-tools
```

This performs a full system upgrade as well as installing the listed packages.
Review the transaction and restart the graphical session or reboot if driver
updates require it. Do not fake a missing LLVM version with a library symlink.
Package references: [lavapipe](https://archlinux.org/packages/extra/x86_64/vulkan-swrast/)
and [validation layers](https://archlinux.org/packages/extra/x86_64/vulkan-validation-layers/).

## Commands from the repository root

```sh
python3 tools/check_linux.py
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s frontend-qt/tests
python3 -m unittest discover tools/tests

python3 linux/cli/nx2linux.py build --build-dir build/jalons-debug --config Debug -j4
SDL_VIDEODRIVER=wayland python3 linux/cli/nx2linux.py verify --build-dir build/jalons-debug --require-vulkan

python3 linux/cli/nx2linux.py build --build-dir build/jalons-release --config Release -j4
SDL_VIDEODRIVER=wayland python3 linux/cli/nx2linux.py verify --build-dir build/jalons-release --require-vulkan
```

After repairing system dependencies, both of these must pass before closing the
strict S3/S4a validation gate:

```sh
SDL_VIDEODRIVER=wayland python3 linux/cli/nx2linux.py verify --build-dir build/jalons-debug --require-validation
SDL_VIDEODRIVER=wayland VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json python3 linux/cli/nx2linux.py verify --build-dir build/jalons-debug --require-validation
```

For a live demo:

```sh
python3 linux/cli/nx2linux.py run --build-dir build/jalons-debug --backend wayland
```

## Git handoff

No commit, branch change or push was performed. Create a dedicated repair branch
from the current S6 commit, stage only the listed paths, inspect the diff, then
commit and push normally:

```sh
git switch -c swann/fix-s1-s6-foundations
git add -- linux/host linux/cli tools/check_linux.py tools/githooks.py tools/tests/test_githooks.py .github/workflows/linux-host.yml "fork en cours/Solution.md" "fork en cours/Swann.md" "fork en cours/Teo.md" "fork en cours/S6-validation.md" "fork en cours/Jalons-remediation.md" "fork en cours/S7-integration.md"
git diff --cached --stat
git diff --cached --check
git commit -m "Linux host: repair S1-S6 foundations and validation gates"
git push -u origin swann/fix-s1-s6-foundations
```

The pre-push native check is not a substitute for the strict graphics check above.
Use a PR, never push directly to main and never bypass hooks.

## T7 / S7 handoff

Retrieved `origin/feat/teo-t7-module-reel` at commit
`426d040bdf4ed2737605a314d109ab40ab049c1a` without merging or changing the working
branch. Read its T6, T7 and known-blockers reports. These reports describe real
module builds and differential function tests, explicitly not game startup.
The native HLE adapter and real SVC dispatch are not connected. No real game test
was rerun here. See [the S7 handoff](S7-integration.md).
Do not commit game binaries, extracted content or keys.

Start S7 with artifact inspection and a reproducible headless execution test.
Compilation, loading, executing guest code, drawing a frame and being playable are
different acceptance stages. The native fixture host must not silently replace
Téo's runtime. Graphical S7 acceptance still requires the S4b game graphics path.
