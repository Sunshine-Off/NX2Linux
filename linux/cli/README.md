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

Verification uses the CTest JUnit report (CTest 3.21+), not console text. The SDL
smoke test must execute successfully even in headless mode. With `--require-vulkan`,
the Vulkan triangle test must also execute successfully. Empty suites, missing
expected tests, invalid reports and CTest failures are errors. Each test has a
60-second timeout. Output streams live to the terminal or the S6 Qt interface.

The Linux Qt interface is launched with `python3 frontend-qt/run_gui.py` from the
repository root. Its Verify action always supplies `--require-vulkan`. See
[the frontend guide](../../frontend-qt/README.md#linux-host-interface-s6).
