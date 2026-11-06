# NX2Linux CLI

The S5 command line interface is independent of Qt and drives the Linux host
build and Vulkan smoke tests.

From the repository root:

```sh
python3 linux/cli/nx2linux.py doctor
python3 linux/cli/nx2linux.py build-host -j2
python3 linux/cli/nx2linux.py verify --require-vulkan
python3 linux/cli/nx2linux.py run
python3 linux/cli/nx2linux.py cache-prune <an AOT cache folder>     # lists the old finished libraries, --delete removes them
```

`doctor` also checks that the interpreter of the engine workers imports `capstone`,
`unicorn` and `lz4`. The workers run with `python3 -I`, which ignores the user site,
so these packages must live in a venv. `tools/setup_toolchain.sh` makes it
(`~/.cache/nx2linux/venv`, or `NX2_VENV`) from `tools/linux-requirements.txt` and
exports `NX2_PYTHON` in `env.sh`.

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

## Building a game

```sh
python3 linux/cli/nx2linux.py build "<game>.nsp" --keys <prod.keys or its folder>
```

One command turns a package into a build directory, `<cache>/build/<title ID>` (or `--out`, never inside the repository):
`ExeFS/`, `RomFS.img`, `modules/` (one ELF and manifest per NSO), `build.json` (the build manifest) and the AOT cache.
The steps are check, extract, modules, manifest and recompile. A step whose inputs and outputs are unchanged is skipped
on the next run, `--force` runs them all again, `--stop-after <step>` and `--skip-recompile` stop early, `--json` prints
the progress events as JSON lines. The first recompile of a large game takes about 25 minutes and about 12 GB of cache.

A missing prerequisite stops the build with exit status 3 and a message that names it and the fix: `keys`, `dotnet`,
`engine`, `worker-python`, `remill` (`REMILL_BUILD`) and, with `--jit`, `dynarmic` (`DYNARMIC_DIR`). `doctor` lists the
same gaps. `tools/setup_toolchain.sh` writes an `env.sh` that exports both variables. The library is
`linux/cli/nx2linux_build.py`, shared with the Qt interface.

The SDL3 and Vulkan host demo used to be `build`. It is now `build-host`.
