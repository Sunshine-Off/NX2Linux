# NX2Linux: assumptions about functions, builds and addresses

Every assumption the recompilation work (ARM64 Switch modules → x86_64 Linux) relies on, with the date it was made and what would confirm or refute it. Newest last.

## 2026-11-01 (T1: the synthetic fixture and `dm/tools/arm64_survey.py`)

| # | Assumption | Why it is made | How to confirm |
|---|---|---|---|
| H1 | Inside a function the unwind data (`.eh_frame_hdr`) describes, every word is an instruction; a word that does not decode is an error, not data. | The fixture has no literal pools. `nso_code.data_blocks` only finds data between functions. | Run `arm64_survey.py` on a retail module you own, outside this repository: count the `SurveyError`s. A compiler can put a literal pool or a jump table inside a function, so expect some. |
| H2 | A stretch of text no FDE covers is a data block, padding or an SDK wrapper `svc #n; ret`; nothing else is treated as code. | `nso_match.py` (`Image._callee`) makes the same assumption for the SDK's system call wrappers. | Compare the survey's gaps with `nso_code.data_blocks` and Ghidra's view (`nso2elf.py`) on a module you own. |
| H3 | A direct branch that leaves its function and does not land on an FDE start is `unknown`, not an error. | A tail call or a jump into a function the unwind data lacks is possible; the survey only reports it. | The survey lists these targets on a module you own: each should be a function start in Ghidra. |
| H4 | The hand-made encoders in `engine/dm/tools/tests/arm64_fixture.py` (`ADDS`, `SUBS`, `MUL`, `MOVZ`, `BL`, `BCOND`, `CBZ`, `SVC`…) encode what their names say. | They are written from the Arm Architecture Reference Manual. | `test_encodings_decode_as_intended` compares each with Capstone 5. A second decoder (Unicorn or qemu-aarch64 as the ARM64 reference, T2) would be an independent check. |
| H5 | The reference ARM64 behaviour will come from Unicorn (instruction and flag tests) and `qemu-aarch64` (whole functions). Not decided yet. | Neither is installed on this machine (2026-11-01); the Mac Apple Silicon option is not available here. | Téo's choice; install one and run the fixture's `arith` function in it. |
| H6 | Capstone 5 is the decoder (`patch_nso.py` refuses Capstone 6). | `patch_nso.py` header. | `pip install "capstone>=5,<6"`; `_md()` in `arm64_survey.py` checks the version. |

Not assumed, only unknown: how the Switch's retail modules lay out their code (the survey has run on the fixture only), and which functions of a real game call `svc` outside the SDK wrappers.

## 2026-11-01 (T3: branches, memory, calls — `aot_lift.py`, `aot_runtime.cpp`)

| # | Assumption | Why it is made | How to confirm |
|---|---|---|---|
| H7 | A `ret` returns to the caller of the lifted function (the C return); x30 is not consulted. | `__remill_function_return` ends the lifted function; direct `bl` is a C call. | Code that rewrites x30 before `ret` (longjmp-like unwinding, coroutine switches, the SDK's context switch) would break this. Search a real module you own for `ret` after an `x30` write that is not a reload from the stack; T4/T5 must handle or refuse it. |
| H8 | A `br` is a tail call to a function entry. A jump into the middle of a function (a jump table) is an explicit error. | The runtime's table holds function entries only. | Count `br` instructions in a real module with `arm64_survey.py` (kind `ibranch`) and check how many land inside the same function. Jump tables need their cases resolved at translation time. |
| H9 | The guest memory is a few flat regions; an unaligned access to normal memory is allowed and not atomic. | AArch64 normal memory allows it. | Not for Device memory or atomics: do not apply to those. |
| H10 | Floating point, SIMD and `svc` are not translated yet; byte, halfword and pair exclusives (`ldxrb`, `stxrh`, `ldxp`, `stxp`) and the LSE atomics (`ldadd`, `swp`, `cas`...) are not decoded. | Remill refuses them (LiftError); the runtime has no FP intrinsics (LinkError). | Re-run the scan of those mnemonics after each Remill update. The Switch's A57 cores have no LSE, but compilers emit `ldxrb`/`stlxrb`/`ldxp` for byte and 128-bit atomics: check a real module for them before T7. |
| H11 | An exclusive store is a compare-and-swap against the value the exclusive load read (QEMU's model), not a hardware monitor: a store of the same value by another thread between the two goes unseen (ABA). | Remill's monitor had no value at all. | Lock-free code that depends on ABA detection (rare: tagged pointers avoid it) would need a version counter; none is known in the SDK. A real module's atomics (count `ldaxr` loops) must run threaded in T5. |
| H12 | On an x86 host, `stlr` costs a full fence (the store->load barrier that keeps it ordered with a later `ldar`). | STLR and LDAR are sequentially consistent with each other on AArch64. | Measure the cost on a real module's lock-heavy code; weaken only after showing no `ldar` follows. |
