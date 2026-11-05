#!/usr/bin/env python3
"""NX2Linux Linux host command line interface."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUILD_DIR = ROOT / 'build' / 'linux-host'


def xdg_dir(kind: str) -> Path:
    if kind not in ('config', 'cache', 'state', 'data'):
        raise ValueError(kind)
    env = os.environ.get(f'XDG_{kind.upper()}_HOME')
    if env and Path(env).is_absolute():
        return Path(env) / 'nx2linux'
    defaults = {
        'config': Path.home() / '.config',
        'cache': Path.home() / '.cache',
        'state': Path.home() / '.local' / 'state',
        'data': Path.home() / '.local' / 'share',
    }
    return defaults[kind] / 'nx2linux'


def tool_version(command: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if output else 'available'


def pkg_config(package: str) -> str | None:
    return tool_version(['pkg-config', '--modversion', package])


WORKER_MODULES = ('capstone', 'unicorn', 'lz4')


def worker_python() -> str:
    """The interpreter of the engine workers: NX2_PYTHON, else the venv of tools/setup_toolchain.sh, else this one (same rule as module_validate.worker_python)."""
    if os.environ.get('NX2_PYTHON'):
        return os.environ['NX2_PYTHON']
    venv = Path(os.environ.get('NX2_VENV') or Path.home() / '.cache' / 'nx2linux' / 'venv') / 'bin' / 'python'
    return str(venv) if venv.exists() else sys.executable


def missing_worker_modules(python: str) -> list[str]:
    """The worker modules `python -I` cannot import (-I ignores the user site, as the workers do)."""
    missing = []
    for module in WORKER_MODULES:
        try:
            ok = subprocess.run([python, '-I', '-c', f'import {module}'], capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            missing.append(module)
    return missing


def doctor_report() -> dict[str, dict[str, str | bool]]:
    checks: dict[str, dict[str, str | bool]] = {}

    py = tool_version([sys.executable, '--version'])
    checks['python'] = {'ok': py is not None, 'detail': py or 'Python unavailable'}
    worker = worker_python()
    missing = missing_worker_modules(worker)
    checks['worker-python'] = {
        'ok': not missing,
        'detail': f'{worker} imports {", ".join(WORKER_MODULES)}' if not missing
        else f'{worker} cannot import {", ".join(missing)} (run tools/setup_toolchain.sh, it makes the venv)',
    }
    checks['cmake'] = {'ok': shutil.which('cmake') is not None,
                       'detail': tool_version(['cmake', '--version']) or 'CMake unavailable'}
    checks['pkg-config'] = {'ok': shutil.which('pkg-config') is not None,
                            'detail': tool_version(['pkg-config', '--version']) or 'pkg-config unavailable'}
    checks['sdl3'] = {'ok': pkg_config('sdl3') is not None,
                      'detail': pkg_config('sdl3') or 'SDL3 development package unavailable'}
    checks['vulkan'] = {'ok': pkg_config('vulkan') is not None,
                        'detail': pkg_config('vulkan') or 'Vulkan development package unavailable'}
    checks['egl'] = {'ok': pkg_config('egl') is not None,
                     'detail': pkg_config('egl') or 'EGL development package unavailable (mesa-libEGL-devel)'}
    checks['dotnet'] = {'ok': shutil.which('dotnet') is not None, 'required': False,
                        'detail': tool_version(['dotnet', '--version']) or 'install dotnet-sdk-10.0 to extract a game'}
    checks['glslc'] = {'ok': shutil.which('glslc') is not None,
                      'detail': tool_version(['glslc', '--version']) or 'glslc unavailable'}
    vulkaninfo = shutil.which('vulkaninfo')
    vulkaninfo_version = tool_version(['vulkaninfo', '--summary']) if vulkaninfo else None
    checks['vulkaninfo'] = {
        'ok': vulkaninfo_version is not None,
        'required': False,
        'detail': vulkaninfo_version or (
            'vulkaninfo is present but could not query a driver' if vulkaninfo
            else 'install vulkan-tools for GPU diagnostics'
        ),
    }
    import nx2linux_build   # the checks `build` makes, so that doctor reports the same gaps
    for c in nx2linux_build.prerequisites():
        if c.name in ('remill', 'dynarmic'):
            checks[c.name] = {'ok': c.ok, 'required': False, 'detail': c.detail if c.ok else f'{c.detail} (needed to {"recompile a game" if c.name == "remill" else "recompile with --jit"}: {c.fix})'}
    for kind in ('config', 'cache', 'state', 'data'):
        checks[f'xdg-{kind}'] = {'ok': True, 'detail': str(xdg_dir(kind))}
    return checks


def print_doctor(report: dict[str, dict[str, str | bool]], as_json: bool) -> int:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for name, check in report.items():
            if check['ok']:
                state = 'OK'
            elif check.get('required', True):
                state = 'MISSING'
            else:
                state = 'OPTIONAL'
            print(f'[{state:7}] {name}: {check["detail"]}')
    required = ('python', 'worker-python', 'cmake', 'pkg-config', 'sdl3', 'vulkan', 'egl', 'glslc')
    return 0 if all(bool(report[name]['ok']) for name in required) else 1


def build_dir(value: str | None) -> Path:
    return (ROOT / value).resolve() if value and not Path(value).is_absolute() else Path(value or DEFAULT_BUILD_DIR).resolve()


def run(command: Sequence[str], *, cwd: Path = ROOT, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=capture)


def configure_and_build(args: argparse.Namespace) -> int:
    directory = build_dir(args.build_dir)
    configure = ['cmake', '-S', str(ROOT / 'linux' / 'host'), '-B', str(directory),
                 f'-DCMAKE_BUILD_TYPE={args.config}']
    result = run(configure)
    if result.returncode != 0:
        return result.returncode
    command = ['cmake', '--build', str(directory)]
    if args.jobs:
        command += ['--parallel', str(args.jobs)]
    return run(command).returncode


def run_demo(args: argparse.Namespace) -> int:
    directory = build_dir(args.build_dir)
    demo = directory / 'nx2linux_vulkan_demo'
    if args.build or not demo.is_file():
        result = configure_and_build(argparse.Namespace(build_dir=args.build_dir,
                                                        config='Debug', jobs=args.jobs))
        if result != 0:
            return result
    env = os.environ.copy()
    if args.backend != 'auto':
        env['SDL_VIDEODRIVER'] = args.backend
    return subprocess.run([str(demo)], cwd=ROOT, env=env).returncode


def verify(args: argparse.Namespace) -> int:
    directory = build_dir(args.build_dir)
    demo = directory / 'nx2linux_vulkan_demo'
    if args.build or not demo.is_file():
        result = configure_and_build(argparse.Namespace(build_dir=args.build_dir,
                                                        config='Debug', jobs=args.jobs))
        if result != 0:
            return result
    with tempfile.TemporaryDirectory(prefix='nx2linux-verify-') as temporary:
        report = Path(temporary) / 'tests.xml'
        result = run(['ctest', '--test-dir', str(directory), '--output-on-failure',
                      '--no-tests=error', '--timeout', '60', '--output-junit', str(report)])
        if result.returncode != 0:
            return result.returncode
        try:
            cases = {case.attrib['name']: case for case in ET.parse(report).iter('testcase')}
            required = ['nx2linux_sdl_host_smoke']
            if args.require_vulkan:
                required += ['nx2linux_vulkan_triangle', 'nx2linux_gl_on_vulkan']
            for name in required:
                case = cases.get(name)
                if (case is None or case.find('skipped') is not None
                        or case.find('failure') is not None or case.find('error') is not None
                        or case.get('status') != 'run'):
                    raise ValueError(f'{name} did not run successfully')
        except (OSError, ET.ParseError, KeyError, ValueError) as error:
            print(f'verify: {error}', file=sys.stderr)
            return 1
    return 0


NX_EXTRACT = ROOT / 'engine' / 'dm' / 'tools' / 'nx_extract'
TITLE_RE = re.compile(r'^title ([0-9a-f]{16})', re.MULTILINE)


def inside_repository(path: Path) -> bool:
    """Game data and keys never go into the checkout: extraction output must be elsewhere"""
    try:
        path.resolve().relative_to(ROOT)
    except ValueError:
        return False
    return True


def extract(args: argparse.Namespace) -> int:
    """Extract a game's ExeFS and RomFS with the engine's nx_extract into the XDG cache (S7)"""
    nsp, keys = Path(args.nsp), Path(args.keys)
    if keys.is_dir():
        keys = keys / 'prod.keys'
    for label, path in (('game', nsp), ('keys', keys)):
        if not path.is_file():
            print(f'extract: {label} file not found: {path}', file=sys.stderr)
            return 2
    if not shutil.which('dotnet'):
        print('extract: dotnet is missing (install dotnet-sdk-10.0)', file=sys.stderr)
        return 2
    if not (NX_EXTRACT / 'nx_extract.csproj').is_file():
        print('extract: engine/ is not synced (python3 tools/engine.py sync)', file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else xdg_dir('cache') / 'extract'
    if inside_repository(out):
        print(f'extract: refusing to write game data inside the repository: {out}', file=sys.stderr)
        return 2
    title = run(['dotnet', 'run', '-c', 'Release', '--project', str(NX_EXTRACT), '--', str(keys), '--title', str(nsp)],
                capture=True)
    found = TITLE_RE.search(title.stdout or '')
    if title.returncode != 0 or not found:
        print('extract: the package has no readable title ID', file=sys.stderr)
        return title.returncode or 5
    target = out / found.group(1)
    target.mkdir(parents=True, exist_ok=True)
    result = run(['dotnet', 'run', '-c', 'Release', '--project', str(NX_EXTRACT), '--', str(keys), str(nsp), '-',
                  str(target), '--romfs-image'])
    if result.returncode == 0:
        print(f'extracted {found.group(1)} to {target}')
    return result.returncode


def game_build(args: argparse.Namespace) -> int:
    """nx2linux build <nsp>: extraction, ELF modules, build manifest, recompilation (linux/cli/nx2linux_build.py)"""
    import nx2linux_build as nb
    if not args.nsp:
        print('build: give the game package, for example: nx2linux build game.nsp --keys prod.keys. The SDL3 and Vulkan host demo is now `nx2linux build-host`', file=sys.stderr)
        return nb.EXIT['usage']
    if not args.keys:
        print('build: --keys is required (your own prod.keys, or the folder that holds it)', file=sys.stderr)
        return nb.EXIT['usage']
    opts = nb.Options(nsp=Path(args.nsp), keys=Path(args.keys), out=Path(args.out) if args.out else None, update=Path(args.update) if args.update else None,
                      jit=args.jit, skip_recompile=args.skip_recompile, stop_after=args.stop_after, force=args.force)
    def on_event(e):
        print(json.dumps(e), flush=True) if args.json else nb.print_event(e)
    try:
        root = nb.run_build(opts, on_event)
    except nb.BuildError as error:
        print(f'build: [{error.step}] {error}', file=sys.stderr)
        return error.code
    print(f'build directory: {root}')
    return 0


SURVEY = ROOT / 'engine' / 'dm' / 'tools' / 'survey.py'


def survey(args: argparse.Namespace) -> int:
    """Coverage of a game's recompilation: lifted, replaced by the host, JIT fallback, not translated and why (engine/dm/tools/survey.py)"""
    rest = [a for a in args.survey_args if a != '--'] if args.survey_args[:1] == ['--'] else args.survey_args
    return subprocess.run([sys.executable, str(SURVEY), *rest], cwd=ROOT).returncode


