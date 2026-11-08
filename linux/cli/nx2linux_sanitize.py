"""ASan and UBSan runs of the native code (`nx2linux.py sanitize`, issue #12).

Three pieces of native code have no other check than the tests that happen to pass: the AOT runtime (engine/dm/tools/aot_runtime.cpp and aot_jit.cpp, with the SIGSEGV handler of the inline memory
accesses), and the host libraries of linux/host (gpu.c and nvnrec.c, audio.c, movie.c, display.c). This builds the libraries in a directory of their own with clang and
-fsanitize, builds the AOT runtime the same way (AOT_SANITIZE, engine/dm/tools/aot_lift.py), and runs the C tests and the engine tests that use them with the sanitizer runtime preloaded into Python.
The normal build is never touched: nothing here changes a flag or a cache key unless AOT_SANITIZE or NX2LINUX_SANITIZE is set.

What a run means: a case is `clean` when its process printed no sanitizer report and its tests passed. A report ends the process (halt_on_error), so a defect cannot scroll past. Leak detection is
off by default: the Vulkan drivers and libdbus leak at exit from code that has no symbols here, and Python itself keeps memory until exit. `--leaks` turns it on for whoever wants to read the noise."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_BUILD_DIR = Path.home() / '.cache' / 'nx2linux' / 'build-host-asan'
SANITIZERS = re.compile(r'[a-z]+(,[a-z]+)*')
# what a sanitizer prints when it found something (ASan, LSan, UBSan)
REPORT = re.compile(r'ERROR: (?:Address|Leak|Memory|Thread)Sanitizer|runtime error:|SUMMARY: \w*Sanitizer')
# the libraries linux/host builds for Python, each of which must have been built with the sanitizer
LIBRARIES = ('libnx2linux_gpu.so', 'libnx2linux_audio.so', 'libnx2linux_display.so', 'libnx2linux_movie.so', 'libnx2linux_sync.so')
ENV_OF_LIBRARY = {'libnx2linux_gpu.so': 'NX2LINUX_GPU_LIB', 'libnx2linux_audio.so': 'NX2LINUX_AUDIO_LIB', 'libnx2linux_display.so': 'NX2LINUX_DISPLAY_LIB', 'libnx2linux_sync.so': 'NX2LINUX_SYNC_LIB'}
# capstone's Python binding opens libcapstone with RTLD_DEEPBIND, which the sanitizer runtime refuses (it ends the process). A sitecustomize on PYTHONPATH removes the request in the test process only.
SITECUSTOMIZE = 'import os\nif hasattr(os, "RTLD_DEEPBIND"): del os.RTLD_DEEPBIND\n'


@dataclass(frozen=True)
class Case:
    """One run of an engine test module (engine/dm/tools/tests/<module>.py), or of ctest (module None)"""
    group: str                       # 'c' (ctest), 'aot' (needs Remill), 'jit' (needs Remill and dynarmic), 'host' (the Python tests of the host libraries)
    module: str | None
    env: tuple[tuple[str, str], ...] = ()
    note: str = ''

    @property
    def name(self) -> str:
        return (self.module or 'ctest') + (f' [{self.note}]' if self.note else '')


INLINE = (('AOT_INLINE_MEM', '1'),)
CASES: tuple[Case, ...] = (
    Case('c', None),
    # the AOT runtime: guest memory, atomics, services, the import table, the cache, the address table, system registers, the lifted code of GCC output
    Case('aot', 'test_aot_memory'),
    Case('aot', 'test_aot_svc'),
    Case('aot', 'test_aot_svc_native'),
    Case('aot', 'test_aot_atomics'),
    Case('aot', 'test_aot_stub'),
    Case('aot', 'test_aot_cache'),
    Case('aot', 'test_aot_sysregs'),
    Case('aot', 'test_aot_t3'),
    Case('aot', 'test_aot_realistic'),
    Case('aot', 'test_aot_simd'),
    # the inline memory accesses: a fault outside every region is a SIGSEGV that the handler of aot_runtime.cpp turns into an error (test_aot_svc reaches it, test_aot_memory skips its two fault tests)
    Case('aot', 'test_aot_memory', INLINE, 'inline memory'),
    Case('aot', 'test_aot_svc', INLINE, 'inline memory, SIGSEGV handler'),
    Case('jit', 'test_aot_jit'),
    Case('jit', 'test_elf_module'),
    # the host libraries through ctypes
    Case('host', 'test_horizon_gpu'),
    Case('host', 'test_horizon_nvn'),
    Case('host', 'test_nvn_rec'),
    Case('host', 'test_horizon_sync'),
    Case('host', 'test_gpuprof'),
    Case('host', 'test_horizon_audio'),
    Case('host', 'test_horizon_movie'),
    Case('host', 'test_movie_codec'),
    Case('host', 'test_movie_decoder'),
    Case('host', 'test_horizon_display'),
)
GROUPS = ('c', 'aot', 'jit', 'host')


@dataclass
class Result:
    case: Case
    status: str          # clean | SANITIZER | FAILED | skipped
    detail: str = ''
    seconds: float = 0.0
    reports: int = 0
    log: Path | None = None


def runtime_library(clang: str = 'clang') -> Path | None:
    """libclang_rt.asan.so of the installed clang (Fedora names it that, other layouts libclang_rt.asan-<arch>.so), or None"""
    try:
        out = subprocess.run([clang, '--print-runtime-dir'], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    folder = Path(out.stdout.strip())
    for name in ('libclang_rt.asan.so', 'libclang_rt.asan-x86_64.so'):
        if (folder / name).is_file():
            return folder / name
    return None


def has_sanitizer(library: Path) -> bool:
    """True when the library imports sanitizer runtime symbols: it was built with -fsanitize (a plain build has none, and a run against it would prove nothing)"""
    try:
        out = subprocess.run(['nm', '-D', '--undefined-only', str(library)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and bool(re.search(r'\b__(?:asan|ubsan)_', out.stdout))


def classify(returncode: int, output: str) -> tuple[str, int]:
    """(status, number of sanitizer report lines) of a finished process. A report wins over a failed test: the report is the finding"""
    reports = len(REPORT.findall(output))
    if reports:
        return 'SANITIZER', reports
    return ('clean', 0) if returncode == 0 else ('FAILED', 0)


def sanitizer_environment(runtime: Path, sanitizers: str, build_dir: Path, sitecustomize: Path, *, soft_vulkan: bool = False, leaks: bool = False,
                          base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment of one sanitized process. Only the child gets it: a preload in the calling shell breaks every tool that shell starts"""
    env = dict(os.environ if base is None else base)
    env['LD_PRELOAD'] = str(runtime)
    env['ASAN_OPTIONS'] = f'detect_leaks={int(leaks)}:abort_on_error=0'
    env['UBSAN_OPTIONS'] = 'print_stacktrace=1:halt_on_error=1'
    env['AOT_SANITIZE'] = sanitizers
    env['PYTHONPATH'] = os.pathsep.join(p for p in (str(sitecustomize), env.get('PYTHONPATH', '')) if p)
    env.setdefault('SDL_VIDEODRIVER', 'dummy')
    env.setdefault('SDL_AUDIODRIVER', 'dummy')
    for library, variable in ENV_OF_LIBRARY.items():
        env[variable] = str(build_dir / library)
    if soft_vulkan:
        icd = Path('/usr/share/vulkan/icd.d/lvp_icd.x86_64.json')
        if icd.is_file():
            env['VK_DRIVER_FILES'] = str(icd)
    return env


