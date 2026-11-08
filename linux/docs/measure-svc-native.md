# Measuring the native kernel calls (issue #19)

`NX2LINUX_SVC_NATIVE=1` answers the four kernel calls on mutex words and keys in C (`ArbitrateLock`, `ArbitrateUnlock`, `WaitProcessWideKeyAtomic`, `SignalProcessWideKey`) instead of through Python. It is off by default.
**How many frames per second it gives is unknown.** No Xenoblade 2 run has used it. This page is the protocol that settles it, written before the run so that the answer does not depend on how the run went.

## What the protocol guards against

In Phase 2 a gap of 5 percent between two builds was 1.6 standard errors, and two runs were spoiled by a validation that used 14 processes at the same time. So:

- **The order is A B B A A B B A A B B A.** A drift of the machine, such as heat, cancels inside each block. Three blocks give 6 runs of each variant, the least that is accepted.
- **The machine must be idle before each run.** `tools/ab_bench.py` samples the whole machine's CPU for 5 seconds and waits until it is under 3 percent busy (about half a core on 16), and it does not start while another `horizon_run.py` runs. A run that had to start on a busy machine is listed and left out.
- **One thing changes.** Same binary, same command, same cache, same key presses. Only `NX2LINUX_SVC_NATIVE` differs. `AOT_GROUP` and `AOT_INLINE_MEM` stay unset, so grouping (a possible 5 percent loss) and inline memory accesses (+8 percent measured before) are not mixed in.
- **The loading is left out.** The measure is the frames presented between second 30 and second 110 of the run, read from the `[frames] at 30 s: N` lines that `horizon_run.py` prints when `NX2LINUX_FRAME_MARKS=30,110` is set (the tool sets it).
- **B must really be B.** `--b-must-match` leaves out a B run whose log does not say the C path was used. Without it, a missing library would quietly turn the series into a comparison of A with A.
- **The verdict.** Mean, standard deviation and standard error of each variant, then B minus A and its standard error, the square root of the two squared standard errors. **A difference under two standard errors is not a result**, and the tool says so. The standard deviation is reported even when the result is nothing.

## Before the first run

1. Build the library: `cmake -S linux/host -B ~/.cache/nx2linux/build-host && cmake --build ~/.cache/nx2linux/build-host`. This builds the host libraries, not the game.
2. The change to `aot_runtime.cpp` (step a1) changes the key of the final library in the cache and not the key of any function's object, so the first run relinks the library from the cached objects instead of lifting and compiling them again. **How long the relink takes is unknown.** Do it once outside the measure, with the same cache the series will use:
   ```sh
   "$NX2_PYTHON" -I engine/dm/tools/horizon_run.py --modules "$MODS" --romfs "$ROMFS" --title 0100e95004038000 --jit --build-only --cache "$CACHE"
   ```
   Use the same flags and the same cache folder as your latest ungrouped Xenoblade 2 runs (`--jit` or not). If the cache is not the one that holds the objects, this is the 25 minute build. Say so before starting it.
3. Close what you can: browser, IDE, anything that wakes up by itself. Leave the machine alone while the series runs. Do not start a build or a validation.

## The series

```sh
source ~/.cache/nx2linux/toolchain/env.sh          # or the env.sh you used for the Xenoblade 2 runs
unset AOT_GROUP AOT_INLINE_MEM
export NX2LINUX_GPU=1 NX2LINUX_PRESS='<the key presses of your earlier 120 s series>'
python3 tools/ab_bench.py run \
    --env-a NX2LINUX_SVC_NATIVE=0 --env-b NX2LINUX_SVC_NATIVE=1 \
    --b-must-match 'native synchronisation: 4 kernel calls in C' --a-must-not-match 'native synchronisation' \
    --marks 30,110 --pause 30 --out ~/ab-svc-native \
    -- "$NX2_PYTHON" -I engine/dm/tools/horizon_run.py --modules "$MODS" --romfs "$ROMFS" --title 0100e95004038000 \
       --display --seconds 115 --cache "$CACHE"
```

Keep `--jit` or drop it as in your earlier series, the same on both variants. `--seconds 115` stops the run after the second mark. The key presses have to bring the game to the same scene at the same time in every run, as in the series of `group-objects.md`. They are not in this repository.

**Time.** 12 runs, each about 115 s of play plus the start (the cache is loaded in about 25 s when nothing is rebuilt, from the notes of S7 bis), 30 s of pause and a 5 s sample: about 3 minutes each, **about 35 to 40 minutes** in all. The idle wait adds to it if the machine is not quiet.
**Disk.** A log per run in `~/ab-svc-native` (the whole output of the run) and `ab_bench.json`. The logs of a run with `NX2LINUX_GPU=1` can be large. I have not measured their size, and a few megabytes each is a guess.

Read the table again with `python3 tools/ab_bench.py report ~/ab-svc-native/ab_bench.json`.

## What to note

- The table (the tool prints it at the end and `report` prints it again), and `ab_bench.json`.
- Whether runs were left out and why (`runs left out ...`), and the `busy before` of each run.
- In the log of one B run: the `[svc] native synchronisation: 4 kernel calls in C` line, and the `svc calls:` line at the end of both a B and an A log. The counts per call tell whether `WaitSynchronization` and the events (step a3) are worth porting.
- Anything abnormal: a run that stopped early, `threads that stopped:` that is not empty, a window that stayed black or frozen, an exit code that is not 0.
- The CPU governor and the GPU driver (the tool records the governor), and what else was open.

## Reading the result

- **A difference of two standard errors or more** in favour of B is a measured gain, for these runs, on this machine and this scene. It is not 30 frames per second until the number says so.
- **Under two standard errors** is no result. It does not show that B is as fast as A. It shows these 12 runs cannot tell. Do not run more until you see that the series was clean, then run the same series again and compare.
- **B slower than A by two standard errors or more** is a result too, and the switch stays off.
- The default of `NX2LINUX_SVC_NATIVE` changes only after a result in favour of B and a soak test (issue #22) without a hang.
