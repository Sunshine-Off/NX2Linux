# Measuring the JIT fallback on a full Xenoblade 2 build (issue #14)

The dynarmic fallback is proved by unit tests only. This measures it on the real game: how many functions stay native,
how many run in the JIT, and how often the running crosses from the recompiled code into the JIT and back.
It needs the game, so it is not part of the tests. Nothing here is run by CI.

## What changes with `--jit`

- The cache key changes, so every function is lifted and compiled again. Use a cache folder of its own and your
  other caches stay as they are. A cold build took 1,430 to 1,541 s on 16 cores and the cache is about 12 GB
  (`S8` section 10 and `S9` section 7). The time for this build is unknown until you run it.
- The inline memory accesses are off with the JIT, because the JIT's write hook must see every store. The run is
  therefore slower than an `AOT_INLINE_MEM=1` run, and the numbers of frames are not comparable with it.
- A function Remill cannot lift runs in the JIT instead of stopping its thread with its address.

## Commands

Free disk needed: about 12 GB for the new cache. Check with `df -h ~/.cache`.

```sh
cd <your clone>
source ~/.cache/nx2linux/toolchain-clean/env.sh      # REMILL_BUILD and DYNARMIC_DIR (use the toolchain you built the game with)
MODS=<the folder that holds main.manifest.json, sdk.manifest.json and subsdk0.manifest.json>
ROMFS=<RomFS.img>
```

1. The baseline, native only, with the cache you already have (about 25 s of loading, then the run). Use the options
   of your usual run for the display, the GPU and the scripted keys. The two runs must differ only in `--jit`,
   the cache folder and the report file.

   ```sh
   python3 -I engine/dm/tools/horizon_run.py --modules "$MODS" --romfs "$ROMFS" --title 0100e95004038000 \
       --seconds 150 --report-json ~/xc2-aot.json
   ```

2. The same run with the fallback. This is the long one.

   ```sh
   python3 -I engine/dm/tools/horizon_run.py --modules "$MODS" --romfs "$ROMFS" --title 0100e95004038000 \
       --jit --seconds 150 --cache ~/.cache/nx2linux/aot-xc2-jit --report-json ~/xc2-jit.json
   ```

3. Read them side by side.

   ```sh
   python3 -I engine/dm/tools/aot_report.py show ~/xc2-jit.json
   python3 -I engine/dm/tools/aot_report.py compare ~/xc2-aot.json ~/xc2-jit.json
   ```

To only build, without playing (the static part of the report is written), add `--build-only` to the second command.

## What the report holds

No game data: counts, and mnemonics of instructions that Remill does not lift (the same as `survey.py` prints).

| Field | Meaning |
|---|---|
| `static.functions`, `lifted`, `replaced` | entries of the build, lifted to native code, replaced by a host function (not game code) |
| `static.jit_fallback`, `jit_fallback_by_reason`, `instruction_details` | functions that run in the JIT, why, and the mnemonics |
| `runtime.jit_entries` | times the recompiled code entered the JIT |
| `runtime.jit_to_aot` | times the JIT went back to recompiled code |
| `runtime.fallback_requests` | indirect calls and jumps whose target the AOT table did not hold |
| `runtime.unlifted_calls` | calls of a function that was not lifted |
| `runtime.jit_invalidations`, `jit_pages` | writes into code the JIT had translated, and pages it holds (see #17) |
| `derived.aot_function_share` | lifted / (lifted + left to the JIT), the replaced functions are not counted |
| `derived.fallback_share_of_indirect_transfers` | `fallback_requests` / (`indirect_calls` + `indirect_jumps`) |

## What to send back

- The two JSON files (they hold no game data) and the output of `aot_report.py compare`.
- The `built in ... s (N cached, M built, K not translated)` line of the second run and `du -sh ~/.cache/nx2linux/aot-xc2-jit`.
- Whether the run ended on its own timer or a thread stopped (`threads that stopped:` in the log).

## How to read it

- `jit_fallback` is expected to be 0 with the Remill of patch 0009: the 27 crypto functions that used to fall back
  are lifted, and 0 functions were left untranslated. A non-zero number is a finding, and `instruction_details` names the mnemonic.
- `jit_entries` of 0 after a run that played means the fallback was never needed by that path. It does not prove the
  fallback correct on the game: it proves the game did not call it. Say so in the issue.
- A large `fallback_requests` with `jit_fallback` of 0 means indirect targets that are not entries of the AOT
  table (generated code, a jump the table analysis did not see). That is the case #17 measures.
