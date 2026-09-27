#!/usr/bin/env bash
set -euo pipefail
: "${WECOM_BUILD_DIR:?必须提供独立构建目录}"
: "${WECOM_WINE_SOURCE:?请通过 --wine-source 提供 Wine 11.17 源码目录}"
# 这里使用 Wine 内部 Unix ABI；必须与已验证的原始组件完全匹配。
qcap=9bc8291670e430ccefcb2688dd4477950555b1c4ae1dcc2cd3a8ba202fce1d41
ntdll=8a13d7042cef287802ea3bc60af40a7ee9def3130bb0d2be32c574693c9beb62
printf '%s  %s\n' \
    "$qcap" /usr/lib/wine/x86_64-unix/qcap.so \
    "$ntdll" /usr/lib/wine/x86_64-unix/ntdll.so | \
    sha256sum --check --status || {
    printf '%s\n' '当前 Wine 基础库不匹配 11.17，拒绝混用内部 ABI。' >&2
    exit 1
}
python3 prepare.py --source "$WECOM_WINE_SOURCE" \
    --output "$WECOM_BUILD_DIR/source"
gcc -shared -fPIC -O2 -Wall -D__WINESRC__ -DWINE_UNIX_LIB -D_WIN64 \
    -fshort-wchar -I/usr/include/wine/windows -I/usr/include \
    -I"$WECOM_BUILD_DIR/source" "$WECOM_BUILD_DIR/source/v4l.c" \
    -o "$WECOM_BUILD_DIR/qcap.so" -L/usr/lib/wine/x86_64-unix \
    -l:ntdll.so -ldl -Wl,-rpath,/usr/lib/wine/x86_64-unix
