"""NX2Linux game build: one library and one command for NSP to build directory.

Steps, in order: check (the tools and files the later steps need), extract (nx_extract), modules (one ELF module and manifest per NSO), manifest (build.json), recompile (the AOT cache).
Each step reports progress as events, is skipped when its recorded inputs and outputs are unchanged (resumable), and fails with a BuildError that says what is missing and how to fix it.
The Qt interface and CI use `run_build` with their own `on_event`. Nothing of the game is written inside the repository."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import nx2linux

FORMAT = 'nx2linux-build-1'
STEPS = ('check', 'extract', 'modules', 'manifest', 'recompile')
TOOLS = nx2linux.ROOT / 'engine' / 'dm' / 'tools'
EXIT = {'usage': 2, 'missing': 3, 'failed': 4}
NSO_MAGIC = b'NSO0'


class BuildError(Exception):
    """A step cannot run or failed. `code` is the exit status of the command, `step` the step that stopped"""
    def __init__(self, step: str, message: str, code: int = EXIT['failed']):
        super().__init__(message)
        self.step, self.code = step, code


@dataclass
class Options:
    nsp: Path
    keys: Path
    out: Optional[Path] = None
    update: Optional[Path] = None
    jit: bool = False
    skip_recompile: bool = False
    stop_after: Optional[str] = None
    force: bool = False
    cache: Optional[Path] = None
    seconds: float = 0.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ''
    needed_for: tuple = ()


def lifter() -> Optional[str]:
    root = os.environ.get('REMILL_BUILD')
    return next(iter(sorted(glob.glob(os.path.join(root, 'bin/lift/remill-lift-*')))), None) if root else None


def prerequisites(jit: bool = False, keys: Optional[Path] = None) -> list[Check]:
    """What the build needs, named the way `doctor` names it. Shared by `build` and `doctor` so they report the same gaps"""
    checks = []
    py = nx2linux.worker_python()
    missing = nx2linux.missing_worker_modules(py)
    checks.append(Check('worker-python', not missing, f'{py} imports capstone, unicorn, lz4' if not missing else f'{py} cannot import {", ".join(missing)}',
                        'run tools/setup_toolchain.sh, it makes the venv', ('modules', 'recompile')))
    checks.append(Check('dotnet', shutil.which('dotnet') is not None, shutil.which('dotnet') or 'dotnet is missing',
                        'install dotnet-sdk-10.0', ('extract',)))
    synced = (nx2linux.NX_EXTRACT / 'nx_extract.csproj').is_file()
    checks.append(Check('engine', synced, str(nx2linux.ROOT / 'engine') if synced else 'engine/ is not synced', 'run python3 tools/engine.py sync', ('extract', 'modules', 'recompile')))
    if keys is not None:
        k = keys / 'prod.keys' if keys.is_dir() else keys
        checks.append(Check('keys', k.is_file(), str(k) if k.is_file() else f'no keys file at {k}', 'pass your own prod.keys with --keys', ('extract',)))
    found = lifter()
    checks.append(Check('remill', found is not None, found or ('REMILL_BUILD is not set' if not os.environ.get('REMILL_BUILD') else f'no remill-lift under {os.environ["REMILL_BUILD"]}/bin/lift'),
                        'run tools/setup_toolchain.sh and source its env.sh (it exports REMILL_BUILD)', ('recompile',)))
    dyn = os.environ.get('DYNARMIC_DIR')
    dyn_ok = bool(dyn) and os.path.exists(os.path.join(dyn, 'build/src/dynarmic/libdynarmic.a'))
    checks.append(Check('dynarmic', dyn_ok, dyn if dyn_ok else ('DYNARMIC_DIR is not set' if not dyn else f'no build/src/dynarmic/libdynarmic.a under {dyn}'),
                        'run tools/setup_toolchain.sh and source its env.sh (it exports DYNARMIC_DIR)', ('recompile (--jit only)',)))
    return checks


def require(checks: list[Check], step: str, jit: bool = False) -> None:
    for c in checks:
        needed = step in c.needed_for or (jit and step == 'recompile' and c.name == 'dynarmic')
        if needed and not c.ok:
            raise BuildError('check', f'{c.name}: {c.detail}. Fix: {c.fix}', EXIT['missing'])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def fingerprint(path: Path) -> str:
    """size and mtime: a 14 GB package is not hashed each run. A changed package changes both"""
    st = path.stat()
    return f'{st.st_size}:{st.st_mtime_ns}'


class Build:
    def __init__(self, opts: Options, on_event: Callable[[dict], None]):
        self.o, self.emit = opts, on_event
        self.keys = opts.keys / 'prod.keys' if opts.keys.is_dir() else opts.keys
        self.root: Optional[Path] = None
        self.state: dict = {'format': FORMAT, 'steps': {}}

    def event(self, step: str, status: str, **kw) -> None:
        self.emit({'step': step, 'status': status, **kw})

    # ---- the state file is the build manifest ----
    @property
    def manifest_path(self) -> Path: return self.root / 'build.json'

    def save(self) -> None:
        tmp = self.manifest_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True) + '\n')
        tmp.replace(self.manifest_path)

    def done(self, step: str, inputs: dict, outputs: list[Path]) -> bool:
        rec = self.state['steps'].get(step)
        return bool(rec) and not self.o.force and rec.get('inputs') == inputs and all(p.exists() for p in outputs)

    def mark(self, step: str, inputs: dict, **extra) -> None:
        self.state['steps'][step] = {'inputs': inputs, **extra}
        self.save()

    # ---- steps ----
    def run_check(self) -> None:
        for p, label in ((self.o.nsp, 'game package'),) + (((self.o.update, 'update package'),) if self.o.update else ()):
            if not p.is_file():
                raise BuildError('check', f'{label} not found: {p}', EXIT['usage'])
        checks = prerequisites(self.o.jit, self.o.keys)
        for step in ('extract', 'modules') + (() if self.o.skip_recompile or self.o.stop_after in ('extract', 'modules', 'manifest') else ('recompile',)):
            require(checks, step, self.o.jit)
        if nx2linux.inside_repository(self.o.out or nx2linux.xdg_dir('cache')):
            raise BuildError('check', 'refusing to write game data inside the repository', EXIT['usage'])

    def title_id(self) -> str:
        r = subprocess.run(['dotnet', 'run', '-c', 'Release', '--project', str(nx2linux.NX_EXTRACT), '--', str(self.keys), '--title', str(self.o.nsp)], capture_output=True, text=True)
        found = nx2linux.TITLE_RE.search(r.stdout or '')
        if r.returncode != 0 or not found:
            raise BuildError('extract', 'the package has no readable title ID. Check that --keys is the prod.keys of this console generation and that the file is a complete NSP or XCI', r.returncode or 5)
        return found.group(1)

    def run_extract(self) -> None:
        inputs = {'nsp': fingerprint(self.o.nsp), 'update': fingerprint(self.o.update) if self.o.update else '', 'keys': fingerprint(self.keys)}
        outputs = [self.root / 'ExeFS', self.root / 'RomFS.img']
        if self.done('extract', inputs, outputs):
            return self.event('extract', 'skipped', reason='unchanged')
        self.event('extract', 'start')
        cmd = ['dotnet', 'run', '-c', 'Release', '--project', str(nx2linux.NX_EXTRACT), '--', str(self.keys), str(self.o.nsp), str(self.o.update) if self.o.update else '-', str(self.root), '--romfs-image']
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            self.event('extract', 'progress', line=line.rstrip())
        if p.wait() != 0:
            raise BuildError('extract', f'nx_extract exited with {p.returncode}. Its output is above', p.returncode)
        self.mark('extract', inputs)
        self.event('extract', 'done')

    def nso_files(self) -> list[Path]:
        out = []
        for p in sorted((self.root / 'ExeFS').iterdir()):
            if p.is_file() and p.name != 'main.npdm' and p.stat().st_size >= 0x100 and p.open('rb').read(4) == NSO_MAGIC:
                out.append(p)
        return out

    def run_modules(self) -> None:
        nsos = [p for p in self.nso_files() if p.name != 'rtld']   # rtld is the host's: the loader never loads it
        if not any(p.name == 'main' for p in nsos):
            raise BuildError('modules', f'ExeFS has no main module (found: {", ".join(p.name for p in nsos) or "nothing"})')
        inputs = {p.name: sha256(p) for p in nsos}
        outputs = [self.root / 'modules' / f'{p.name}.manifest.json' for p in nsos]
        if self.done('modules', inputs, outputs):
            return self.event('modules', 'skipped', reason='unchanged')
        self.event('modules', 'start', modules=[p.name for p in nsos])
        py = nx2linux.worker_python()
        for p in nsos:
            r = subprocess.run([py, '-I', str(TOOLS / 'elf_module.py'), str(p), str(self.root / 'modules'), p.name], capture_output=True, text=True)
            if r.returncode != 0:
                raise BuildError('modules', f'converting {p.name} failed: {(r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else r.returncode}')
            self.event('modules', 'progress', module=p.name)
        self.mark('modules', inputs, order=[p.name for p in nsos])
        self.event('modules', 'done')

    def run_manifest(self) -> None:
        self.event('manifest', 'start')
        mods = {}
        for p in sorted((self.root / 'modules').glob('*.manifest.json')):
            m = json.loads(p.read_text())
            mods[m['name']] = {'manifest': p.name, 'elf': m['elf'], 'sha256': m['sha256'], 'build_id': m['build_id']}
        self.state.update({
            'title_id': self.state.get('title_id'), 'modules': mods,
            'romfs': 'RomFS.img', 'exefs': 'ExeFS',
            'tools': {'python': sys.version.split()[0], 'worker': nx2linux.worker_python(), 'remill': lifter(), 'dynarmic': os.environ.get('DYNARMIC_DIR')},
        })
        self.mark('manifest', {k: v['sha256'] for k, v in mods.items()})
        self.event('manifest', 'done', path=str(self.manifest_path))

    def run_recompile(self) -> None:
        inputs = {'modules': self.state['steps']['manifest']['inputs'], 'jit': self.o.jit, 'remill': lifter()}
        cache = self.o.cache or nx2linux.xdg_dir('cache') / 'aot-cache'
        if self.done('recompile', inputs, [self.root / 'build.json']):
            return self.event('recompile', 'skipped', reason='unchanged')
        self.event('recompile', 'start', cache=str(cache), note='the first build of a large game takes about 25 minutes and about 12 GB of cache')
        order = self.state['steps']['modules'].get('order') or sorted(self.state['modules'])
        cmd = [nx2linux.worker_python(), '-I', str(TOOLS / 'horizon_run.py'), '--modules', str(self.root / 'modules'), '--order', ','.join(order),
               '--romfs', str(self.root / 'RomFS.img'), '--title', self.state['title_id'], '--cache', str(cache), '--build-only'] + (['--jit'] if self.o.jit else [])
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=dict(os.environ))
        for line in p.stdout:
            self.event('recompile', 'progress', line=line.rstrip())
        if p.wait() != 0:
            raise BuildError('recompile', f'the recompilation exited with {p.returncode}. Its output is above')
        self.mark('recompile', inputs, cache=str(cache))
        self.event('recompile', 'done')

    # ---- driver ----
    def run(self) -> Path:
        self.event('check', 'start')
        self.run_check()
        self.event('check', 'done')
        if self.o.stop_after == 'check':
            return self.o.out or Path()
        base = self.o.out or nx2linux.xdg_dir('cache') / 'build'
        tid = self.title_id()
        self.root = base / tid
        self.root.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.is_file():
            try:
                old = json.loads(self.manifest_path.read_text())
                if old.get('format') == FORMAT: self.state = old
            except ValueError:
                pass   # a damaged build.json is rebuilt from the steps
        self.state['title_id'] = tid
        for step in STEPS[1:]:
            if step == 'recompile' and self.o.skip_recompile:
                self.event('recompile', 'skipped', reason='--skip-recompile')
                break
            getattr(self, f'run_{step}')()
            if self.o.stop_after == step:
                break
        self.save()
        return self.root


def run_build(opts: Options, on_event: Callable[[dict], None] = lambda e: None) -> Path:
    """Run the build; returns the build directory. Raises BuildError"""
    if opts.stop_after is not None and opts.stop_after not in STEPS:
        raise BuildError('check', f'--stop-after takes one of {", ".join(STEPS)}', EXIT['usage'])
    return Build(opts, on_event).run()


def print_event(e: dict) -> None:
    if e['status'] == 'progress':
        print(e.get('line') or f"[{e['step']}] {e.get('module', '')}", flush=True)
    else:
        extra = f" ({e['reason']})" if e.get('reason') else (f" {e['note']}" if e.get('note') else '')
        print(f"[{e['step']}] {e['status']}{extra}", flush=True)