def build(root: Path, build_dir: Path, sanitizers: str, jobs: int) -> int:
    """Configure linux/host with clang and the sanitizers in build_dir, and build it. 0 when it worked"""
    configure = ['cmake', '-S', str(root / 'linux' / 'host'), '-B', str(build_dir), '-DCMAKE_BUILD_TYPE=Debug', '-DCMAKE_C_COMPILER=clang', f'-DNX2LINUX_SANITIZE={sanitizers}']
    if shutil.which('ninja'):
        configure += ['-G', 'Ninja']
    code = subprocess.run(configure).returncode
    if code:
        print(f'sanitize: configuring {build_dir} failed. If it was configured with another compiler, remove it and run again (it is a build directory of its own)', file=sys.stderr)
        return code
    return subprocess.run(['cmake', '--build', str(build_dir)] + (['--parallel', str(jobs)] if jobs else [])).returncode


def skip_reason(case: Case, build_dir: Path) -> str:
    """Why this case cannot run here, or '' when it can"""
    if case.group in ('aot', 'jit'):
        remill = os.environ.get('REMILL_BUILD', '')
        if not remill or not Path(remill).exists():
            return 'needs REMILL_BUILD (source <toolchain prefix>/env.sh, made by tools/setup_toolchain.sh)'
    if case.group == 'jit':
        dyn = os.environ.get('DYNARMIC_DIR', '')
        if not dyn or not (Path(dyn) / 'build' / 'src' / 'dynarmic' / 'libdynarmic.a').is_file():
            return 'needs DYNARMIC_DIR (the dynarmic fallback, tools/setup_toolchain.sh)'
    return ''


