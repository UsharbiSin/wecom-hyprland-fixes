// 只构造合成状态；不创建 Portal、PipeWire、X11 连接，也不读取桌面。
#include "portal-capture.cpp"
#include <cassert>

int main() {
    using screencast_policy::full_plane_mask;
    using screencast_policy::supported_visual;
    assert(supported_visual(24, 0xff0000, 0xff00, 0xff));
    assert(supported_visual(32, 0xff0000, 0xff00, 0xff));
    assert(!supported_visual(16, 0xf800, 0x7e0, 0x1f));
    assert(!supported_visual(24, 0xff, 0xff00, 0xff0000));
    assert(full_plane_mask(24, ~0UL));
    assert(full_plane_mask(24, 0xffffffUL));
    assert(!full_plane_mask(24, 0xffUL));
    assert(!full_plane_mask(24, 0));
    assert(full_plane_mask(32, 0xffffffffUL));
    assert(!full_plane_mask(32, 0xffffffUL));
    assert(!full_plane_mask(16, ~0UL));

    Capture test;
    test.requested = now_ms();
    test.width = test.height = 1;
    test.pixels = {30, 20, 10, 0};
    state(&test, PW_STREAM_STATE_STREAMING, PW_STREAM_STATE_ERROR,
          "离线测试注入错误");
    assert(test.stream_failed.load());
    tick(&test);
    assert(test.blocked);
    assert(!test.stream_failed.load());
    assert(test.pixels.empty() && !test.width && !test.height);
    assert(!test.session && !test.loop && !test.pending);
    // 持续请求期间保持阻断，不反复弹出共享源选择框。
    tick(&test);
    assert(test.blocked);
    test.requested = now_ms() - 6000;
    tick(&test);
    assert(!test.blocked);
    std::puts("共享模块离线状态与视觉保护检查通过");
    return 0;
}
