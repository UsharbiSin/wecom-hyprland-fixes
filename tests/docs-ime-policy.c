/* 使用合成消息测试协议与路由，不连接 X11、Wine 或企业微信。 */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#include "../modules/docs-ime/policy.h"

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #condition); \
        return 1; \
    } \
} while (0)

/* 两字预编辑 + 一枚非 BMP 字符；两个串均有独立终止符。 */
static struct wecom_ime_packet *sample(void *buffer)
{
    struct wecom_ime_packet *packet = buffer;
    memset(buffer, 0, 256);
    packet->magic = WECOM_IME_MAGIC;
    packet->size = offsetof(struct wecom_ime_packet, strings) + 12;
    packet->source_pid = 320;
    packet->source_tid = 324;
    packet->focus = 0x1029e;
    packet->root = 0x100a8;
    packet->comp_present = 1;
    packet->result_present = 1;
    packet->comp_len = 2;
    packet->result_len = 2;
    packet->cursor = 2;
    packet->strings[0] = 'n';
    packet->strings[1] = 'i';
    packet->strings[3] = 0xd83d;
    packet->strings[4] = 0xde00;
    return packet;
}

static int packet_boundaries(void)
{
    union { max_align_t alignment; unsigned char bytes[256]; } buffer;
    struct wecom_ime_packet *packet = sample(buffer.bytes);
    const size_t header = offsetof(struct wecom_ime_packet, strings);
    CHECK(wecom_ime_packet_valid(packet, packet->size));
    CHECK(!wecom_ime_packet_valid(NULL, 0));
    for (size_t size = 0; size < packet->size; ++size)
        CHECK(!wecom_ime_packet_valid(packet, size));
    CHECK(!wecom_ime_packet_valid(packet, packet->size + 1));

    packet->magic ^= 1;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->size--;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->comp_len = UINT32_MAX;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->result_len = UINT32_MAX;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->comp_present = 2;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->result_present = 2;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->comp_present = 0; /* 不能隐藏仍携带内容的预编辑串。 */
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->result_present = 0;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->cursor = 3;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->strings[2] = 'x';
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->strings[5] = 'x';
    CHECK(!wecom_ime_packet_valid(packet, packet->size));

    packet = sample(buffer.bytes);
    packet->strings[0] = 0; /* 内嵌NUL不能隐去报文尾部另一段文本。 */
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->strings[3] = 0;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->source_pid = 0;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->source_tid = 0;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->focus = 0;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));
    packet = sample(buffer.bytes);
    packet->root = 0;
    CHECK(!wecom_ime_packet_valid(packet, packet->size));

    /* 空串和缺省串都存在于现场取消/结束事件，不能按长度误判。 */
    packet = sample(buffer.bytes);
    packet->comp_len = packet->result_len = packet->cursor = 0;
    packet->size = (uint32_t)header + 4;
    packet->strings[0] = packet->strings[1] = 0;
    CHECK(wecom_ime_packet_valid(packet, packet->size));
    packet->comp_present = packet->result_present = 0;
    CHECK(wecom_ime_packet_valid(packet, packet->size));

    /* 拒绝超长包，而不是因32位长度计算回绕把它当作短包。 */
    unsigned char *large = calloc(1, WECOM_IME_MAX_PACKET + 2);
    CHECK(large != NULL);
    struct wecom_ime_packet *limit = sample(large);
    limit->size = WECOM_IME_MAX_PACKET;
    limit->comp_len = (WECOM_IME_MAX_PACKET - (uint32_t)header) / 2 - 2;
    limit->result_len = limit->cursor = 0;
    limit->result_present = 0;
    for (uint32_t i = 0; i < limit->comp_len; ++i) limit->strings[i] = 'a';
    CHECK(wecom_ime_packet_valid(limit, limit->size));
    limit->size += 2;
    limit->comp_len++;
    CHECK(!wecom_ime_packet_valid(limit, limit->size));
    free(large);
    return 0;
}

