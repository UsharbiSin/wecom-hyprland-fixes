#!/usr/bin/env bash
set -euo pipefail
module_dir=$(cd -- "$(dirname -- "$0")" && pwd)
build_root=${WECOM_XIM_BUILD_DIR:-"$module_dir/.build"}
output_dir=${1:-${WECOM_BUILD_DIR:-"$module_dir/artifacts"}}
mkdir -p "$build_root" "$output_dir"
build_root=$(cd "$build_root" && pwd)
output_dir=$(cd "$output_dir" && pwd)

fetch() {
    local url=$1 name=$2 sha=$3
    if [[ ! -f "$build_root/$name" ]]; then
        curl --fail --location --retry 2 "$url" -o "$build_root/$name"
    fi
    printf '%s  %s\n' "$sha" "$build_root/$name" | sha256sum --check --status
}

fetch https://www.x.org/releases/individual/lib/libX11-1.8.13.tar.xz \
    libX11-1.8.13.tar.xz \
    69606f485c2c07c14ef64f75b7bb326d48587af33795d9ab3e607c0b5f94f11c

# xtrans is a header-only build dependency.  Keep a missing dependency private
# rather than installing packages or replacing the system's X libraries.
if ! pkg-config --exists xtrans; then
    fetch https://www.x.org/releases/individual/lib/xtrans-1.6.0.tar.xz \
        xtrans-1.6.0.tar.xz \
        faafea166bf2451a173d9d593352940ec6404145c5d1da5c213423ce4d359e92
    tar -xf "$build_root/xtrans-1.6.0.tar.xz" -C "$build_root"
    (cd "$build_root/xtrans-1.6.0" &&
        ./configure --prefix="$build_root/deps" && make install)
    export PKG_CONFIG_PATH="$build_root/deps/share/pkgconfig"\
"${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
fi

tar -xf "$build_root/libX11-1.8.13.tar.xz" -C "$build_root"
patch --directory="$build_root/libX11-1.8.13" --strip=1 \
    < "$module_dir/xim-instantiate-per-display.patch"
mkdir -p "$build_root/build"
(cd "$build_root/build" &&
    ../libX11-1.8.13/configure --prefix=/usr --libdir=/usr/lib \
        --disable-static --disable-specs --disable-devel-docs \
        --without-xmlto --without-fop &&
    make -j"${WECOM_BUILD_JOBS:-4}")
install -m 755 "$build_root/build/src/.libs/libX11.so.6.4.0" \
    "$output_dir/libX11.so.6.4.0"
sha256sum "$output_dir/libX11.so.6.4.0"
