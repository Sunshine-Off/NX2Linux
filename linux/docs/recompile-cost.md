# Reducing the cost of recompiling a game (issue #18)

A cold build of Xenoblade 2 is about 98,000 objects, about 12 GB of cache and 1,430 to 1,541 s on 16 cores (`S8` section 10, `S9` section 7).
The code is derived from the game and cannot be shipped, so every user pays it. Nothing was implemented for this issue yet: this page measures,
proposes, and waits for a choice. The measuring tool is `engine/dm/tools/aot_cost.py`.

## What was measured, on code that is not the game's

`aot_cost.py measure` generates C (loops, switches, floats, structures, calls), compiles it for AArch64 with `aarch64-linux-gnu-gcc`, lifts every function with Remill and
compiles it in each variant. The run below is 400 functions of 90 bytes of AArch64 on average, one job at a time so that the CPU times are not disturbed
(`aot_cost.py measure --functions 400 --jobs 1`, 1 min 44 s). **Every figure is from this corpus, not from the game.**

| Variant | Bytes per function | Compile CPU | zstd of each object | What changes |
|---|---|---|---|---|
| base (today: `clang++ -c -O2 -fPIC`) | 2,149 (100%) | 7.2 s (100%) | 339 KB for 400 (39%) | |
| no unwind tables | 2,149 (100%) | 102% | 100% | nothing: the lifted functions are `nounwind`, there is no `.eh_frame` to remove (0.4% of the bytes are unwind data) |
| slim (also no `.llvm_addrsig`, no `.comment`) | 2,070 (96%) | 103% | 97% | 4% smaller |
| slim at -Os, at -O1 | 96%, 97% | 100% | 97%, 98% | no gain, and the code changes |
| thin LTO | 3,717 (173%) | 81% | 373% | larger objects, the optimisation moves to the link |
| 64 functions per object (`llvm-link`) | 1,286 (60%) | 30% | 84 KB for 400 (25%) | the per-object headers, symbols and process start-up are paid once per 64 |

What is in one object of the corpus (base): code 853 bytes (40%), ELF header and section table 554 (26%), symbols 421 (20%), relocations 308 (14%), unwind and data 14.
Compressed together, the 400 objects go from 860 KB to 42 KB with zstd 3. One at a time they only reach 339 KB. In groups of 64 they reach 85 KB.

**The time is in the lifter, not in the compiler.** `remill-lift` took 86 ms of CPU for a function of one instruction, 87 ms for 100 and 96 ms for 1,000:
about 85 ms is the process start-up (it loads its semantics each time), and a lifted instruction costs about 0.01 ms. On the corpus the lifting is 34.4 s of CPU and the
compile 7.2 s, so **83% of the CPU time is the lifter, mostly its fixed start-up**. A cold build is about 22,900 core-seconds at most (1,430 s on 16 cores) for about 103,000 entries, about 220 ms each:
the start-up alone would be about 39% of that (**deduced**, not measured on the game: the cores are not all busy all the time).

**What this does not explain.** The corpus makes an object of about 2 KB. The reported cache is 12 GB for 97,763 objects, about 123 KB per object, 57 times more.
The corpus functions are small (90 bytes), the game's are larger and the inline memory accesses (`AOT_INLINE_MEM=1`) make the code bigger, but that does not obviously reach 57 times.
It may also be that part of the 12 GB is not objects (the finished libraries kept under `lib/`, one per key). **What the 12 GB holds is unknown.** One read-only command tells (below), and the choice depends on its answer.

## Options

| | Option | Expected gain | Risk and cost |
|---|---|---|---|
| E | **Batch the lifting**: `remill-lift` lifts many functions in one process (a list on the command line or in a file), so the semantics are loaded once. A patch to Remill (series 0011) and a change to `aot_lift.py` | up to 39% of the CPU time of a cold build (deduced), more on the corpus (the fixed start-up is 83% of the CPU there). Disk: none | medium. It changes the cache key (everyone recompiles once, about 25 min and 12 GB). The cached object of a function does not depend on the others, so the cache stays per function |
| B | **Group the objects**: 64 functions per object (`llvm-link`, one compile) | on the corpus: size 40% less, compile CPU 70% less. On the game it depends on the real size of an object: about 1.3 KB per object of fixed overhead, so about 1% if an object is 120 KB, about 40% if it is 2 KB | medium. A change to one function recompiles its group of 64. The cache key becomes a hash of a group, the bookkeeping (`.syms`, `.fail`) is per function today. Fewer files for the link line, which the response file handles today |
| D | **Compress the cached objects** (zstd), and decompress at the link | on the corpus: objects one at a time 2.5 times smaller, grouped by 64 about 6 times smaller | low. The link reads every object, so decompressing 12 GB costs some tens of seconds of one core (zstd is about 1 GB/s: deduced). The cache key does not change. Best together with B |
| A | no unwind tables, slim objects | 4% of the size on the corpus, nothing in time | none, and not worth doing: measured above |
| C | thin LTO | none: larger objects | high link time on 100,000 functions. Not recommended |
| F | lift only what is reached, the rest in the JIT | large | high: it gives up the static recompilation. Not recommended, and #14 and #17 are the measurements that would say how much the JIT can carry |

## My recommendation, to be confirmed

1. **Run `cache-stats` on your real cache first** (read-only, a minute): it says what the 12 GB are, which decides between B, D and nothing for the disk.
2. **E** for the time. It is the only option with a large gain on the time, and the cost is one Remill patch and one recompilation of every game.
3. **D**, then **B** only if `cache-stats` shows that the objects are small. Drop A and C.

## The command to run on your cache

It reads sizes and the sections of a random sample of objects, and compresses that sample in memory. It writes nothing in the cache. The objects are code derived from the game, they are read on your machine and only numbers come out.

```sh
cd <your clone>
python3 -I engine/dm/tools/aot_cost.py cache-stats ~/.cache/nx2linux/aot-xc2c --sample 500 --json ~/xc2-cache-stats.json
```

Use the folder of the build you want to know about (the ones under `~/.cache/nx2linux/` are `aot-cache`, `aot-xc2`, `aot-xc2b`, `aot-xc2c` and others). The time is unknown, mostly the reading of 500 objects.
Add `--no-compress` to skip the compression. Send back the printed text and the JSON file. They hold counts and sizes only.
The measurement on the corpus is `python3 -I engine/dm/tools/aot_cost.py measure` with `REMILL_BUILD` set (about 2 minutes for 400 functions with `--jobs 1`).
