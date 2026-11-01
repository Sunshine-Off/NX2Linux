"""Portable CLI tests plus the dependency-free Linux native host check.

GPU/SDL acceptance is separate: linux/cli/nx2linux.py verify --require-validation.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'linux/cli/tests', '-v'],
                   cwd=ROOT, check=True)
    if sys.platform != 'linux':
        print('Native Linux host execution requires Linux. The Linux CI job runs it.')
        return 0
    with tempfile.TemporaryDirectory(prefix='nx2linux-headless-check-') as directory:
        for command in [
            ['cmake', '-S', str(ROOT / 'linux/host'), '-B', directory,
             '-DNX2LINUX_GRAPHICS=OFF', '-DCMAKE_BUILD_TYPE=Debug'],
            ['cmake', '--build', directory, '--parallel', '2'],
            [sys.executable, str(ROOT / 'linux/cli/nx2linux.py'), 'verify', '--headless', '--build-dir', directory],
        ]:
            subprocess.run(command, cwd=ROOT, check=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
