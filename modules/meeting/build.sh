#!/usr/bin/env bash
set -euo pipefail
: "${WECOM_BUILD_DIR:?请通过统一入口构建}"
: "${WECOM_APP_VERSION:?缺少企业微信版本}"
if [[ "$WECOM_APP_VERSION" != 5.0.11.6018 ]]; then
    echo '会议入口仅核验过企业微信 5.0.11.6018。' >&2
    exit 1
fi
mkdir -p "$WECOM_BUILD_DIR"
i686-w64-mingw32-gcc -Wall -Wextra -Werror -shared -Os \
    -Wl,--kill-at,--no-insert-timestamp \
    -o "$WECOM_BUILD_DIR/SLWGA.dll" slwga-compat.c