def run_case(case: Case, root: Path, python: str, env: dict[str, str], build_dir: Path, log_dir: Path, timeout: int) -> Result:
    reason = skip_reason(case, build_dir)
    if reason:
        return Result(case, 'skipped', reason)
    env = dict(env, **dict(case.env))
    if case.group == 'jit':   # dynarmic installs signal handlers on alternate stacks per thread, and ASan then fails to free its own copy at thread exit ("failed to deallocate", error 22): measured, test_aot_jit passes with this and fails without
        env['ASAN_OPTIONS'] = env.get('ASAN_OPTIONS', '') + ':use_sigaltstack=0'
    if case.module is None:   # the C tests are programs that carry the sanitizer runtime themselves: a preloaded one would be a second runtime ("incompatible ASan runtimes")
        env = {k: v for k, v in env.items() if k != 'LD_PRELOAD'}
        command, cwd = ['ctest', '--test-dir', str(build_dir), '--output-on-failure', '--timeout', '120'], root
    else:
        command, cwd = [python, '-m', 'unittest', case.module], root / 'engine' / 'dm' / 'tools' / 'tests'
    start = time.monotonic()
    try:
        done = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
        code, text = done.returncode, (done.stdout or '') + (done.stderr or '')
    except subprocess.TimeoutExpired as error:
        code, text = 124, f'timed out after {timeout} s\n' + (error.stderr.decode(errors='replace') if isinstance(error.stderr, bytes) else (error.stderr or ''))
    seconds = time.monotonic() - start
    status, reports = classify(code, text)
    log = log_dir / (re.sub(r'[^A-Za-z0-9_.+-]+', '_', case.name) + '.log')
    log.write_text(text)
    tail = [line for line in text.strip().splitlines() if line.strip()][-1:] or ['']
    detail = f'{reports} sanitizer report line(s), first in {log.name}' if reports else tail[0]
    return Result(case, status, detail, seconds, reports, log)


def summary(results: Sequence[Result]) -> str:
    width = max(len(r.case.name) for r in results)
    lines = [f'{"case":<{width}}  {"result":<9}  {"seconds":>7}  detail']
    for r in results:
        lines.append(f'{r.case.name:<{width}}  {r.status:<9}  {r.seconds:7.1f}  {r.detail}')
    counts = {s: sum(r.status == s for r in results) for s in ('clean', 'SANITIZER', 'FAILED', 'skipped')}
    lines.append('')
    lines.append('  '.join(f'{k}: {v}' for k, v in counts.items()))
    return '\n'.join(lines)


def run(args, root: Path, python: str) -> int:
    """`nx2linux.py sanitize`: build, check that the build is instrumented, run the cases, print the table. Exit 0 when nothing reported and nothing failed"""
    sanitizers = args.sanitizers
    if not SANITIZERS.fullmatch(sanitizers):
        print(f'sanitize: --sanitizers wants a comma-separated list such as address,undefined, not {sanitizers!r}', file=sys.stderr)
        return 2
    for tool in ('clang', 'clang++', 'cmake', 'nm'):
        if not shutil.which(tool):
            print(f'sanitize: {tool} is not installed (the sanitizer build needs clang, clang++, cmake and binutils)', file=sys.stderr)
            return 2
    runtime = runtime_library()
    if runtime is None:
        print('sanitize: the sanitizer runtime of clang (libclang_rt.asan.so) was not found: install clang\'s compiler-rt package', file=sys.stderr)
        return 2
    build_dir = (Path(args.build_dir) if args.build_dir else DEFAULT_BUILD_DIR).resolve()
    if build_dir == (root / 'build' / 'linux-host').resolve():
        print('sanitize: this is the normal build directory, use one of its own', file=sys.stderr)
        return 2
    if not args.skip_build:
        code = build(root, build_dir, sanitizers, args.jobs)
        if code:
            return code
    missing = [name for name in LIBRARIES if not (build_dir / name).is_file()]
    if missing:
        print(f'sanitize: not built in {build_dir}: {", ".join(missing)}', file=sys.stderr)
        return 1
    plain = [name for name in LIBRARIES if not has_sanitizer(build_dir / name)]
    if plain:
        print(f'sanitize: {", ".join(plain)} has no sanitizer in it (a plain build): a run would prove nothing. Remove {build_dir} and run again', file=sys.stderr)
        return 1
    groups = set(args.only or GROUPS)
    cases = [c for c in CASES if c.group in groups]
    log_dir = build_dir / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    with tempfile.TemporaryDirectory(prefix='nx2linux-sanitize-') as tmp:
        (Path(tmp) / 'sitecustomize.py').write_text(SITECUSTOMIZE)
        env = sanitizer_environment(runtime, sanitizers, build_dir, Path(tmp), soft_vulkan=args.vulkan == 'soft', leaks=args.leaks)
        for case in cases:
            print(f'sanitize: {case.name} ...', flush=True)
            results.append(run_case(case, root, python, env, build_dir, log_dir, args.timeout))
            print(f'sanitize: {case.name}: {results[-1].status} ({results[-1].seconds:.1f} s)', flush=True)
    print()
    print(f'sanitizers {sanitizers}, runtime {runtime}, build {build_dir}, logs {log_dir}')
    print(summary(results))
    return 0 if all(r.status in ('clean', 'skipped') for r in results) else 1
