#!/usr/bin/env bash
set -euo pipefail

: "${WECOM_BUILD_DIR:?请通过 wecom-fix build clipboard 调用}"
: "${WECOM_PREFIX:?缺少专用 Wine 前缀}"
module_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mkdir -p -- "$WECOM_BUILD_DIR"
build_dir=$(cd -- "$WECOM_BUILD_DIR" && pwd)

python3 "$module_dir/riched20/build.py" --output "$build_dir"
python3 "$module_dir/riched20/verify.py" --build-dir "$build_dir"
python3 "$module_dir/outbound/configure.py" \
    --prefix "$WECOM_PREFIX" --output "$build_dir/prefix-config.h"

g++ -std=c++17 -O2 -Wall -Wextra -Werror -Wpedantic \
    "$module_dir/outbound/check-policy.cpp" -o "$build_dir/check-policy"
"$build_dir/check-policy"

x86_64-w64-mingw32-g++ -std=c++17 -O2 -static \
    -Wall -Wextra -Werror -Wpedantic -municode -Wl,--no-insert-timestamp \
    -I "$build_dir" "$module_dir/outbound/wecom-png-outbound.cpp" \
    -o "$build_dir/wecom-png-outbound.exe" \
    -lgdiplus -lole32 -lshell32 -luser32
python3 "$module_dir/outbound/check-imports.py" \
    "$build_dir/wecom-png-outbound.exe"
printf '%s\n' '双向图片模块构建完成。'
