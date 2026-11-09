# Where the time goes: stack samples with gdb (issue #19)

The native kernel calls give 17.9 frames per second against 16.3, and the target is 30. Before changing anything else, this finds what the threads are doing. `perf` is not installed, and the host spends its time in places Python's profiler cannot see
(the GIL, ctypes, the driver). So `tools/sample_stacks.py` does what S8 did by hand: it attaches gdb to the running host a number of times, takes the stack of every thread, and counts.

**What it gives.** For each sample, every thread is either waiting (futex, condition, sleep, poll), waiting for the GIL, or running. Running threads are split by what they run: the recompiled game code, the AOT runtime, Python and ctypes, the host's GPU library,
the audio, movie, window and sync libraries, the Vulkan driver, other. It also lists the functions they are in and each kind of thread apart (`guest-*`, `nvn-queue`...).

**What it does not give.** A sample is where threads are at one instant. 14 samples of about 40 threads is a picture, not a measurement. Read the shares as orders of magnitude, as S8 did, and sample the variant you compare against (A) the same way.
**Each sample stops the game** for as long as gdb takes, and the table says how long. A run that is sampled is not a run to take a frame rate from.

## Before

- gdb must be installed (`gdb --version`). I did not install it. On Fedora it is `sudo dnf install gdb`, your decision.
- gdb must be allowed to attach. Look at `cat /proc/sys/kernel/yama/ptrace_scope`: 0 lets you attach to your own processes, 1 only to your own children. If it is 1 and the tool says it was refused, it does not change anything for you.
  The setting is yours to change.
- Build the library again after pulling (`cmake --build ~/.cache/nx2linux/build-host`), the interface changed in #76.
- Close what you can and leave the machine alone, as for the series. The window of the game must stay open.

## The procedure

Two terminals. In both, the variables of `measure-svc-native.md` (`source .../toolchain-p2/env.sh`, `MODS`, `ROMFS`, `CACHE`, `NX2LINUX_GPU=1`, `NX2LINUX_PRESS`).

**Terminal 1**, the game, B first. `--seconds 400` keeps it alive long enough:

```sh
NX2LINUX_SVC_NATIVE=1 "$NX2_PYTHON" -I engine/dm/tools/horizon_run.py --modules "$MODS" --romfs "$ROMFS" \
  --title 0100e95004038000 --display --jit --seconds 400 --cache "$CACHE" > ~/run-B.log 2>&1
```

**Terminal 2**, when the opening scene is playing, at about 45 s after you started (the window of the series was 30 to 110 s):

```sh
python3 tools/sample_stacks.py run --match horizon_run.py --samples 14 --interval 5 --out ~/stacks-B
```

It prints one line per sample, then the report, and writes `~/stacks-B/report.txt` and `report.json`. About 14 samples at 5 s apart plus the time gdb holds the process: a few minutes. Let terminal 1 end (or close its window) afterwards.

Then the same with the Python path for reference. `NX2LINUX_SVC_NATIVE=0` in terminal 1, `--out ~/stacks-A` in terminal 2.

If `--match` finds more than one process, it lists them. Give `--pid` instead.

## What to send back

`~/stacks-B/report.txt` and `~/stacks-A/report.txt`, and any line starting with `[sample]` that shows a gdb time much longer than the others. The sample files themselves hold the stack of every thread with paths of your machine. Read them before sharing.

## How to read it

- **Many threads waiting for the GIL.** The bridge to Python is still the limit. The next step is another port to C, chosen by the functions the running threads are in.
- **Many threads running Python and ctypes.** The same, with the Python work itself.
- **A lot of running time in the recompiled game code.** The inline memory accesses (`AOT_INLINE_MEM=1`, +8 percent measured before) are worth their recompilation of about 25 minutes.
- **Most threads waiting, few running, little GIL.** The game waits on itself, as S8 found in its first look. Look at which kind of thread waits, and at the functions in the waits table.
- **Time in the Vulkan driver or the host GPU library, mostly in the `nvn-queue` thread.** The limit is the drawing, not the kernel calls.
