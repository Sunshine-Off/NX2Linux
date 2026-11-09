# Long runs, clean exit and stalls (issue #22)

A long run of Xenoblade 2 has to end the way the team decides, and has to leave numbers behind. This page is what the host does for that, and the runs to do on the game. **No Xenoblade 2 run has used any of it yet.** What works is checked with fake components and with real signals sent to a child process, not with the game.

## What the host does

| Variable or event | What happens |
|---|---|
| The end of `--seconds`, `Ctrl-C` (SIGINT), `kill` (SIGTERM) | The same steps in the same order: the GPU queue drains (5 s at most), the report is printed (30 s at most), the audio mixer is closed (5 s), the last soak line is written. Each step says `ok`, `failed: ...` or `still running after N s, left behind` on a `[shutdown]` line. A step that blocks does not stop the others |
| Exit code | 0 at the end of `--seconds`, 130 after Ctrl-C, 143 after SIGTERM (128 plus the signal) |
| A second Ctrl-C while it shuts down | Leaves at once with the same code, without waiting for the steps |
| `NX2LINUX_SOAK_LOG=FILE` | Appends one line to FILE every `NX2LINUX_SOAK_INTERVAL` seconds (10 by default, 1 at least): time into the run, resident memory in kB, threads, open files, frames presented, frames per second since the last line, seconds since the last new frame, guest threads that stopped, stall flag |
| `NX2LINUX_STALL_SECONDS=N` | One `[stall]` message with where each guest thread is, when no new frame was presented for N seconds. Only after the first frame, so loading is never a stall. Once per stall, and a `[stall] frames again` line when they resume. It reports and does not stop the run |

Both variables are off unless set. The file is written line by line and flushed, so a run that dies keeps everything up to its last line. `python3 tools/soak_summary.py FILE` turns it into numbers.

What is **not** known: how much memory, threads or files a long run of Xenoblade 2 uses, whether it stalls, and what the log of an hour weighs.

## Before the runs

1. Get the engine of this change: `git switch shikosu/p3-d-shutdown-soak` and `python3 tools/engine.py sync`. If the pull request is merged, `git switch main`, `git pull` and the same `sync`.
2. The change is in the host and not in the generated code, so **no recompilation is expected** (a deduction, not a check). If the run starts building functions, stop it and tell me.
3. `source ~/xc2-env.sh` in every new terminal, as before. Close what wakes up by itself, and leave the machine alone.
4. Use `tee -i`, not `tee`. Without `-i`, Ctrl-C also kills `tee` and the end of the log is lost.

## Run 1: the exit, about 6 minutes in all

Two short runs. The scene does not matter.

**1a, Ctrl-C.** Start it, wait about 90 seconds, press `Ctrl-C` once in the terminal.
```sh
source ~/xc2-env.sh
XC2_SECONDS=300 bash ~/xc2-run.sh 2>&1 | tee -i ~/exit-ctrlc.log
echo "exit=${PIPESTATUS[0]}"
```

**1b, SIGTERM.** Start it, wait about 90 seconds, and from another terminal send `pkill -TERM -f engine/dm/tools/horizon_run.py`.
```sh
source ~/xc2-env.sh
XC2_SECONDS=300 bash ~/xc2-run.sh 2>&1 | tee -i ~/exit-term.log
echo "exit=${PIPESTATUS[0]}"
```

**Expected:** `exit=130` and `exit=143`, a `[shutdown] signal SIGINT: stopping` line (`SIGTERM` for 1b), then `[shutdown] gpu queue drain`, `report` and `audio mixer` lines each with `ok`, then `[shutdown] done in ... s`, and the usual report between them. The window closes (deduced from the exit, not seen).
**Note:** the exit code, the `[shutdown]` lines (`grep shutdown ~/exit-ctrlc.log`), the time between the key press and the prompt, any step that is not `ok`, and whether a window or a process stayed behind (`pgrep -fl horizon_run`).

## Run 2: one hour

```sh
source ~/xc2-env.sh
export NX2LINUX_SVC_NATIVE=1 NX2LINUX_SOAK_LOG=$HOME/soak-1h.csv NX2LINUX_STALL_SECONDS=60
XC2_SECONDS=3600 bash ~/xc2-run.sh 2>&1 | tee -i ~/soak-1h.log
echo "exit=${PIPESTATUS[0]}"
python3 tools/soak_summary.py ~/soak-1h.csv
```

`NX2LINUX_SVC_NATIVE=1` is on purpose: the point of this run is to see whether the C kernel calls are stable for an hour. `NX2LINUX_PRESS` presses A once at 45 seconds, and after that the game is in whatever scene it reached. Play it normally with the keyboard or the pad, or leave the character standing somewhere, and **write down which**. A run that plays is a harder test than one that waits at a menu.

**Time:** one hour and a few minutes. **Disk:** the file is about 25 kB (360 lines of about 70 bytes). The log of an hour is unknown, check `du -h ~/soak-1h.log` once after the first 10 minutes and tell me if it grows by more than a few MB a minute.
**Note:** the exit code, the whole output of `soak_summary.py`, `grep -c '\[stall\]' ~/soak-1h.log` and the stall lines themselves, the last 40 lines of the log, `threads that stopped:`, and what you did in the game.

## Run 3: four hours

Only after run 2 is clean. The numbers below are proposals for the team to change, and nobody has decided them:
- no `[stall]` line, `threads that stopped: []` and an exit code of 0,
- the memory slope in `soak_summary.py` small against the memory in use (one hour of data cannot show a slow leak, so a small slope is not a proof),
- no step of the shutdown that is not `ok`.

The same commands with `XC2_SECONDS=14400` and `soak-4h` in the names. **Time:** four hours and a few minutes, the machine kept awake (suspend off). **Disk:** about 100 kB for the file, the log is unknown.

If run 2 or run 3 stalls or loses a thread, do the same run with `NX2LINUX_SVC_NATIVE=0`. It is the only way to tell whether the C calls are the cause. One stall in one run is a lead and not a result.

## What the runs decide

- Whether `NX2LINUX_SVC_NATIVE` becomes the default (it is 0 now, see #19 and #79). That is a decision for the team after run 3, it does not follow from this page.
- Whether `NX2LINUX_STALL_SECONDS` should be on by default and with which value. 60 seconds is a first guess.

## What is not covered

- A crash in a host library (a segmentation fault in the GPU or audio library) is not a clean shutdown. It is the subject of the crash reports of #23.
- A stall is only reported. Stopping the run or restarting a thread on a stall is not done.
- Memory of the guest and of the GPU, which the process counter does not separate. The resident memory is the whole process.
