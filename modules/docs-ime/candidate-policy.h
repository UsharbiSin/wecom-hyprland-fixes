/* SPDX-License-Identifier: LGPL-2.1-or-later */
#ifndef WECOM_DOCS_IME_CANDIDATE_POLICY_H
#define WECOM_DOCS_IME_CANDIDATE_POLICY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define WECOM_IME_CANDIDATE_MAGIC UINT32_C(0x57494350)
#define WECOM_IME_COORDINATE_LIMIT INT32_C(1048576)
#define WECOM_IME_MAX_LINE_HEIGHT UINT32_C(512)

/* Coordinates are signed screen pixels, not Chromium DIPs. No pointers or
 * text cross this reverse channel. Keep this layout identical on both sides.
 */
struct wecom_ime_candidate_packet {
    uint32_t magic, size, token;
    uint32_t source_pid, source_tid, target_pid, target_tid;
    uint32_t focus, root;
    int32_t x, y;
    uint32_t line_height;
};

_Static_assert(sizeof(struct wecom_ime_candidate_packet) == 48,
               "candidate packet ABI changed");

static inline bool wecom_ime_candidate_packet_valid(const void *buffer,
                                                   size_t bytes)
{
    if (!buffer || bytes != sizeof(struct wecom_ime_candidate_packet))
        return false;
    const struct wecom_ime_candidate_packet *p = buffer;
    return p->magic == WECOM_IME_CANDIDATE_MAGIC && p->size == bytes &&
        p->token && p->source_pid && p->source_tid &&
        p->target_pid && p->target_tid && p->source_pid != p->target_pid &&
        p->focus && p->root && p->focus != p->root &&
        p->line_height && p->line_height <= WECOM_IME_MAX_LINE_HEIGHT &&
        p->x >= -WECOM_IME_COORDINATE_LIMIT &&
        p->x < WECOM_IME_COORDINATE_LIMIT &&
        p->y >= -WECOM_IME_COORDINATE_LIMIT &&
        (int64_t)p->y + p->line_height < WECOM_IME_COORDINATE_LIMIT;
}

static inline bool wecom_ime_candidate_in_bounds(
    const struct wecom_ime_candidate_packet *p,
    int32_t left, int32_t top, int32_t right, int32_t bottom)
{
    /* A partly visible last line is valid; the candidate can extend below
     * the client, and the native input method handles screen boundaries.
     */
    return p && left < right && top < bottom &&
        p->x >= left && p->x < right && p->y >= top && p->y < bottom;
}

struct wecom_ime_candidate_route {
    uint32_t token, source_pid, source_tid, focus, root;
    uint32_t root_pid, root_tid, focus_pid, focus_tid;
    uint32_t sender_pid, sender_tid;
    bool focus_matches, chain_matches, receiver_matches;
};

static inline bool wecom_ime_candidate_route_valid(
    const struct wecom_ime_candidate_packet *p,
    const struct wecom_ime_candidate_route *r)
{
    return p && r && wecom_ime_candidate_packet_valid(p, sizeof(*p)) &&
        p->token == r->token && p->source_pid == r->source_pid &&
        p->source_tid == r->source_tid && p->focus == r->focus &&
        p->root == r->root && p->source_pid == r->root_pid &&
        p->source_tid == r->root_tid && p->target_pid == r->focus_pid &&
        p->target_tid == r->focus_tid && p->target_pid == r->sender_pid &&
        p->target_tid == r->sender_tid && r->focus_matches &&
        r->chain_matches && r->receiver_matches;
}

#endif
