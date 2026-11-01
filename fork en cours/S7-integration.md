# S7 — T7 handoff and headless-first integration

## Evidence inspected

Remote branch: `origin/feat/teo-t7-module-reel`.

Inspected revision: `426d040bdf4ed2737605a314d109ab40ab049c1a`.

Read-only inspection covered the branch's `T6-manifeste-elf.md`,
`T7-module-reel.md` and `blocages-connus.md`. The branch is fetched, not merged.
Its reports are evidence supplied by Téo, not independently reproduced results
in this checkout.

The T7 report records whole-module builds for rtld, main, subsdk0 and subsdk1,
and differential tests on real functions. Later patches improve floating-point,
NEON and jump-table coverage. None of these reports establishes game startup,
real service calls or rendered frames. Do not interpret sample lifting coverage
as the percentage of runtime spent in AOT or a complete static-only build.

T6 uses `ModuleSet` and the AOT runtime, with verified manifests, relocation and
Python host-import callbacks. This is not the `nx2linux_native_module` fixture
ABI added for S1a. Adapt to the real runtime rather than forcing it into the
fixture entry point.

## Known integration blockers from Téo's reports

- Native HLE import adapters are not connected
- The actual Horizon `svc_dispatch` is not linked into this Linux path
- Apple/Mach dependencies remain in host service files
- Guest memory permissions and some loader features remain unimplemented
- JIT invalidation/thread semantics and return/context behavior need review
- Function-level differential success is not whole-game execution coverage

Historical numbers in T7 predate the later coverage patches. Reproduce current
results on the inspected revision before using them as acceptance evidence.

## Work order

### S7.0 — Preserve and integrate the source trees

Commit the S1–S6 remediation on its dedicated branch first. Then use a separate
S7 branch to integrate Téo's patch series. Read `engine/AGENTS.md`, verify the
shared upstream pin, reconcile patch numbering, run `tools/engine.py check`, and
sync without discarding any engine work. Do not overwrite the current dirty tree
with a checkout of Téo's branch.

### S7.1 — Reproduce a synthetic runtime execution

Locate or reproducibly build the patched Remill/LLVM and Dynarmic dependencies.
Run T6's synthetic manifest/relocation/import tests. Record the ABI and memory
contract, then implement the Linux platform adapter. Check diagnostics for
missing imports, invalid manifests and service failures. No game data is needed
for this gate.

### S7.2 — First real headless startup

With the owner's local game inputs, reproduce conversion and module builds.
Resolve module imports in the documented order and implement only the required
HLE/SVC subset, failing explicitly for unsupported services. Record the first
entry executed, initialization progress, unresolved imports and AOT/JIT counters.
Compiling modules is not enough to accept this gate.

### S7.3 — First real frame and playable behavior

Depends on S4b: actual game graphics commands, shaders, resource management and
synchronization. Connect that path to the Linux renderer and verify a real frame,
then input and continued execution. The synthetic triangle is not this gate.

## Current status

S7 preparation has begun through source/report inspection. No merge, runtime
adapter, real game launch or graphical integration has been performed. S1b/S4b
and the strict Vulkan environment checks remain open.
