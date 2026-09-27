#!/usr/bin/env bash
set -euo pipefail
: "${WECOM_BUILD_DIR:?必须提供独立构建目录}"
: "${WECOM_PREFIX:?必须提供企业微信专用 Wine 环境}"
app="$WECOM_PREFIX/drive_c/Program Files (x86)/WXWork"
version="${WECOM_APP_VERSION:-5.0.11.6018}"
python3 patch-libcef.py \
    --source "$app/$version/compatible_web/libcef.dll" \
    --output "$WECOM_BUILD_DIR/libcef.dll"
