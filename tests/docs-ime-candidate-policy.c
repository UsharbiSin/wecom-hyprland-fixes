/* 合成候选框坐标及过期路由测试，不连接桌面或读取文档。 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../modules/docs-ime/candidate-policy.h"

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #condition); \
        return 1; \
    } \
} while (0)

static struct wecom_ime_candidate_packet sample(void)
{
    return (struct wecom_ime_candidate_packet){
        .magic = WECOM_IME_CANDIDATE_MAGIC,
        .size = sizeof(struct wecom_ime_candidate_packet),
        .token = 0x12345678,
        .source_pid = 320, .source_tid = 324,
        .target_pid = 648, .target_tid = 652,
        .focus = 0x1029e, .root = 0x100a8,
        .x = -720, .y = -160, .line_height = 28,
    };
}

static int packet_boundaries(void)
{
    struct wecom_ime_candidate_packet p = sample();
    CHECK(wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    CHECK(!wecom_ime_candidate_packet_valid(NULL, sizeof(p)));
    for (size_t bytes = 0; bytes < sizeof(p); ++bytes)
        CHECK(!wecom_ime_candidate_packet_valid(&p, bytes));
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p) + 1));
    CHECK(!wecom_ime_candidate_packet_valid(&p, SIZE_MAX));
    p.magic ^= 1;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p = sample();
    p.size--;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p.size = sizeof(p) + 1;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p.size = UINT32_MAX;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));

#define REJECT_ZERO(field) do { \
    p = sample(); \
    p.field = 0; \
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p))); \
} while (0)
    REJECT_ZERO(token);
    REJECT_ZERO(source_pid);
    REJECT_ZERO(source_tid);
    REJECT_ZERO(target_pid);
    REJECT_ZERO(target_tid);
    REJECT_ZERO(focus);
    REJECT_ZERO(root);
#undef REJECT_ZERO
    p = sample();
    p.target_pid = p.source_pid;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p = sample();
    p.focus = p.root;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    return 0;
}

static int signed_coordinates_and_line_height(void)
{
    struct wecom_ime_candidate_packet p = sample();
    /* 左侧及上方显示器的坐标必须保持有符号，不能裁成零。 */
    CHECK(p.x < 0 && p.y < 0);
    CHECK(wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p.x = p.y = -WECOM_IME_COORDINATE_LIMIT;
    CHECK(wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p.x--;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p = sample();
    p.y = -WECOM_IME_COORDINATE_LIMIT - 1;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p = sample();
    p.x = WECOM_IME_COORDINATE_LIMIT - 1;
    CHECK(wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p.x++;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));

    p = sample();
    p.line_height = 1;
    p.y = WECOM_IME_COORDINATE_LIMIT - 2;
    CHECK(wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p.y++;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p = sample();
    p.line_height = WECOM_IME_MAX_LINE_HEIGHT;
    CHECK(wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p.y = WECOM_IME_COORDINATE_LIMIT - (int32_t)p.line_height - 1;
    CHECK(wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p.y++;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p = sample();
    p.line_height = 0;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p.line_height = WECOM_IME_MAX_LINE_HEIGHT + 1;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    p.line_height = UINT32_MAX;
    CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));

    /* 极端值同时验证拒绝路径不会触发 UBSan 的有符号溢出。 */
    const int32_t extremes[] = {INT32_MIN, INT32_MAX};
    for (size_t i = 0; i < sizeof(extremes) / sizeof(extremes[0]); ++i) {
        p = sample();
        p.x = extremes[i];
        CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
        p = sample();
        p.y = extremes[i];
        CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
        p.line_height = UINT32_MAX;
        CHECK(!wecom_ime_candidate_packet_valid(&p, sizeof(p)));
    }
    return 0;
}

static int focused_client_bounds(void)
{
    struct wecom_ime_candidate_packet p = sample();
    CHECK(wecom_ime_candidate_in_bounds(&p, -1280, -720, 0, 0));
    CHECK(!wecom_ime_candidate_in_bounds(NULL, -1280, -720, 0, 0));
    CHECK(!wecom_ime_candidate_in_bounds(&p, 0, 0, 1280, 720));
    CHECK(!wecom_ime_candidate_in_bounds(&p, -720, -160, -720, 0));
    CHECK(!wecom_ime_candidate_in_bounds(&p, -1280, -160, 0, -160));
    CHECK(!wecom_ime_candidate_in_bounds(&p, 0, -720, -1280, 0));
    CHECK(!wecom_ime_candidate_in_bounds(&p, -1280, 0, 0, -720));
    p.x = -1280;
    p.y = -720;
    CHECK(wecom_ime_candidate_in_bounds(&p, -1280, -720, 0, 0));
    p.x--;
    CHECK(!wecom_ime_candidate_in_bounds(&p, -1280, -720, 0, 0));
    p.x = -1280;
    p.y--;
    CHECK(!wecom_ime_candidate_in_bounds(&p, -1280, -720, 0, 0));
    p.x = p.y = -1;
    CHECK(wecom_ime_candidate_in_bounds(&p, -1280, -720, 0, 0));
    /* 末行部分可见时，允许行高延伸出客户区。 */
    CHECK(p.y + (int32_t)p.line_height > 0);
    p.x = 0;
    CHECK(!wecom_ime_candidate_in_bounds(&p, -1280, -720, 0, 0));
    p.x = -1;
    p.y = 0;
    CHECK(!wecom_ime_candidate_in_bounds(&p, -1280, -720, 0, 0));
    p = sample();
    CHECK(wecom_ime_candidate_in_bounds(&p, INT32_MIN, INT32_MIN,
                                       INT32_MAX, INT32_MAX));
    return 0;
}

static struct wecom_ime_candidate_route current_route(
    const struct wecom_ime_candidate_packet *p)
{
    return (struct wecom_ime_candidate_route){
        .token = p->token,
        .source_pid = p->source_pid, .source_tid = p->source_tid,
        .focus = p->focus, .root = p->root,
        .root_pid = p->source_pid, .root_tid = p->source_tid,
        .focus_pid = p->target_pid, .focus_tid = p->target_tid,
        .sender_pid = p->target_pid, .sender_tid = p->target_tid,
        .focus_matches = true, .chain_matches = true,
        .receiver_matches = true,
    };
}

static int stale_routes_and_reused_handles(void)
{
    struct wecom_ime_candidate_packet p = sample();
    struct wecom_ime_candidate_route route = current_route(&p);
    CHECK(wecom_ime_candidate_route_valid(&p, &route));
    CHECK(!wecom_ime_candidate_route_valid(NULL, &route));
    CHECK(!wecom_ime_candidate_route_valid(&p, NULL));

    /* 异步返回之前守护进程重启、焦点切换、窗口句柄复用或发送端
       所属线程改变，都必须逐项拒绝，不能仅凭 HWND 相等放行。 */
#define REJECT_CHANGED(field) do { \
    route = current_route(&p); \
    route.field ^= 1; \
    CHECK(!wecom_ime_candidate_route_valid(&p, &route)); \
} while (0)
    REJECT_CHANGED(token);
    REJECT_CHANGED(source_pid);
    REJECT_CHANGED(source_tid);
    REJECT_CHANGED(focus);
    REJECT_CHANGED(root);
    REJECT_CHANGED(root_pid);
    REJECT_CHANGED(root_tid);
    REJECT_CHANGED(focus_pid);
    REJECT_CHANGED(focus_tid);
    REJECT_CHANGED(sender_pid);
    REJECT_CHANGED(sender_tid);
#undef REJECT_CHANGED
    route = current_route(&p);
    route.focus_matches = false;
    CHECK(!wecom_ime_candidate_route_valid(&p, &route));
    route = current_route(&p);
    route.chain_matches = false;
    CHECK(!wecom_ime_candidate_route_valid(&p, &route));
    route = current_route(&p);
    route.receiver_matches = false;
    CHECK(!wecom_ime_candidate_route_valid(&p, &route));

    /* 匹配的身份也不能绕过坐标包自身的格式检查。 */
    route = current_route(&p);
    p.size--;
    CHECK(!wecom_ime_candidate_route_valid(&p, &route));
    p = sample();
    p.line_height = UINT32_MAX;
    CHECK(!wecom_ime_candidate_route_valid(&p, &route));
    p = sample();
    p.y = INT32_MAX;
    CHECK(!wecom_ime_candidate_route_valid(&p, &route));
    return 0;
}

int main(void)
{
    if (packet_boundaries() || signed_coordinates_and_line_height() ||
        focused_client_bounds() || stale_routes_and_reused_handles()) return 1;
    puts("docs-ime candidate coordinate and routing checks passed");
    return 0;
}
