#!/usr/bin/env bash
set -euo pipefail
: "${WECOM_BUILD_DIR:?必须提供独立构建目录}"
: "${WECOM_PREFIX:?必须提供企业微信专用 Wine 前缀}"
version="${WECOM_APP_VERSION:-5.0.11.6018}"
python3 start.py --prefix "$WECOM_PREFIX" \
    --app-version "$version" --verify-only
cc=i686-w64-mingw32-gcc
"$cc" -shared -O2 -Wall -Wextra -Werror bridge.c \
    -o "$WECOM_BUILD_DIR/docs-ime-bridge.dll" \
    -Wl,--kill-at -limm32 -luser32
"$cc" -O2 -Wall -Wextra -Werror -municode guard.c \
    -o "$WECOM_BUILD_DIR/docs-ime-guard.exe" -luser32
cp start.py module.json "$WECOM_BUILD_DIR/"
