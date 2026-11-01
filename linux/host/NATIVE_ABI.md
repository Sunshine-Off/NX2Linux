# Native fixture host contract (S1a)

This is a testable POSIX foundation, not the Switch guest ABI and not an AOT
recompiler. `nx2linux_headless` loads a trusted native x86_64 ELF shared object
with `dlopen(RTLD_NOW | RTLD_LOCAL)`. No SDL, Vulkan or display is required.

## Module boundary

The module exports a constant `Nx2LinuxNativeModule nx2linux_native_module`.
See `include/nx2linux/posix_host.h` for the exact C layout. The host checks the
ABI version, structure size and entry pointer before calling `run`.

The host passes a versioned structure containing zero-initialized read/write
memory, its size, a monotonic clock callback and a log callback. The mapping has
inaccessible guard pages at both ends and no executable permission. This region
is host scratch memory, not a model of Switch virtual memory.

The module must join all worker threads before returning. The host unloads the
module before releasing its scratch memory. Modules must not keep pointers to
the stack-resident host API after `run` returns. A nonzero entry result becomes a
nonzero host exit status with a diagnostic.

Only trusted modules may be loaded. ELF constructors run during `dlopen`, before
the descriptor check. There is no sandbox, guest exception handling, instruction
translation, SVC handling or isolation from invalid native pointers. A module
crash can crash the process. This is intentional native-plugin behavior, not a
claim about safely executing untrusted game code.

The synthetic fixture verifies memory access, the clock and native pthread/TLS/
mutex behavior. These tests do not establish ARM64 guest TLS or memory semantics.

## Boundary to agree with Téo (S1b)

Before treating an AOT artifact as loadable, agree and test:

1. Artifact manifest, architecture, ABI version, engine identity and relocations
2. CPU state layout, guest address translation and memory ownership
3. Entry/callout signatures, SVC dispatch, TLS and thread lifecycle
4. Error/exception propagation and cleanup of all module-owned workers
5. Graphics command interface, resource lifetime and synchronization

Téo owns guest semantics, guest loading/relocations and AOT correctness. Swann
owns native platform facilities and the adapter into the agreed runtime contract.
The fixture ABI is a proposal for exercising platform facilities, not a unilateral
replacement for Téo's runtime ABI. Do not load a game artifact through this API
until a deliberate adapter and conformance tests exist.
