# NX2Linux CLI

The S5 command line interface is independent of Qt and drives the Linux host
build and Vulkan smoke tests.

From the repository root:

```sh
python3 linux/cli/nx2linux.py doctor
python3 linux/cli/nx2linux.py build -j2
python3 linux/cli/nx2linux.py verify --require-vulkan
python3 linux/cli/nx2linux.py run
```

`verify` accepts a skipped Vulkan test by default so it can run in headless CI.
`--require-vulkan` turns that skip into a failure, which is useful on a real
desktop or GPU test runner.

Verification uses the CTest JUnit report (CTest 3.21+), not console text. Native
module, SDL and demo exit-status checks must execute in the default graphics
configuration even without a display. With `--require-vulkan`,
the Vulkan triangle test must also execute successfully. Empty suites, missing
expected tests, invalid reports and CTest failures are errors. Each test has a
60-second timeout. Output streams live to the terminal or the S6 Qt interface.

The Linux Qt interface is launched with `python3 frontend-qt/run_gui.py` from the
repository root. Its Verify action always supplies `--require-vulkan`. See
[the frontend guide](../../frontend-qt/README.md#linux-host-interface-s6).

## Native host and strict validation

```sh
python3 linux/cli/nx2linux.py build --headless --config Release -j2
python3 linux/cli/nx2linux.py verify --headless
SDL_VIDEODRIVER=wayland python3 linux/cli/nx2linux.py verify --require-validation
```

`--headless` builds only the native fixture host and uses `build/linux-headless`
by default. Its verification requires both native tests and no graphics tests.
It cannot be combined with `--require-vulkan` or `--require-validation`.
The final command uses the graphics build directory and requires a working
Khronos validation installation, including synchronization validation. It implies
`--require-vulkan`. Missing layers, skipped tests and validation errors are failures.

Doctor now checks the C compiler (`CC` or `cc`) and executable CMake/CTest >= 3.21,
not only whether their binaries exist. A successful doctor is a dependency probe,
not evidence that a game can compile or run. Headless builds do not require the
graphics dependencies reported by the default doctor command.
