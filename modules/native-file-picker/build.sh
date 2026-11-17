#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
target="${WECOM_BUILD_DIR:-../../build/native-file-picker}"
mkdir -p "$target"
x86_64-w64-mingw32-gcc -std=c11 -O2 -Wall -Wextra -Werror \
    -municode guard.c -o "$target/guard.exe" -lshell32 -lole32
cp bridge.py "$target/bridge.py"
