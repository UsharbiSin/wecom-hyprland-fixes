#pragma once

namespace screencast_policy {
// 本桥生成 RGB888 像素，仅接管已验证的默认真彩色布局。
inline bool supported_visual(int depth, unsigned long red,
                             unsigned long green, unsigned long blue) {
    return (depth == 24 || depth == 32) && red == 0xff0000 &&
           green == 0xff00 && blue == 0xff;
}

inline bool full_plane_mask(int depth, unsigned long mask) {
    if (depth != 24 && depth != 32)
        return false;
    const unsigned long required = depth == 32 ? 0xffffffffUL : 0xffffffUL;
    return (mask & required) == required;
}
} // namespace screencast_policy
