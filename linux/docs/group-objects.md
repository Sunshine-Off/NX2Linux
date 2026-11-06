# Grouping the compiled objects (issue #18, option B)

`AOT_GROUP=N` in the environment makes `aot_lift.Aot` lift about N functions, merge their IR with `llvm-link` and compile the result into **one object**, with a `.grp` file beside it
(the functions it defines, the symbols each leaves undefined, and the ones Remill could not lift). With `AOT_GROUP` unset, 0 or 1 nothing changes: one object per function, and the existing caches stay valid.
Grouped objects live under their own keys, so both kinds can sit in one cache. It is off by default because it is not measured on the game yet.

## How it works

- **The groups are cut by address.** A group ends before a function whose address hashes to a multiple of N, or at 4 times N. Adding, removing or changing one function changes its group and at most the next one,
  not every group after it. A changed function recompiles its group, about N functions, instead of itself.
- **A call between two functions of a group stays a call.** The test looks for the relocation to the callee in the object.
- **It degrades to the ungrouped build.** A group that `llvm-link` or the compiler cannot handle, and a group where one function needs a symbol the runtime does not define, are split into one object per function
  (marked `split` in the `.grp`, so the next build reads them back as such). `Aot.group_splits` counts them, and so does the line `objects grouped: ... split` that `horizon_run.py` prints.
- **Use N of 32 or more.** The functions of a group are lifted by one `remill-lift` process, so a small N gives back the start-up cost that option E removed (4 gives 20 processes for 70 functions).
- `cache-stats` counts the `.grp` files, and `horizon_run.py --report-json` has `group_hits`, `group_builds` and `group_splits` in `static`.

## What it saves, on the generated corpus

1,000 functions of 90 bytes of AArch64 on average, a cold build with a cache, 14 jobs, run twice for the first and the 64 rows:

| `AOT_GROUP` | files in `obj/` | on disk | content | CPU | wall time |
|---|---|---|---|---|---|
| 0 | 2,000 | 8.65 MB | 2.20 MB | 76.3 s and 76.4 s | 7.2 s and 7.3 s |
| 16 | 124 | 1.74 MB | 1.45 MB | 58.6 s | 5.2 s |
| 64 | 26 | **1.43 MB (-83%)** | 1.38 MB (-37%) | **36.3 s and 40.1 s (-48%)** | 6.3 s and 6.8 s |
| 256 | 6 | 1.37 MB | 1.36 MB | 22.9 s | 16.6 s |

The wall time of this small corpus hides the gain: 13 groups for 14 jobs leave the cores idle, and 3 groups at 256 even more. Xenoblade 2 has 103,000 functions, about 1,600 groups at 64, which keeps every core busy.
**The effect on the game is not measured.** The whole engine suite also passes with `AOT_GROUP=4` (every fixture grouped), apart from two files that count one object per function and are pinned to that mode.

## Measuring it on Xenoblade 2

In a cache of its own so that nothing existing is touched, with the same `--jit` as the cold build of 861 s and 1.3 GB:

```sh
source ~/.cache/nx2linux/toolchain-p2/env.sh
AOT_GROUP=64 "$NX2_PYTHON" -I engine/dm/tools/horizon_run.py --modules "$MODS" --romfs "$ROMFS" --title 0100e95004038000 \
    --jit --build-only --cache ~/.cache/nx2linux/aot-xc2-p2-group --report-json ~/xc2-group.json
find ~/.cache/nx2linux/aot-xc2-p2-group/obj -type f | wc -l
du -sh ~/.cache/nx2linux/aot-xc2-p2-group/obj ~/.cache/nx2linux/aot-xc2-p2-group/lib
```

Then play it as usual with `AOT_GROUP=64` and the same `--cache`, and compare the picture and the frame rate with the ungrouped cache.
Without `AOT_GROUP` the same cache folder is read as an ungrouped one, so the first run of the game is a cold build.
