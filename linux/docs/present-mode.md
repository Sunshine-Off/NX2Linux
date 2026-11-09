# Is the display pacing the frame rate? (issue #19)

Three different builds of the host run Xenoblade 2 at nearly the same rate in the A B B A series of the team lead: 17.88 fps (native calls, `--jit`), 17.79 fps (native calls, no `--jit`) and 17.86 fps (native calls, inline memory accesses), with a standard deviation of 0.04 to 0.19 fps. Inline memory accesses remove the memory accessors that the stack samples show as the hottest code, and they changed the rate by 0.07 fps. When a speed-up of the code does not move the frame rate, the frame rate is probably set by something else than the code.

**One candidate is known.** The window's swapchain uses the present mode FIFO (`gpu.c`, `VK_PRESENT_MODE_FIFO_KHR`), so a present waits for the display refresh. If the pipeline blocks on it, a frame takes a whole number of refresh periods and the rate lands between two of them. That is a deduction. It has not been tested, and other things could also set the rate (the game's own frame limiter, the audio tick, the way the host signals the end of a frame).

`NX2LINUX_PRESENT_MODE=immediate` (or `mailbox`, `relaxed`) asks for another present mode. It is **opt-in and never the default**. A mode that the surface does not support falls back to FIFO and the log says so. With `immediate` the picture can tear. This is for a measurement.

## The test

The variables of `measure-svc-native.md` and `~/xc2-run.sh` are set (`source ~/xc2-env.sh`). `AOT_INLINE_MEM` stays 0 here, so the cache is the one you already have. Rebuild the library first: `cmake --build ~/.cache/nx2linux/build-host`.

**Look at your screen's refresh rate first** (the display settings of your desktop) and note it. It is needed to read the result.

A short look, 60 s each, without touching the window:

```sh
for M in fifo immediate; do
  XC2_SECONDS=60 AOT_INLINE_MEM=0 NX2LINUX_SVC_NATIVE=1 NX2LINUX_PRESENT_MODE=$M bash ~/xc2-run.sh > ~/pm-$M.log 2>&1
done
grep -H -E "present mode|frames presented" ~/pm-fifo.log ~/pm-immediate.log | cut -c1-120
```

You must see `[gpu] present mode: fifo` and `[gpu] present mode: immediate` (or `fifo (the surface does not support ...)`, which means this surface does not offer it: try `mailbox`).

The series, if the short look shows a difference (35 to 40 minutes, same rules as before):

```sh
python3 tools/ab_bench.py run \
  --env-a NX2LINUX_PRESENT_MODE=fifo --env-a AOT_INLINE_MEM=0 --env-a NX2LINUX_SVC_NATIVE=1 \
  --env-b NX2LINUX_PRESENT_MODE=immediate --env-b AOT_INLINE_MEM=0 --env-b NX2LINUX_SVC_NATIVE=1 \
  --b-must-match 'present mode: immediate' --a-must-not-match 'present mode: immediate' \
  --marks 30,110 --pause 30 --out ~/ab-present -- bash ~/xc2-run.sh
```

## How to read it

- **A clearly higher rate with `immediate`.** The display pacing was a limit, and the real speed of the host is above what the series showed. The gain of every earlier step has to be read again with this on.
- **The same rate.** The display is not the limit. Something else is, and the stack samples of #77 are where to look.
- **A rate that does not follow the refresh rate.** The two numbers say how many refresh periods a frame takes on average (refresh rate divided by the frame rate), and that is the quantity to reason on.
