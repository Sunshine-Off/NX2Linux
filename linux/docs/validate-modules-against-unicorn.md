# Validating the Xenoblade 2 modules against Unicorn (issue #15)

Balatro's recompiled code was compared with the Unicorn ARM64 core (227 functions identical over 2,009 cases, 0 differences).
The Xenoblade 2 modules were not. `module_validate.py diff` lifts leaf functions, compiles them, runs them next to
Unicorn on random inputs and compares registers, NZCV and memory. `validate_game.py` runs all of its steps on every module
and puts the result in one table. It needs the game, so it is not part of the tests and CI does not run it.

Nothing of the game is stored or printed beyond addresses, counts and mnemonics. The work folder holds code derived
from the game (converted modules, lifted objects), so it must stay outside the repository: the tool refuses a folder inside a git repository.

## Calibrate first

The time of a full pass is unknown (nothing was measured on these modules). Run a small one, read the seconds each step prints, and scale the numbers up.

```sh
cd <your clone>
source ~/.cache/nx2linux/toolchain-clean/env.sh      # REMILL_BUILD, and DYNARMIC_DIR for --jit (use the toolchain the game was built with)
EXEFS=<the ExeFS folder that nx2linux extract wrote: main, sdk, subsdk0>
WORK=~/.cache/nx2linux/validate-xc2
python3 -I engine/dm/tools/validate_game.py "$EXEFS" "$WORK" main,sdk,subsdk0 \
    --functions 20 --cases 6 --coverage-sample 200 --md ~/xc2-validate-small.md
```

## The full pass

```sh
python3 -I engine/dm/tools/validate_game.py "$EXEFS" "$WORK" main,sdk,subsdk0 \
    --functions 300 --cases 12 --seeds 1,2,3 --coverage-sample 3000 --md ~/xc2-validate.md
```

A step whose result is already in the work folder is skipped, so an interrupted run resumes. A second run with other numbers needs `--force`
or a new work folder. With `--jit` the tool adds, for each module and seed, the functions Remill cannot lift run in the dynarmic fallback
next to Unicorn. The Remill of patch 0009 lifts the 27 functions that used to fall back, so this pass may have nothing to test, and the table then says 0 functions for it.

The steps for each module are `coverage`, then `diff` on the leaf functions, then `diff --switches` on the functions with a jump table, then the census of all modules.
Everything the tool runs is `module_validate.py`, which you can also run by hand on one module and one step.

## Reading the table

| Column | Meaning |
|---|---|
| functions tested, equal, mismatch | functions compared, identical to Unicorn on every case, and those with a difference |
| inconclusive | no case could be compared (both sides faulted, or the step budget ran out) |
| unliftable | functions Remill cannot lift (only with no fallback) |
| timeout, died | a batch that hung or a worker that crashed: a result is missing, so the run is not a pass |
| cases equal | the number of comparisons behind the "equal" column. A row of "equal" with few cases proves little |
| both fault, budget, fault differs | cases that prove nothing (both fault, over budget) and a case where only one side faulted (a difference). The line under the table gives the case and, when Unicorn is the one that stopped, **the mnemonic of the instruction it stopped on** (`UC_ERR_EXCEPTION on fmov`). Unicorn cannot execute everything Remill lifts, such as half-precision floating point, LSE atomics and pointer authentication, so read the mnemonic before reading it as a defect of the recompiler |
| MISMATCH cases | cases where registers, flags or memory differ |
| jump-table jumps | indirect jumps of the tables that stayed native |

The exit status is 0 only when nothing differs, nothing timed out or died, and at least one case was compared.

## What to send back

- The markdown table (`--md`), and the `validate.log` lines of any step that did not exit 0.
- For a mismatch: the line the table prints (module, folder, function address, the inputs, the registers that differ). It holds no game bytes.
- The seconds each step printed and `du -sh "$WORK"` (the disk used is unknown until you run it).

## What this cannot show

It compares leaf functions and switch functions, with random inputs, one module at a time. A function that calls another, a function that reads
a system register and the interaction between threads are not compared, because the Unicorn side has no Horizon around it. Equal on 3 seeds is evidence, not a proof of the whole game.

## Running again only the functions that differed

The functions and the inputs are drawn from the seed, so the same command on the same module gives the same cases. A new work folder keeps the earlier results.

```sh
WORK2=~/.cache/nx2linux/validate-xc2-sdk
"$NX2_PYTHON" -I engine/dm/tools/validate_game.py "$EXEFS" "$WORK2" sdk \
    --functions 300 --cases 12 --seeds 1,2,3 --coverage-sample 200 --md ~/xc2-validate-sdk.md
```

Send back the lines that start with `- sdk` under the table. They hold the mnemonic and the random inputs, not game code.
