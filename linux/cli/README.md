# NX2Linux CLI

The S5 command line interface is independent of Qt and drives the Linux host
build and Vulkan smoke tests.

From the repository root:

```sh
python3 linux/cli/nx2linux.py doctor
python3 linux/cli/nx2linux.py build -j2
python3 linux/cli/nx2linux.py verify --require-vulkan
python3 linux/cli/nx2linux.py run
```

`verify` accepts a skipped Vulkan test by default so it can run in headless CI.
`--require-vulkan` turns that skip into a failure, which is useful on a real
desktop or GPU test runner.
