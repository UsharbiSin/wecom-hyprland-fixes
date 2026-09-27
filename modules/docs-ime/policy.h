/* SPDX-License-Identifier: LGPL-2.1-or-later */
#ifndef WECOM_DOCS_IME_POLICY_H
#define WECOM_DOCS_IME_POLICY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <wchar.h>

#define WECOM_IME_MAGIC UINT32_C(0x57494d45)
#define WECOM_IME_MAX_PACKET (256u * 1024u)
#define WECOM_IME_COMPOSITION_HEADER 100u

struct wecom_ime_packet {
    uint32_t magic, size, source_pid, source_tid;
    uint32_t focus, root;
    uint32_t comp_present, result_present;
    uint32_t comp_len, result_len, cursor;
    uint16_t strings[];
};

static inline bool wecom_ime_field_valid(uint32_t offset, uint32_t chars,
                                        uint32_t total)
{
    if (total < WECOM_IME_COMPOSITION_HEADER) return false;
    if (!offset) return !chars;
    return offset >= WECOM_IME_COMPOSITION_HEADER && !(offset & 1) &&
        offset <= total && chars <= (total - offset) / 2;
}

static inline bool wecom_ime_packet_valid(const void *buffer, size_t bytes)
{
    const size_t header = offsetof(struct wecom_ime_packet, strings);
    if (!buffer || bytes < header + 4 || bytes > WECOM_IME_MAX_PACKET ||
        (bytes - header) % 2) return false;
    const struct wecom_ime_packet *p = buffer;
    size_t chars = (bytes - header) / 2;
    if (p->magic != WECOM_IME_MAGIC || p->size != bytes ||
        p->comp_len > chars || p->result_len > chars ||
        (size_t)p->comp_len + p->result_len + 2 != chars ||
        p->comp_present > 1 || p->result_present > 1 ||
        (!p->comp_present && p->comp_len) ||
        (!p->result_present && p->result_len) ||
        p->cursor > p->comp_len || !p->focus || !p->root ||
        !p->source_pid || !p->source_tid) return false;
    const uint16_t *result = p->strings + p->comp_len + 1;
    if (p->strings[p->comp_len] || result[p->result_len]) return false;
    for (size_t i = 0; i < p->comp_len; ++i)
        if (!p->strings[i]) return false;
    for (size_t i = 0; i < p->result_len; ++i)
        if (!result[i]) return false;
    return true;
}

static inline bool wecom_ime_destination_valid(uint32_t source_pid,
    uint32_t target_pid, uint32_t root_pid, bool focus_matches,
    bool chain_matches)
{
    return source_pid && target_pid && source_pid != target_pid &&
        root_pid == source_pid && focus_matches && chain_matches;
}

static inline wchar_t wecom_ime_path_char(wchar_t ch)
{
    if (ch == L'/') return L'\\';
    return ch >= L'A' && ch <= L'Z' ? ch + L'a' - L'A' : ch;
}

static inline bool wecom_ime_absolute_path(const wchar_t *path)
{
    if (!path || !path[0] || !path[1] || !path[2] ||
        wecom_ime_path_char(path[0]) < L'a' ||
        wecom_ime_path_char(path[0]) > L'z' || path[1] != L':' ||
        wecom_ime_path_char(path[2]) != L'\\') return false;
    const wchar_t *part = path + 3;
    for (const wchar_t *p = part;; ++p) {
        if (*p && *p < 32) return false;
        if (*p != L'/' && *p != L'\\' && *p) continue;
        size_t length = (size_t)(p - part);
        if (!length || (length == 1 && *part == L'.') ||
            (length == 2 && part[0] == L'.' && part[1] == L'.'))
            return false;
        if (!*p) return true;
        part = p + 1;
    }
}

static inline bool wecom_ime_same_path(const wchar_t *left,
                                      const wchar_t *right)
{
    if (!wecom_ime_absolute_path(left) || !wecom_ime_absolute_path(right))
        return false;
    while (*left && *right)
        if (wecom_ime_path_char(*left++) != wecom_ime_path_char(*right++))
            return false;
    return !*left && !*right;
}
#endif
