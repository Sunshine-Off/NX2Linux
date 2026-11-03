#!/usr/bin/env bash
# Install the NX2Linux recompiler toolchain at pinned versions, outside the repository:
#   Remill   lifting-bits/remill @ 56918a8 + fork en cours/remill-patches/*.patch (git am)
#   dynarmic azahar-emu/dynarmic @ a466015, with its submodules, built with -fPIC (the JIT fallback)
#   LLVM     the system one, checked against the version the work was validated with (22.x)
# Usage: tools/setup_toolchain.sh [PREFIX]   (default: $NX2_TOOLCHAIN or ~/.cache/nx2linux/toolchain)
# Needs: git cmake ninja clang clang++ llvm-config (system LLVM dev packages), and either the Boost headers or dnf + rpm2cpio (to unpack them without root).
# Ends by writing PREFIX/env.sh, which exports REMILL_BUILD and DYNARMIC_DIR, and by printing a verification.
set -euo pipefail

REMILL_COMMIT=56918a8
DYNARMIC_COMMIT=a466015
LLVM_MAJOR=22

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
prefix=$(realpath -m "${1:-${NX2_TOOLCHAIN:-$HOME/.cache/nx2linux/toolchain}}")
patches="$here/fork en cours/remill-patches"
jobs=${JOBS:-$(nproc)}
say() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

say "tools"
for t in git cmake ninja clang clang++ llvm-config; do command -v "$t" >/dev/null || die "$t not found (install it first)"; done
llvm=$(llvm-config --version)
echo "LLVM $llvm, $(clang++ --version | head -1)"
[ "${llvm%%.*}" = "$LLVM_MAJOR" ] || [ -n "${NX2_ALLOW_LLVM:-}" ] || die "LLVM $llvm: this work was validated with LLVM $LLVM_MAJOR.x. Set NX2_ALLOW_LLVM=1 to try anyway."
[ -d "$patches" ] || die "no patch folder $patches"
mkdir -p "$prefix"

say "Remill $REMILL_COMMIT"
if [ ! -d "$prefix/remill/.git" ]; then git clone https://github.com/lifting-bits/remill.git "$prefix/remill"; fi
cd "$prefix/remill"
if [ ! -e .nx2-patched ]; then
  git checkout -q "$REMILL_COMMIT" || die "commit $REMILL_COMMIT not found in the Remill checkout"
  git -c user.name=nx2linux -c user.email=nx2linux@localhost am --3way "$patches"/*.patch
  git rev-parse HEAD > .nx2-patched
else
  echo "already patched ($(git log --oneline -1))"
fi
if [ ! -e build/.nx2-built ]; then
  cmake -G Ninja -S dependencies -B dependencies/build -DUSE_EXTERNAL_LLVM=ON > deps-config.log 2>&1 || die "Remill dependencies: see $PWD/deps-config.log"
  cmake --build dependencies/build > deps-build.log 2>&1 || die "Remill dependencies: see $PWD/deps-build.log"
  cmake -G Ninja -B build "-DCMAKE_PREFIX_PATH:PATH=$PWD/dependencies/install" -DCMAKE_BUILD_TYPE=Release > build-config.log 2>&1 || die "Remill configure: see $PWD/build-config.log"
  cmake --build build > build.log 2>&1 || die "Remill build: see $PWD/build.log"
  touch build/.nx2-built
fi
lift=$(ls "$PWD"/build/bin/remill-lift-* 2>/dev/null | head -1) || true
[ -n "$lift" ] || lift=$(find "$PWD/build" -name 'remill-lift-*' -type f -perm -u+x | head -1)
[ -n "$lift" ] || die "remill-lift not built"
echo "lifter: $lift"

say "Boost headers (dynarmic)"
boostinc=""
if [ -d /usr/include/boost ]; then
  echo "system Boost"
else
  if [ ! -d "$prefix/boost/usr/include/boost" ]; then
    command -v dnf >/dev/null && command -v rpm2cpio >/dev/null || die "no Boost headers (/usr/include/boost) and no dnf+rpm2cpio to unpack them: install boost-devel"
    mkdir -p "$prefix/boost"; (cd "$prefix/boost" && dnf download --destdir . boost-devel.x86_64 && rpm2cpio boost-devel-*.x86_64.rpm | cpio -idm --quiet)
  fi
  boostinc="$prefix/boost/usr/include"; echo "unpacked Boost: $boostinc"
fi

say "dynarmic $DYNARMIC_COMMIT"
if [ ! -d "$prefix/dynarmic/.git" ]; then git clone https://github.com/azahar-emu/dynarmic.git "$prefix/dynarmic"; fi
cd "$prefix/dynarmic"
git checkout -q "$DYNARMIC_COMMIT" || die "commit $DYNARMIC_COMMIT not found in the dynarmic checkout"
git submodule update --init --recursive
if [ ! -e build/src/dynarmic/libdynarmic.a ]; then
  cmake -G Ninja -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DDYNARMIC_TESTS=OFF -DDYNARMIC_WARNINGS_AS_ERRORS=OFF -DDYNARMIC_USE_BUNDLED_EXTERNALS=ON ${boostinc:+"-DBoost_INCLUDE_DIR=$boostinc" "-DCMAKE_CXX_FLAGS=-isystem $boostinc"} > build-config.log 2>&1 || die "dynarmic configure: see $PWD/build-config.log"
  cmake --build build -j"$jobs" > build.log 2>&1 || die "dynarmic build: see $PWD/build.log"
fi
for f in src/dynarmic/libdynarmic.a externals/mcl/src/libmcl.a externals/fmt/libfmt.a externals/zydis/libZydis.a externals/zydis/zycore/libZycore.a; do
  [ -e "build/$f" ] || die "dynarmic: build/$f is missing (aot_lift.py links it)"
done

say "env.sh"
cat > "$prefix/env.sh" <<EOF
# made by tools/setup_toolchain.sh: Remill @ $REMILL_COMMIT (+ patches), dynarmic @ $DYNARMIC_COMMIT, LLVM $llvm
export REMILL_BUILD="$prefix/remill/build"
export DYNARMIC_DIR="$prefix/dynarmic"
${boostinc:+export CPLUS_INCLUDE_PATH="$boostinc\${CPLUS_INCLUDE_PATH:+:\$CPLUS_INCLUDE_PATH}"}
EOF
cat "$prefix/env.sh"
echo "done: source $prefix/env.sh"
