#pragma once

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

// 图片输出的前缀、路径、格式与资源限制。
namespace policy {
constexpr std::uint64_t byte_limit = 64u * 1024u * 1024u;

inline bool prefix_matches(const std::wstring &actual,
                           const std::wstring &configured) {
    return !configured.empty() && configured.front() == L'/' &&
           actual == configured;
}

inline bool local_drive_path(const std::wstring &path) {
    if (path.size() < 3 || path.size() >= 32767)
        return false;
    const bool drive = (path[0] >= L'A' && path[0] <= L'Z') ||
                       (path[0] >= L'a' && path[0] <= L'z');
    return drive && path[1] == L':' && (path[2] == L'\\' || path[2] == L'/');
}

inline bool eligible_formats(bool wecom, bool single_file_format, bool png) {
    return wecom && single_file_format && !png;
}

inline bool bounded_bytes(std::int64_t bytes) {
    return bytes > 0 && static_cast<std::uint64_t>(bytes) <= byte_limit;
}

inline bool bounded_image(std::uint32_t width, std::uint32_t height) {
    return width && height && width <= 16384 && height <= 16384 &&
           static_cast<std::uint64_t>(width) * height <= 16000000;
}

template <typename T>
inline bool contains_original_formats(const std::vector<T> &after,
                                      const std::vector<T> &before) {
    return std::is_sorted(after.begin(), after.end()) &&
           std::is_sorted(before.begin(), before.end()) &&
           std::includes(after.begin(), after.end(), before.begin(),
                         before.end());
}
} // namespace policy
