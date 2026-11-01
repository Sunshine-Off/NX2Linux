"""Regression check for the real demo's exit-status handling, with link-time stubs."""
import os
import subprocess
import sys

for mode, code, message in [
    ('quit', 0, 'Rendering triangle'),
    ('resize-failure', 1, 'Could not recreate Vulkan swapchain'),
    ('draw-recreate-failure', 1, 'Could not recreate Vulkan swapchain'),
    ('poll-failure', 1, 'Could not poll host events'),
    ('suspended', 0, 'Rendering triangle'),
]:
    result = subprocess.run([sys.argv[1]], env=dict(os.environ, NX2LINUX_TEST_MODE=mode),
                            text=True, capture_output=True, timeout=5)
    if result.returncode != code or message not in result.stdout + result.stderr:
        raise SystemExit(f'{mode}: expected exit {code}, got {result.returncode}\n{result.stdout}{result.stderr}')
    print(f'{mode}: correct exit {code}')
