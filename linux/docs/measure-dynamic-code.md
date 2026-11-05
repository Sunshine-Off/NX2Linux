# Measuring generated and self-modifying code in a real game (issue #17)

The plan requires proving dynamic code. Until now a block generated through the JIT was tested on fixtures only. This tells you, from a run of
Xenoblade 2, whether the game makes memory executable, and, with the fallback on, whether the JIT is ever entered outside the recompiled modules
(which is what generated code looks like). It builds on the report of #14 (`measure-jit-fallback.md`) and needs the game.

## What is measured

- **Calls that make memory executable.** Horizon now names and records `SetMemoryPermission`, `SetProcessMemoryPermission`, `ControlCodeMemory`, `CreateCodeMemory`,
  `MapProcessCodeMemory` and `UnmapProcessCodeMemory`. A call with the X bit, or one that creates or maps code memory, counts as a request for executable memory.
  The first call of each kind is logged as `[dyncode] ...` with the address of the calling instruction. This works with or without `--jit`.
  The code-memory calls are still refused with `R_NOT_IMPLEMENTED`, as before: naming them changed nothing for the game.
- **Where the JIT was entered** (with `--jit`). Each entry has a target address. A target inside a module image is a function Remill did not lift or an address
  the AOT table did not hold. A target outside every module is code the game generated. The report counts both and lists the largest targets.
- **Invalidations.** `runtime.jit_invalidations` and `runtime.jit_pages` count writes into code the JIT had translated.

## Commands

Free disk: none for the first run (it uses your existing cache). The second run is the #14 run and needs about 12 GB for its cache folder.

```sh
cd <your clone>
source ~/.cache/nx2linux/toolchain-clean/env.sh
MODS=<the folder that holds main.manifest.json, sdk.manifest.json and subsdk0.manifest.json>
ROMFS=<RomFS.img>
```

1. Without the fallback, on the cache you already have. Use the display, GPU and key options of your usual run, and play as long as you can: the longer
   and the more varied the path, the more this means (the first fight, the map, a loading screen, a cutscene).

   ```sh
   python3 -I engine/dm/tools/horizon_run.py --modules "$MODS" --romfs "$ROMFS" --title 0100e95004038000 \
       --seconds 300 --report-json ~/xc2-dyn-aot.json
   python3 -I engine/dm/tools/aot_report.py show ~/xc2-dyn-aot.json
   ```

2. With the fallback, after the build of #14 (cold, about 25 min, **not measured with `--jit`**):

   ```sh
   python3 -I engine/dm/tools/horizon_run.py --modules "$MODS" --romfs "$ROMFS" --title 0100e95004038000 \
       --jit --seconds 300 --cache ~/.cache/nx2linux/aot-xc2-jit --report-json ~/xc2-dyn-jit.json
   python3 -I engine/dm/tools/aot_report.py show ~/xc2-dyn-jit.json
   ```

## How to read it

| What you see | What it means |
|---|---|
| `calls that make memory executable: 0 (none)` in both | the game asked the kernel for no executable memory on that path. It proves nothing about other paths |
| `SetMemoryPermission (X)` or a code-memory call | the game makes memory executable. The `[dyncode]` line gives the address of the call. Note it down, this is the case #17 is about |
| `0 places outside the modules` with `--jit` | the JIT was never entered outside the recompiled code on that path: no generated code was run |
| places outside the modules, with entries | generated code ran in the JIT. The targets and their counts are in `extra.jit_targets`. Send them back |
| `fallback_requests` above 0 with `jit_fallback` of 0 | indirect transfers to addresses the AOT table did not hold. Look at `jit_targets` to tell a generated block from a missed switch case |
| `jit_invalidations` above 0 | the game wrote into code the JIT had translated: self-modifying or regenerated code |

**What is already proved on fixtures** (`test_aot_dynamic_code.py`): a thread that ran generated code sees a rewrite made by another thread, whether the rewrite
is a store of guest code or a write by the harness, and the cache maintenance sequence a JIT emits (`dc cvau`, `dsb`, `ic ivau`, `isb`) runs. With the cross-thread
invalidation turned off on purpose, the first test fails (it saw the stale code 7 six times instead of 7, 9, 7, 300, 1, 2). `blocages-connus.md` item 5 and the audit still list this as an open defect: the
code (`aot_jit.cpp`, `hypotheses.md` H15 and H22) and these tests say it was fixed in T5. Item 6 of that note (`ret` ignores the value of x30) is a different question and is not covered here.

## What to send back

- The two JSON files (they hold addresses and counts, no game data) and the `[dyncode]` lines of the logs.
- How long you played and what you did, because a clean report on the title screen means little.
