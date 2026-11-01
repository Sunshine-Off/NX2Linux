# S6 — Linux Qt host integration

## Delivered scope

The Linux entry point now selects `LinuxWindow` and `LinuxBackend`. macOS and
Windows retain the Apple workflow. Linux calls the S5 CLI with argument lists,
without a shell, using asynchronous QProcess operations.

- Diagnose, build, strict verification and Vulkan demo launch
- Checkout, build directory, Debug/Release and SDL display selection
- Live output, operation status and indeterminate progress
- Persistent per-operation logs and XDG settings
- Single-operation guard, Stop and asynchronous close with child-group cleanup
- Structured diagnostic rows and visible failure handling

PySide6 6.7+ is required for creating a separate child process session. Verification
uses CTest 3.21+ JUnit reports and refuses missing, skipped or failed required tests.
The SDL smoke test now retains its assertions in Release builds. Previously,
`NDEBUG` removed both assertions and setup calls, producing an ineffective test.

## Validation performed

Environment: Linux x86_64, Python 3.14.6, CMake 4.4.2, SDL 3.4.14, Wayland and
NVIDIA GeForce RTX 4070. Qt widget tests use the offscreen platform.

| Check | Result |
|---|---|
| S6 tests, run separately with verbose output | 17 passed |
| Complete Qt suite, including S6 | 234 passed |
| CLI suite, run separately | 10 passed |
| Repository tools suite | 124 passed |
| Real Qt → CLI → doctor/build/verify/run, Debug | Passed |
| Real Qt → CLI → doctor/build/verify/run, Release | Passed after fixing SDL assertions |
| Real CTest SDL and Vulkan checks in both configurations | 2 passed, none skipped |
| Strict verify with SDL dummy and unavailable Vulkan | Rejected with exit 1 as expected |
| Localization regeneration | 1047 strings, generated file unchanged |
| Whitespace check | Passed |

S6 tests cover entry-point routing without loading the Apple model, live UTF-8
output, argument handling with spaces, strict verification arguments, diagnostic
errors, nonzero exits, failed process startup, log I/O errors, persisted settings,
relative XDG overrides, immediate cancellation, a child ignoring SIGTERM, and
window close during an operation.

The existing Windows negative tests deliberately print `FAIL:` while checking
invalid source layouts. The unittest result is `OK`. These messages are not suite
failures.

## Reproduce

From the repository root:

```sh
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s frontend-qt/tests -p test_linux_frontend.py -v
python3 -m unittest discover -s linux/cli/tests -v
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s frontend-qt/tests
python3 -m unittest discover tools/tests
python3 frontend-qt/gen_localization.py
git diff --exit-code HEAD -- frontend-qt/nspb_qt/localization.py
```

Desktop/GPU checks, each with real SDL/Vulkan access:

```sh
QT_QPA_PLATFORM=offscreen python3 frontend-qt/tests/check_linux_workflow.py --backend wayland
QT_QPA_PLATFORM=offscreen python3 frontend-qt/tests/check_linux_workflow.py --backend wayland --config Release --build-dir build/s6-qt-release
```

The first command uses `build/s6-qt-validation`. Logs remain under each build
directory's `qt-test-logs/`. These directories are ignored by Git.

For an interactive acceptance check:

```sh
python3 frontend-qt/run_gui.py
```

## Boundaries and next milestone

This delivers the S6 interface for the existing host demo. It does not complete
the original plan's game-oriented frontend stages. No game recompiler, guest
loader, AOT/JIT progress or NVN-to-Vulkan integration has been added. S7 requires
Téo's runtime/artifact contracts and further game graphics integration on Swann's
side. A triangle is not evidence of Switch game rendering feasibility by itself.

This run did not validate X11, lavapipe, other GPUs, Windows/macOS execution,
Vulkan validation-layer cleanliness or rendered pixels against a reference.
Widget layout was inspected from a captured image. Interactive keyboard/window
behavior still merits a user acceptance check. Passing tests do not guarantee the
absence of bugs or establish the correctness of the complete existing renderer.

Branch: `swann/jalon-s6-qt-linux`.

Suggested commit: `Linux frontend: integrate S6 Qt host workflow`.

No commit or push was performed.