static int composition_offsets(void)
{
    /* Wine 11.18 的 COMPOSITIONSTRING 为25个DWORD，即100字节。 */
    CHECK(wecom_ime_field_valid(0, 0, 100));
    CHECK(wecom_ime_field_valid(100, 0, 100));
    CHECK(wecom_ime_field_valid(100, 2, 104));
    CHECK(!wecom_ime_field_valid(0, 0, 99));
    CHECK(!wecom_ime_field_valid(0, 1, 104));
    CHECK(!wecom_ime_field_valid(101, 1, 104));
    CHECK(!wecom_ime_field_valid(96, 2, 104));
    CHECK(!wecom_ime_field_valid(99, 2, 104));
    CHECK(!wecom_ime_field_valid(100, 3, 104));
    CHECK(!wecom_ime_field_valid(106, 0, 104));
    CHECK(!wecom_ime_field_valid(100, UINT32_MAX, 104));
    CHECK(!wecom_ime_field_valid(UINT32_MAX, 1, UINT32_MAX));
    CHECK(!wecom_ime_field_valid(UINT32_MAX - 1, 2, UINT32_MAX));
    return 0;
}

static int destination_changes(void)
{
    CHECK(wecom_ime_destination_valid(320, 648, 320, 1, 1));
    CHECK(!wecom_ime_destination_valid(0, 648, 320, 1, 1));
    CHECK(!wecom_ime_destination_valid(320, 0, 320, 1, 1));
    CHECK(!wecom_ime_destination_valid(320, 648, 0, 1, 1));
    CHECK(!wecom_ime_destination_valid(320, 320, 320, 1, 1));
    CHECK(!wecom_ime_destination_valid(320, 648, 999, 1, 1));
    CHECK(!wecom_ime_destination_valid(320, 648, 320, 1, 0));
    /* 合成开始后的真实焦点变化必须在接收端再次拒绝。 */
    CHECK(!wecom_ime_destination_valid(320, 648, 320, 0, 1));
    CHECK(!wecom_ime_destination_valid(320, 648, 320, 0, 0));
    return 0;
}

static int executable_identity(void)
{
    const wchar_t *known =
        L"C:\\Program Files (x86)\\WXWork\\5.0.11.6018\\WXWorkWeb.exe";
    CHECK(wecom_ime_same_path(known, known));
    CHECK(wecom_ime_same_path(known,
        L"c:/program files (x86)/wxwork/5.0.11.6018/wxworkweb.EXE"));
    CHECK(!wecom_ime_same_path(known, L"WXWorkWeb.exe"));
    CHECK(!wecom_ime_same_path(known, L"C:WXWorkWeb.exe"));
    CHECK(!wecom_ime_same_path(known,
        L"C:\\Program Files (x86)\\WXWork\\5.0.11.6018\\WXWorkWeb.exe.other"));
    CHECK(!wecom_ime_same_path(known,
        L"C:\\Program Files (x86)\\other\\WXWorkWeb.exe"));
    CHECK(!wecom_ime_same_path(known,
        L"C:\\Program Files (x86)\\WXWork\\other\\..\\"
        L"5.0.11.6018\\WXWorkWeb.exe"));
    CHECK(!wecom_ime_same_path(L"WXWorkWeb.exe", L"WXWorkWeb.exe"));
    CHECK(!wecom_ime_same_path(L"C:WXWorkWeb.exe", L"C:WXWorkWeb.exe"));
    CHECK(!wecom_ime_same_path(L"", L""));
    CHECK(!wecom_ime_same_path(NULL, known));
    CHECK(!wecom_ime_same_path(known, NULL));
    CHECK(wecom_ime_absolute_path(known));
    CHECK(!wecom_ime_absolute_path(L"C:"));
    CHECK(!wecom_ime_absolute_path(L"C:\\"));
    CHECK(!wecom_ime_absolute_path(L"C:\\folder\\.\\app.exe"));
    CHECK(!wecom_ime_absolute_path(L"C:\\folder\\..\\app.exe"));
    CHECK(!wecom_ime_absolute_path(L"C:\\folder\napp.exe"));
    CHECK(!wecom_ime_absolute_path(L"C:\\folder\\\\app.exe"));
    return 0;
}

int main(void)
{
    if (packet_boundaries() || composition_offsets() ||
        destination_changes() || executable_identity()) return 1;
    puts("docs-ime protocol, composition bounds and destination checks passed");
    return 0;
}
