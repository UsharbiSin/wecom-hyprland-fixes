/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Wine 11.18 private declarations from include/immdev.h and ntuser.h:
 * Copyright 2007 CodeWeavers, Aric Stewart
 * Copyright 2021 Jacek Caban for CodeWeavers
 * License: ../../licenses/LGPL-2.1-or-later.txt
 */
#ifndef WECOM_DOCS_IME_ABI_H
#define WECOM_DOCS_IME_ABI_H

#include <windows.h>
#include <imm.h>
#include <stdio.h>
#include <wchar.h>
#include "policy.h"
#include "candidate-policy.h"

typedef struct {
    DWORD dwSize;
    DWORD dwCompReadAttrLen, dwCompReadAttrOffset;
    DWORD dwCompReadClauseLen, dwCompReadClauseOffset;
    DWORD dwCompReadStrLen, dwCompReadStrOffset;
    DWORD dwCompAttrLen, dwCompAttrOffset;
    DWORD dwCompClauseLen, dwCompClauseOffset;
    DWORD dwCompStrLen, dwCompStrOffset;
    DWORD dwCursorPos, dwDeltaStart;
    DWORD dwResultReadClauseLen, dwResultReadClauseOffset;
    DWORD dwResultReadStrLen, dwResultReadStrOffset;
    DWORD dwResultClauseLen, dwResultClauseOffset;
    DWORD dwResultStrLen, dwResultStrOffset;
    DWORD dwPrivateSize, dwPrivateOffset;
} WINE_COMPOSITIONSTRING;

_Static_assert(sizeof(WINE_COMPOSITIONSTRING) == 100, "Wine ABI changed");
_Static_assert(sizeof(void *) == 4, "Only the verified i686 ABI is supported");

struct ime_driver_call_params {
    HIMC himc;
    const BYTE *state;
    WINE_COMPOSITIONSTRING *compstr;
    BOOL *key_consumed;
};
typedef LRESULT (WINAPI *NtMessageCall)(HWND, UINT, WPARAM, LPARAM,
                                      void *, DWORD, BOOL);
typedef ULONG_PTR (WINAPI *NtCallTwoParam)(ULONG_PTR, ULONG_PTR, ULONG);
#define WINE_IME_TO_ASCII_EX 0
#define NTUSER_IME_DRIVER_CALL 0x0305
#define NTUSER_SET_IME_COMPOSITION_RECT 6
#define IMN_WINE_SET_COMP_STRING 0x0010
#define STATUS_BUFFER_TOO_SMALL_VALUE ((LONG)0xc0000023)

#define IME_MAPPING L"Local\\WeComDocsImeConfig-v2"
/* Keep the singleton mutex stable across protocol upgrades. The old guard
 * must stop before a v2 guard can own this Wine prefix.
 */
#define IME_MUTEX L"Local\\WeComDocsImeGuard-v1"
#define IME_STOP L"Local\\WeComDocsImeStop-v2"
#define IME_DONE L"Local\\WeComDocsImeDone-v2"
#define IME_CLEAN L"Local\\WeComDocsImeClean-v2"
#define IME_PROPERTY L"WeComDocsImeReceiver-v2"
#define IME_SOURCE_PROPERTY L"WeComDocsImeSource-v2"
#define IME_CLASS L"WeComDocsImeIpc-v2"
#define IME_PRIME L"WeComDocsImePrime-v2"
#define IME_ACK L"WeComDocsImeAck-v2"
#define IME_READY L"WeComDocsImeReady-v2"
#define IME_SHUTDOWN L"WeComDocsImeShutdown-v2"
#define IME_POSITION L"WeComDocsImePosition-v2"
#define IME_CONFIG_MAGIC 0x57494346u
#define IME_PATH_MAX 1024

struct bridge_config {
    DWORD magic, size, owner_pid, source_pid, controller, token;
    WCHAR app_dir[IME_PATH_MAX];
    WCHAR app_version[64];
    WCHAR debug_log[IME_PATH_MAX];
    BOOL debug;
};

static inline BOOL ime_is_class(HWND window, const WCHAR *expected)
{
    WCHAR name[128];
    return GetClassNameW(window, name, 128) && !wcscmp(name, expected);
}

static inline BOOL ime_process_path(DWORD pid, WCHAR *path)
{
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                 FALSE, pid);
    DWORD size = IME_PATH_MAX;
    if (!process) return FALSE;
    BOOL ok = QueryFullProcessImageNameW(process, 0, path, &size);
    CloseHandle(process);
    return ok && size < IME_PATH_MAX;
}

static inline BOOL ime_expected_path(const struct bridge_config *cfg,
    DWORD pid, const WCHAR *suffix, BOOL versioned)
{
    WCHAR expected[IME_PATH_MAX], actual[IME_PATH_MAX];
    int count = versioned ? swprintf(expected, IME_PATH_MAX, L"%ls\\%ls\\%ls",
        cfg->app_dir, cfg->app_version, suffix) :
        swprintf(expected, IME_PATH_MAX, L"%ls\\%ls", cfg->app_dir, suffix);
    return count > 0 && count < IME_PATH_MAX &&
        ime_process_path(pid, actual) && wecom_ime_same_path(actual, expected);
}

static inline BOOL ime_source_window(const struct bridge_config *cfg,
                                    HWND root)
{
    DWORD pid = 0;
    GetWindowThreadProcessId(root, &pid);
    return (!cfg->source_pid || pid == cfg->source_pid) &&
        ime_is_class(root, L"WeWorkWindow") &&
        (ime_expected_path(cfg, pid, L"WXWork.exe", TRUE) ||
         ime_expected_path(cfg, pid, L"WXWork.exe", FALSE));
}

static inline BOOL ime_document_window(const struct bridge_config *cfg,
                                      HWND focus, HWND root)
{
    DWORD pid = 0;
    GetWindowThreadProcessId(focus, &pid);
    if (!ime_is_class(focus, L"Chrome_WidgetWin_0") ||
        !ime_expected_path(cfg, pid, L"compatible_web\\WXWorkWeb.exe", TRUE) ||
        GetAncestor(focus, GA_ROOT) != root || !ime_source_window(cfg, root))
        return FALSE;
    BOOL cef = FALSE, mail = FALSE;
    HWND parent = GetParent(focus);
    for (unsigned depth = 0; parent && parent != root && depth < 64; ++depth) {
        DWORD owner = 0;
        GetWindowThreadProcessId(parent, &owner);
        if (owner == pid && ime_is_class(parent, L"CefBrowserWindow"))
            cef = TRUE;
        WCHAR name[128] = L"";
        GetClassNameW(parent, name, 128);
        if (cef && name[0] == L'Q' && name[1] == L't' &&
            ime_expected_path(cfg, owner, L"wemail\\WeMail.exe", TRUE))
            mail = TRUE;
        parent = GetParent(parent);
    }
    return parent == root && cef && mail;
}
#endif
