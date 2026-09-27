#include "policy.h"
#include <cassert>

int main() {
    assert(
        policy::prefix_matches(L"/tmp/企业微信 前缀", L"/tmp/企业微信 前缀"));
    assert(!policy::prefix_matches(L"/tmp/other", L"/tmp/wecom"));
    assert(!policy::prefix_matches(L"", L""));
    assert(!policy::prefix_matches(L"relative", L"relative"));
    assert(policy::local_drive_path(L"C:\\目录 有空格\\图片.png"));
    assert(policy::local_drive_path(L"z:/tmp/picture.png"));
    assert(!policy::local_drive_path(L"\\\\server\\share\\image.png"));
    assert(!policy::local_drive_path(L"C:relative.png"));
    assert(!policy::local_drive_path(L"/tmp/image.png"));
    assert(!policy::local_drive_path(L""));
    assert(!policy::local_drive_path(std::wstring(32767, L'a')));
    for (int bits = 0; bits < 8; ++bits) {
        const bool result =
            policy::eligible_formats(bits & 1, bits & 2, bits & 4);
        assert(result == (bits == 3));
    }
    assert(!policy::bounded_bytes(-1));
    assert(!policy::bounded_bytes(0));
    assert(policy::bounded_bytes(1));
    assert(policy::bounded_bytes(policy::byte_limit));
    assert(!policy::bounded_bytes(policy::byte_limit + 1));
    assert(policy::bounded_image(4000, 4000));
    assert(!policy::bounded_image(4001, 4000));
    assert(!policy::bounded_image(0, 1));
    assert(!policy::bounded_image(1, 0));
    assert(!policy::bounded_image(16385, 1));
    assert(!policy::bounded_image(1, 16385));
    assert(!policy::bounded_image(0xffffffff, 0xffffffff));
    const std::vector<unsigned> before{1, 13, 49300};
    assert(policy::contains_original_formats(
        std::vector<unsigned>{1, 13, 49300, 49301}, before));
    assert(!policy::contains_original_formats(
        std::vector<unsigned>{1, 13, 49301}, before));
    assert(!policy::contains_original_formats(
        std::vector<unsigned>{49300, 1, 13}, before));
    return 0;
}
