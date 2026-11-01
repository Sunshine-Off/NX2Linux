#!/usr/bin/env python3
"""NX2Linux Linux host command line interface."""
from __future__ import annotations

import argparse
import json
import os
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


def doctor_report() -> dict[str, dict[str, str | bool]]:
    checks: dict[str, dict[str, str | bool]] = {}

    py = tool_version([sys.executable, '--version'])
    checks['python'] = {'ok': py is not None, 'detail': py or 'Python unavailable'}
    checks['cmake'] = {'ok': shutil.which('cmake') is not None,
                       'detail': tool_version(['cmake', '--version']) or 'CMake unavailable'}
    checks['pkg-config'] = {'ok': shutil.which('pkg-config') is not None,
                            'detail': tool_version(['pkg-config', '--version']) or 'pkg-config unavailable'}
    checks['sdl3'] = {'ok': pkg_config('sdl3') is not None,
                      'detail': pkg_config('sdl3') or 'SDL3 development package unavailable'}
    checks['vulkan'] = {'ok': pkg_config('vulkan') is not None,
                        'detail': pkg_config('vulkan') or 'Vulkan development package unavailable'}
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
    required = ('python', 'cmake', 'pkg-config', 'sdl3', 'vulkan', 'glslc')
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
                required.append('nx2linux_vulkan_triangle')
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


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--build-dir', help='build directory, relative to the repository root')
    common.add_argument('-j', '--jobs', type=int, default=0, help='parallel build jobs')
    ap = argparse.ArgumentParser(prog='nx2linux', description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)

    doctor = sub.add_parser('doctor', help='diagnose Linux build and runtime dependencies')
    doctor.add_argument('--json', action='store_true', help='print machine-readable JSON')

    build = sub.add_parser('build', parents=[common], help='configure and build the Linux host')
    build.add_argument('--config', choices=('Debug', 'Release'), default='Debug')

    launch = sub.add_parser('run', parents=[common], help='run the visible Vulkan demo')
    launch.add_argument('--build', action='store_true', help='build before launching')
    launch.add_argument('--backend', choices=('auto', 'wayland', 'x11', 'offscreen'), default='auto')

    check = sub.add_parser('verify', parents=[common], help='run Linux host tests')
    check.add_argument('--build', action='store_true', help='build before testing')
    check.add_argument('--require-vulkan', action='store_true', help='fail if the Vulkan test is skipped')
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == 'doctor':
        return print_doctor(doctor_report(), args.json)
    if args.command == 'build':
        return configure_and_build(args)
    if args.command == 'run':
        return run_demo(args)
    if args.command == 'verify':
        return verify(args)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
