#!/usr/bin/env bash
set -euo pipefail
: "${WECOM_BUILD_DIR:?请通过 wecom-fix build 调用}"
for arch in i386 x86_64; do
  test -f "/usr/lib/wine/$arch-windows/explorer.exe" || {
    printf '缺少原始 Wine Explorer：%s\n' "$arch" >&2
    exit 1
  }
done
mkdir -p "$WECOM_BUILD_DIR/bin"
for arch in i686 x86_64; do
  bits=32
  test "$arch" = i686 || bits=64
  "$arch-w64-mingw32-gcc" -Wall -Wextra -Werror -O2 \
    -municode -mwindows explorer-proxy.c \
    -o "$WECOM_BUILD_DIR/explorer-proxy-$bits.exe"
done
cp thunar-bridge.py "$WECOM_BUILD_DIR/"
cp bin/xdg-open "$WECOM_BUILD_DIR/bin/"
chmod +x "$WECOM_BUILD_DIR/bin/xdg-open"
printf '%s\n' '已构建 32/64 位目录转接器；未修改 MIME 默认程序。'