RED_RE = re.compile(r'\[RED\]')


def smoke(args: argparse.Namespace) -> int:
    """Start a game command several times and write a report (S7): every run must exit 0 and log no [RED] line

    The command is the host that runs the recompiled game; it must print its GPU as `gpu: <name>` and the driver as
    `driver: <name>` so the report can say where it ran. Nothing of the game is stored in the report."""
    if not args.command_line:
        print('smoke: give the host command after --', file=sys.stderr)
        return 2
    runs = []
    for index in range(args.runs):
        try:
            done = subprocess.run(args.command_line, cwd=ROOT, text=True, capture_output=True, timeout=args.timeout)
            code, text = done.returncode, (done.stdout or '') + (done.stderr or '')
        except subprocess.TimeoutExpired as error:
            code, text = 124, ((error.stdout or b'').decode(errors='replace') if isinstance(error.stdout, bytes) else (error.stdout or ''))
        reds = [line for line in text.splitlines() if RED_RE.search(line)]
        runs.append({'run': index + 1, 'exit': code, 'red': len(reds)})
        if args.log_dir:
            Path(args.log_dir).mkdir(parents=True, exist_ok=True)
            (Path(args.log_dir) / f'run{index + 1}.log').write_text(text)
    gpu = driver = ''
    for line in text.splitlines():
        if line.startswith('gpu:'):
            gpu = line[4:].strip()
        elif line.startswith('driver:'):
            driver = line[7:].strip()
    passed = all(r['exit'] == 0 and r['red'] == 0 for r in runs)
    report = {'title_id': args.title_id, 'version': args.game_version, 'profile': args.profile, 'gpu': gpu,
              'driver': driver, 'runs': runs, 'passed': passed}
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--build-dir', help='build directory, relative to the repository root')
    common.add_argument('-j', '--jobs', type=int, default=0, help='parallel build jobs')
    ap = argparse.ArgumentParser(prog='nx2linux', description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)

    doctor = sub.add_parser('doctor', help='diagnose Linux build and runtime dependencies')
    doctor.add_argument('--json', action='store_true', help='print machine-readable JSON')

    gb = sub.add_parser('build', help='build a game: package to extraction, ELF modules, build manifest and recompilation (needs dotnet, your prod.keys and Remill)')
    gb.add_argument('nsp', nargs='?', help='the .nsp or .xci of the game')
    gb.add_argument('--keys', help='prod.keys, or the folder that holds it')
    gb.add_argument('--update', help='the update package')
    gb.add_argument('--out', help='build folder (default: the XDG cache), never inside the repository. The game goes to <out>/<title ID>')
    gb.add_argument('--jit', action='store_true', help='build the dynarmic fallback (needs DYNARMIC_DIR)')
    gb.add_argument('--skip-recompile', action='store_true', help='stop after the build manifest, the recompilation (about 25 minutes, 12 GB) is not run')
    gb.add_argument('--stop-after', choices=('check', 'extract', 'modules', 'manifest', 'recompile'), help='stop after this step')
    gb.add_argument('--force', action='store_true', help='run every step again')
    gb.add_argument('--json', action='store_true', help='print the progress events as JSON lines')

    build = sub.add_parser('build-host', parents=[common], help='configure and build the Linux host demo')
    build.add_argument('--config', choices=('Debug', 'Release'), default='Debug')

    launch = sub.add_parser('run', parents=[common], help='run the visible Vulkan demo')
    launch.add_argument('--build', action='store_true', help='build before launching')
    launch.add_argument('--backend', choices=('auto', 'wayland', 'x11', 'offscreen'), default='auto')

    check = sub.add_parser('verify', parents=[common], help='run Linux host tests')
    check.add_argument('--build', action='store_true', help='build before testing')
    check.add_argument('--require-vulkan', action='store_true', help='fail if the Vulkan test is skipped')

    san = sub.add_parser('sanitize', help='run the AOT runtime and the host libraries under ASan and UBSan, in a build directory of their own (needs clang, Remill for the AOT cases)')
    san.add_argument('--build-dir', help='the sanitizer build directory (default: ~/.cache/nx2linux/build-host-asan), never the normal one')
    san.add_argument('-j', '--jobs', type=int, default=0, help='parallel build jobs')
    san.add_argument('--sanitizers', default='address,undefined', help='the -fsanitize list')
    san.add_argument('--only', nargs='+', choices=('c', 'aot', 'jit', 'host'), help='run only these groups: c (ctest), aot, jit (needs DYNARMIC_DIR), host (the Python tests of the host libraries)')
    san.add_argument('--skip-build', action='store_true', help='use the build directory as it is')
    san.add_argument('--vulkan', choices=('default', 'soft'), default='default', help='soft: run the GPU tests on llvmpipe (Mesa lavapipe) whatever the machine has')
    san.add_argument('--leaks', action='store_true', help='also detect leaks (noisy: the drivers and Python leak at exit)')
    san.add_argument('--timeout', type=int, default=1800, help='seconds allowed to each case')

    ext = sub.add_parser('extract', help='extract a game package into the XDG cache (needs dotnet and your prod.keys)')
    ext.add_argument('nsp', help='the .nsp of the game')
    ext.add_argument('--keys', required=True, help='prod.keys, or the folder that holds it')
    ext.add_argument('--out', help='output folder (default: the XDG cache), never inside the repository')

    sv = sub.add_parser('survey', help='how every function of a game is handled (lifted, replaced, JIT, not translated) and why')
    sv.add_argument('survey_args', nargs=argparse.REMAINDER, help='survey.py arguments: --modules DIR --title ID [--jit] [--census] [--json OUT]')

    sm = sub.add_parser('smoke', help='run the host on a game several times and report exits and [RED] lines')
    sm.add_argument('--runs', type=int, default=3)
    sm.add_argument('--timeout', type=int, default=120, help='seconds per run')
    sm.add_argument('--title-id', default='')
    sm.add_argument('--game-version', default='')
    sm.add_argument('--profile', default='')
    sm.add_argument('--log-dir', help='keep each run log here (outside the repository)')
    sm.add_argument('command_line', nargs=argparse.REMAINDER, help='-- <host command and arguments>')
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == 'doctor':
        return print_doctor(doctor_report(), args.json)
    if args.command == 'build':
        return game_build(args)
    if args.command == 'build-host':
        return configure_and_build(args)
    if args.command == 'run':
        return run_demo(args)
    if args.command == 'verify':
        return verify(args)
    if args.command == 'sanitize':
        import nx2linux_sanitize
        return nx2linux_sanitize.run(args, ROOT, worker_python())
    if args.command == 'extract':
        return extract(args)
    if args.command == 'survey':
        return survey(args)
    if args.command == 'smoke':
        args.command_line = [a for a in args.command_line if a != '--'] if args.command_line[:1] == ['--'] else args.command_line
        return smoke(args)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
