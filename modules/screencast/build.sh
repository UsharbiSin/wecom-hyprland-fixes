#!/usr/bin/env bash
set -euo pipefail
: "${WECOM_BUILD_DIR:?必须提供独立构建目录}"
mkdir -p "$WECOM_BUILD_DIR"
dependency_flags=$(pkg-config --cflags --libs \
    libportal libpipewire-0.3 x11)
read -r -a flags <<< "$dependency_flags"
g++ -std=c++20 -O2 -Wall -Wextra -Werror \
    check-state.cpp -o "$WECOM_BUILD_DIR/check-state" \
    "${flags[@]}" -ldl -pthread
"$WECOM_BUILD_DIR/check-state"
g++ -std=c++20 -O2 -Wall -Wextra -Werror -shared -fPIC \
    portal-capture.cpp -o "$WECOM_BUILD_DIR/libwecom-screencast.so.new" \
    "${flags[@]}" -ldl -pthread
mv "$WECOM_BUILD_DIR/libwecom-screencast.so.new" \
    "$WECOM_BUILD_DIR/libwecom-screencast.so"
