#!/usr/bin/env bash
set -euo pipefail
: "${WECOM_BUILD_DIR:?请通过 wecom-fix build 调用}"
mkdir -p "$WECOM_BUILD_DIR"
i686-w64-mingw32-gcc -Wall -Wextra -Werror -O2 \
  shadow-guard.c -o "$WECOM_BUILD_DIR/shadow-guard.exe" -luser32
cp hyprland.lua "$WECOM_BUILD_DIR/hyprland.lua"
printf '%s\n' '已构建装饰层辅助程序；Hyprland 片段需手动加载。'
