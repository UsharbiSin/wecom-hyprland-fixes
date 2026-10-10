/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Wine 11.18/11.19 XIM updates are process local, even when the focused CEF
 * child belongs to another process. Transfer only verified document IME
 * updates and use public IMM APIs in the destination process. Return the
 * document's caret geometry to the source process, which owns the root XIC.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <imm.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include "bridge-abi.h"

static HINSTANCE instance;
static INIT_ONCE initialized = INIT_ONCE_STATIC_INIT;
static const struct bridge_config *config;
static HANDLE mapping, stop_event;
static NtMessageCall nt_call;
static NtCallTwoParam nt_two_param;
static UINT prime_message, ready_message, shutdown_message, ack_message;
static UINT position_message;
static LONG pinned;

static __thread struct {
    HWND receiver, marked_focus, marked_root, composing_focus, controller;
    HKL previous_layout, activated_layout;
    HANDLE owner;
    DWORD token;
    BOOL enabled, busy, position_busy, candidate_applied;
    struct wecom_ime_candidate_packet last_candidate;
} state;

static void debug_log(const char *format, ...)
{
    if (!config || !config->debug) return;
    char line[1024];
    int used = snprintf(line, sizeof(line), "docs-ime pid=%lu tid=%lu ",
                        GetCurrentProcessId(), GetCurrentThreadId());
    va_list args;
    va_start(args, format);
    int added = vsnprintf(line + used, sizeof(line) - used, format, args);
    va_end(args);
    if (added < 0) return;
    used += added;
    if (used > (int)sizeof(line) - 2) used = sizeof(line) - 2;
    line[used++] = '\r';
    line[used++] = '\n';
    HANDLE output = GetStdHandle(STD_ERROR_HANDLE);
    BOOL close_output = FALSE;
    if (config->debug_log[0]) {
        output = CreateFileW(config->debug_log, FILE_APPEND_DATA,
            FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL, NULL);
        close_output = TRUE;
    }
    if (output && output != INVALID_HANDLE_VALUE) {
        DWORD written;
        WriteFile(output, line, (DWORD)used, &written, NULL);
        if (close_output) CloseHandle(output);
    }
}

static BOOL CALLBACK initialize(PINIT_ONCE once, PVOID arg, PVOID *context)
{
    (void)once;
    (void)arg;
    (void)context;
    prime_message = RegisterWindowMessageW(IME_PRIME);
    ready_message = RegisterWindowMessageW(IME_READY);
    shutdown_message = RegisterWindowMessageW(IME_SHUTDOWN);
    ack_message = RegisterWindowMessageW(IME_ACK);
    position_message = RegisterWindowMessageW(IME_POSITION);
    HMODULE win32u = GetModuleHandleW(L"win32u.dll");
    if (win32u) {
        FARPROC address = GetProcAddress(win32u, "NtUserMessageCall");
        _Static_assert(sizeof(nt_call) == sizeof(address), "pointer ABI");
        memcpy(&nt_call, &address, sizeof(nt_call));
        address = GetProcAddress(win32u, "NtUserCallTwoParam");
        _Static_assert(sizeof(nt_two_param) == sizeof(address), "pointer ABI");
        memcpy(&nt_two_param, &address, sizeof(nt_two_param));
    }
    mapping = OpenFileMappingW(FILE_MAP_READ, FALSE, IME_MAPPING);
    if (mapping)
        config = MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, sizeof(*config));
    stop_event = OpenEventW(SYNCHRONIZE, FALSE, IME_STOP);
    return TRUE;
}

static BOOL valid_config(void)
{
    return config && config->magic == IME_CONFIG_MAGIC &&
        config->size == sizeof(*config) && config->owner_pid &&
        config->source_pid && config->token && config->controller &&
        config->app_dir[IME_PATH_MAX - 1] == 0 &&
        config->app_version[63] == 0 &&
        config->debug_log[IME_PATH_MAX - 1] == 0 && nt_call && stop_event;
}

static BOOL live(void)
{
    return state.enabled && valid_config() &&
        state.token == config->token && state.owner &&
        WaitForSingleObject(state.owner, 0) == WAIT_TIMEOUT &&
        WaitForSingleObject(stop_event, 0) == WAIT_TIMEOUT;
}

static BOOL local_focus(HWND focus, HWND root)
{
    DWORD pid = 0;
    DWORD thread = GetWindowThreadProcessId(focus, &pid);
    DWORD root_pid = 0;
    GetWindowThreadProcessId(root, &root_pid);
    return live() && thread == GetCurrentThreadId() &&
        pid == GetCurrentProcessId() &&
        wecom_ime_destination_valid(config->source_pid, pid, root_pid,
            GetFocus() == focus, ime_document_window(config, focus, root));
}

static void cleanup(void)
{
    state.enabled = FALSE;
    if (state.marked_focus &&
        GetPropW(state.marked_focus, IME_PROPERTY) == state.receiver)
        RemovePropW(state.marked_focus, IME_PROPERTY);
    if (state.marked_root &&
        GetPropW(state.marked_root, IME_SOURCE_PROPERTY) == state.receiver)
        RemovePropW(state.marked_root, IME_SOURCE_PROPERTY);
    if (state.composing_focus && GetFocus() == state.composing_focus) {
        HIMC context = ImmGetContext(state.composing_focus);
        if (context) {
            ImmNotifyIME(context, NI_COMPOSITIONSTR, CPS_CANCEL, 0);
            ImmReleaseContext(state.composing_focus, context);
        }
    }
    if (state.receiver) {
        KillTimer(state.receiver, 1);
        DestroyWindow(state.receiver);
    }
    if (state.previous_layout &&
        GetKeyboardLayout(0) == state.activated_layout)
        ActivateKeyboardLayout(state.previous_layout, 0);
    if (state.owner) CloseHandle(state.owner);
    state.owner = NULL;
    state.receiver = state.marked_focus = state.composing_focus = NULL;
    state.marked_root = NULL;
    state.candidate_applied = FALSE;
    memset(&state.last_candidate, 0, sizeof(state.last_candidate));
    state.previous_layout = state.activated_layout = NULL;
    if (state.controller)
        PostMessageW(state.controller, ack_message, GetCurrentThreadId(),
                     state.token);
    debug_log("cleanup token=%lu", state.token);
}

static BOOL map_points(HWND from, HWND to, RECT *rect)
{
    SetLastError(ERROR_SUCCESS);
    return MapWindowPoints(from, to, (POINT *)rect, 2) ||
        GetLastError() == ERROR_SUCCESS;
}

static LRESULT import_position(HWND sender, const COPYDATASTRUCT *copy)
{
    if (!live() || !nt_two_param || !copy ||
        copy->dwData != WECOM_IME_CANDIDATE_MAGIC ||
        !wecom_ime_candidate_packet_valid(copy->lpData, copy->cbData) ||
        GetCurrentProcessId() != config->source_pid) return FALSE;
    const struct wecom_ime_candidate_packet *p = copy->lpData;
    HWND focus = (HWND)(ULONG_PTR)p->focus;
    HWND root = (HWND)(ULONG_PTR)p->root;
    DWORD root_pid = 0, focus_pid = 0, sender_pid = 0;
    DWORD root_tid = GetWindowThreadProcessId(root, &root_pid);
    DWORD focus_tid = GetWindowThreadProcessId(focus, &focus_pid);
    DWORD sender_tid = GetWindowThreadProcessId(sender, &sender_pid);
    HWND current_focus = GetFocus();
    struct wecom_ime_candidate_route route = {
        .token = state.token,
        .source_pid = GetCurrentProcessId(),
        .source_tid = GetCurrentThreadId(),
        .focus = (DWORD)(ULONG_PTR)current_focus,
        .root = (DWORD)(ULONG_PTR)GetAncestor(current_focus, GA_ROOT),
        .root_pid = root_pid, .root_tid = root_tid,
        .focus_pid = focus_pid, .focus_tid = focus_tid,
        .sender_pid = sender_pid, .sender_tid = sender_tid,
        .focus_matches = current_focus == focus,
        .chain_matches = ime_document_window(config, focus, root),
        .receiver_matches = state.marked_root == root &&
            GetPropW(root, IME_SOURCE_PROPERTY) == state.receiver &&
            GetPropW(focus, IME_PROPERTY) == sender &&
            ime_is_class(sender, IME_CLASS),
    };
    if (!wecom_ime_candidate_route_valid(p, &route)) return FALSE;

    /* Chromium has already converted DIPs to screen pixels. The Wine
     * syscall maps root-client coordinates with per-monitor DPI internally;
     * use the same context for the inverse mapping, then restore it on every
     * path. Do not modify the source IMC's saved composition/candidate forms.
     */
    DPI_AWARENESS_CONTEXT previous = SetThreadDpiAwarenessContext(
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE);
    if (!previous) return FALSE;
    BOOL accepted = FALSE;
    RECT bounds;
    RECT rect = {p->x, p->y + (LONG)p->line_height,
                 p->x + 1, p->y + (LONG)p->line_height + 1};
    if (GetClientRect(focus, &bounds) && map_points(focus, NULL, &bounds) &&
        wecom_ime_candidate_in_bounds(p, bounds.left, bounds.top,
                                      bounds.right, bounds.bottom) &&
        map_points(NULL, root, &rect) && live() && GetFocus() == focus &&
        GetAncestor(focus, GA_ROOT) == root &&
        GetPropW(root, IME_SOURCE_PROPERTY) == state.receiver &&
        GetPropW(focus, IME_PROPERTY) == sender)
        accepted = !!nt_two_param((ULONG_PTR)root, (ULONG_PTR)&rect,
                                 NTUSER_SET_IME_COMPOSITION_RECT);
    SetThreadDpiAwarenessContext(previous);
    if (accepted != state.candidate_applied ||
        (accepted && memcmp(p, &state.last_candidate, sizeof(*p)))) {
        debug_log("candidate focus=%p root=%p screen=%ld,%ld height=%lu "
                  "accepted=%d", focus, root, (long)p->x, (long)p->y,
                  (unsigned long)p->line_height, accepted);
        if (accepted) state.last_candidate = *p;
    }
    state.candidate_applied = accepted;
    return accepted;
}

static void publish_position(void)
{
    if (state.position_busy || !live() || !state.receiver ||
        GetCurrentProcessId() == config->source_pid) return;
    HWND focus = GetFocus(), root = GetAncestor(focus, GA_ROOT);
    if (state.marked_focus != focus || !local_focus(focus, root) ||
        GetPropW(focus, IME_PROPERTY) != state.receiver) return;
    HWND source = (HWND)GetPropW(root, IME_SOURCE_PROPERTY);
    DWORD source_pid = 0, root_pid = 0;
    DWORD source_tid = GetWindowThreadProcessId(source, &source_pid);
    DWORD root_tid = GetWindowThreadProcessId(root, &root_pid);
    if (!source || source_pid != config->source_pid || root_pid != source_pid ||
        source_tid != root_tid || !ime_is_class(source, IME_CLASS)) return;

    state.position_busy = TRUE;
    /* This pointer stays inside the CEF process and owning UI thread.
     * Wine does not marshal IMECHARPOSITION in cross-process WM_IME_REQUEST.
     * Chromium returns no geometry for non-text and password controls.
     */
    IMECHARPOSITION position = {.dwSize = sizeof(position), .dwCharPos = 0};
    LRESULT available = SendMessageW(focus, WM_IME_REQUEST,
        IMR_QUERYCHARPOSITION, (LPARAM)&position);
    struct wecom_ime_candidate_packet packet = {
        .magic = WECOM_IME_CANDIDATE_MAGIC, .size = sizeof(packet),
        .token = state.token,
        .source_pid = source_pid, .source_tid = source_tid,
        .target_pid = GetCurrentProcessId(),
        .target_tid = GetCurrentThreadId(),
        .focus = (DWORD)(ULONG_PTR)focus, .root = (DWORD)(ULONG_PTR)root,
        .x = position.pt.x, .y = position.pt.y,
        .line_height = position.cLineHeight,
    };
    if (available &&
        wecom_ime_candidate_packet_valid(&packet, sizeof(packet)) &&
        local_focus(focus, root) &&
        GetPropW(focus, IME_PROPERTY) == state.receiver &&
        GetPropW(root, IME_SOURCE_PROPERTY) == source) {
        COPYDATASTRUCT copy = {WECOM_IME_CANDIDATE_MAGIC,
                               sizeof(packet), &packet};
        DWORD_PTR accepted = 0;
        SendMessageTimeoutW(source, WM_COPYDATA, (WPARAM)state.receiver,
            (LPARAM)&copy, SMTO_ABORTIFHUNG, 250, &accepted);
    }
    state.position_busy = FALSE;
}

static LRESULT import_packet(HWND sender, const COPYDATASTRUCT *copy)
{
    if (!live() || !copy || copy->dwData != WECOM_IME_MAGIC ||
        !wecom_ime_packet_valid(copy->lpData, copy->cbData)) return FALSE;
    const struct wecom_ime_packet *data = copy->lpData;
    HWND focus = (HWND)(ULONG_PTR)data->focus;
    HWND root = (HWND)(ULONG_PTR)data->root;
    if (sender != root || data->source_pid != config->source_pid ||
        !local_focus(focus, root)) return FALSE;
    DWORD sender_pid = 0;
    DWORD sender_tid = GetWindowThreadProcessId(sender, &sender_pid);
    if (sender_pid != data->source_pid || sender_tid != data->source_tid)
        return FALSE;
    HIMC context = ImmGetContext(focus);
    if (!context) return FALSE;
    HKL layout = GetKeyboardLayout(0);
    HKL next = (HKL)(ULONG_PTR)0xe0010409;
    if (layout != next) {
        if (!state.previous_layout) state.previous_layout = layout;
        ActivateKeyboardLayout(next, 0);
        state.activated_layout = GetKeyboardLayout(0);
    }
    const WCHAR *comp = (const WCHAR *)data->strings;
    const WCHAR *result = comp + data->comp_len + 1;
    /* ImmIsIME also returns true for Wine's plain 04090409 layout, while
     * its built-in ImeToAsciiEx requires an IME HKL. Use the tested HKL.
     */
    BOOL ok = GetKeyboardLayout(0) == next;
    /* The Wine WoW64 thunk cannot marshal WINE_IME_POST_UPDATE. Public
     * IMM keeps result generation inside this process. It currently does
     * not preserve XIM clause attributes or the original preedit cursor.
     */
    if (ok && data->result_len) {
        ok = ImmSetCompositionStringW(context, SCS_SETSTR, (void *)result,
                                      data->result_len * 2, NULL, 0);
        if (ok)
            ok = ImmNotifyIME(context, NI_COMPOSITIONSTR, CPS_COMPLETE, 0);
    }
    if (ok && data->comp_present) {
        ok = ImmSetCompositionStringW(context, SCS_SETSTR, (void *)comp,
                                      data->comp_len * 2, NULL, 0);
        state.composing_focus = data->comp_len ? focus : NULL;
    } else if (ok && !data->result_len) {
        ok = ImmNotifyIME(context, NI_COMPOSITIONSTR, CPS_CANCEL, 0);
        state.composing_focus = NULL;
    } else if (data->result_len) state.composing_focus = NULL;
    debug_log("import comp=%lu result=%lu cursor=%lu accepted=%d",
              data->comp_len, data->result_len, data->cursor, ok);
    ImmReleaseContext(focus, context);
    if (ok && state.receiver)
        PostMessageW(state.receiver, position_message, state.token, 0);
    return ok;
}

static LRESULT CALLBACK ipc_proc(HWND window, UINT message,
                                 WPARAM parameter, LPARAM data)
{
    if (message == shutdown_message && parameter == state.token) {
        cleanup();
        return TRUE;
    }
    if (message == WM_TIMER && parameter == 1) {
        if (!live()) cleanup();
        else publish_position();
        return 0;
    }
    if (message == position_message && parameter == state.token) {
        publish_position();
        return 0;
    }
    if (message == WM_COPYDATA) {
        const COPYDATASTRUCT *copy = (const COPYDATASTRUCT *)data;
        if (copy && copy->dwData == WECOM_IME_CANDIDATE_MAGIC)
            return import_position((HWND)parameter, copy);
        return import_packet((HWND)parameter, (const COPYDATASTRUCT *)data);
    }
    if (message == ready_message) {
        HWND focus = (HWND)parameter;
        if (!local_focus(focus, (HWND)data)) return FALSE;
        HIMC context = ImmGetContext(focus);
        if (!context) return FALSE;
        ImmReleaseContext(focus, context);
        return TRUE;
    }
    return DefWindowProcW(window, message, parameter, data);
}

static void prime(DWORD token)
{
    if (!valid_config() || config->token != token ||
        WaitForSingleObject(stop_event, 0) != WAIT_TIMEOUT) return;
    if (!state.enabled || state.token != token) {
        if (state.enabled || state.receiver) cleanup();
        state.token = token;
        state.controller = (HWND)(ULONG_PTR)config->controller;
        state.owner = OpenProcess(SYNCHRONIZE, FALSE, config->owner_pid);
        state.enabled = !!state.owner;
    }
    HWND focus = GetFocus(), root = GetAncestor(focus, GA_ROOT);
    DWORD root_pid = 0;
    DWORD root_tid = GetWindowThreadProcessId(root, &root_pid);
    BOOL source = live() && GetCurrentProcessId() == config->source_pid &&
        root_pid == config->source_pid && root_tid == GetCurrentThreadId() &&
        ime_source_window(config, root);
    if (!source && !local_focus(focus, root)) return;
    if (!state.receiver) {
        WNDCLASSW cls = {0};
        cls.lpfnWndProc = ipc_proc;
        cls.hInstance = instance;
        cls.lpszClassName = IME_CLASS;
        if (!RegisterClassW(&cls) &&
            GetLastError() != ERROR_CLASS_ALREADY_EXISTS) return;
        /* A blocked destination may outlive hook removal. Its window
         * procedure must remain mapped until the process exits.
         */
        if (!InterlockedCompareExchange(&pinned, 0, 0)) {
            HMODULE module;
            DWORD flags = GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                          GET_MODULE_HANDLE_EX_FLAG_PIN;
            if (!GetModuleHandleExW(flags, (LPCWSTR)(ULONG_PTR)ipc_proc,
                                   &module)) return;
            InterlockedExchange(&pinned, 1);
        }
        state.receiver = CreateWindowExW(WS_EX_NOACTIVATE, IME_CLASS,
            L"", 0, 0, 0, 0, 0, HWND_MESSAGE, NULL, instance, NULL);
        if (!state.receiver) return;
        if (!SetTimer(state.receiver, 1, 100, NULL)) {
            cleanup();
            return;
        }
        PostMessageW(state.controller, prime_message, GetCurrentThreadId(),
                     (LPARAM)state.receiver);
    }
    if (source) {
        if (state.marked_root != root) {
            if (state.marked_root &&
                GetPropW(state.marked_root, IME_SOURCE_PROPERTY) ==
                state.receiver)
                RemovePropW(state.marked_root, IME_SOURCE_PROPERTY);
            state.marked_root = NULL;
            if (SetPropW(root, IME_SOURCE_PROPERTY, state.receiver))
                state.marked_root = root;
        }
        return;
    }
    if (state.marked_focus != focus) {
        if (state.marked_focus &&
            GetPropW(state.marked_focus, IME_PROPERTY) == state.receiver)
            RemovePropW(state.marked_focus, IME_PROPERTY);
        state.marked_focus = NULL;
        if (SetPropW(focus, IME_PROPERTY, state.receiver))
            state.marked_focus = focus;
    }
}

static void transfer(const CWPSTRUCT *message)
{
    if (state.busy || !live() || message->message != WM_IME_NOTIFY ||
        message->wParam != IMN_WINE_SET_COMP_STRING ||
        !ime_is_class(message->hwnd, L"IME") ||
        GetCurrentProcessId() != config->source_pid) return;
    HWND focus = GetFocus(), root = GetAncestor(focus, GA_ROOT);
    if (!ime_document_window(config, focus, root)) return;
    DWORD target_pid = 0, ipc_pid = 0;
    DWORD target_tid = GetWindowThreadProcessId(focus, &target_pid);
    HWND ipc = (HWND)GetPropW(focus, IME_PROPERTY);
    DWORD ipc_tid = GetWindowThreadProcessId(ipc, &ipc_pid);
    if (target_pid == GetCurrentProcessId() || !ipc ||
        ipc_pid != target_pid || ipc_tid != target_tid ||
        !ime_is_class(ipc, IME_CLASS)) return;
    state.busy = TRUE;
    DWORD_PTR ready = 0;
    if (!SendMessageTimeoutW(ipc, ready_message, (WPARAM)focus, (LPARAM)root,
        SMTO_ABORTIFHUNG, 250, &ready) || !ready) goto done;
    WINE_COMPOSITIONSTRING sizing = {0};
    BOOL consumed = FALSE;
    /* Only read an existing update (state == NULL). Wine 11.19 changed
     * posting and key processing, not this queue-read ABI.
     */
    struct ime_driver_call_params params = {NULL, NULL, &sizing, &consumed};
    LONG status = nt_call(message->hwnd, WINE_IME_TO_ASCII_EX, VK_PROCESSKEY,
        message->lParam, &params, NTUSER_IME_DRIVER_CALL, FALSE);
    if (status != STATUS_BUFFER_TOO_SMALL_VALUE ||
        sizing.dwSize < sizeof(sizing) ||
        sizing.dwSize > WECOM_IME_MAX_PACKET / 2) goto done;
    DWORD allocation = sizing.dwSize;
    WINE_COMPOSITIONSTRING *composition = calloc(1, allocation);
    struct wecom_ime_packet *packet = calloc(1, WECOM_IME_MAX_PACKET);
    if (!composition || !packet) {
        free(composition);
        free(packet);
        goto done;
    }
    /* Recheck after allocation and the first syscall; it has not consumed
     * the update yet. A later race rejects delivery, never redirects text.
     */
    if (!live() || GetFocus() != focus ||
        GetPropW(focus, IME_PROPERTY) != ipc ||
        !SendMessageTimeoutW(ipc, ready_message, (WPARAM)focus, (LPARAM)root,
            SMTO_ABORTIFHUNG, 250, &ready) || !ready) goto release;
    composition->dwSize = allocation;
    params.compstr = composition;
    status = nt_call(message->hwnd, WINE_IME_TO_ASCII_EX, VK_PROCESSKEY,
        message->lParam, &params, NTUSER_IME_DRIVER_CALL, FALSE);
    if (status || composition->dwSize > allocation ||
        !wecom_ime_field_valid(composition->dwCompStrOffset,
            composition->dwCompStrLen, composition->dwSize) ||
        !wecom_ime_field_valid(composition->dwResultStrOffset,
            composition->dwResultStrLen, composition->dwSize)) goto release;
    DWORD bytes = offsetof(struct wecom_ime_packet, strings) +
        (composition->dwCompStrLen + composition->dwResultStrLen + 2) * 2;
    if (bytes > WECOM_IME_MAX_PACKET) goto release;
    packet->magic = WECOM_IME_MAGIC;
    packet->size = bytes;
    packet->source_pid = GetCurrentProcessId();
    packet->source_tid = GetCurrentThreadId();
    packet->focus = (DWORD)(ULONG_PTR)focus;
    packet->root = (DWORD)(ULONG_PTR)root;
    packet->comp_present = !!composition->dwCompStrOffset;
    packet->result_present = !!composition->dwResultStrOffset;
    packet->comp_len = composition->dwCompStrLen;
    packet->result_len = composition->dwResultStrLen;
    packet->cursor = composition->dwCursorPos;
    if (packet->comp_len)
        memcpy(packet->strings, (BYTE *)composition +
               composition->dwCompStrOffset, packet->comp_len * 2);
    if (packet->result_len)
        memcpy(packet->strings + packet->comp_len + 1, (BYTE *)composition +
               composition->dwResultStrOffset, packet->result_len * 2);
    if (!wecom_ime_packet_valid(packet, bytes)) goto release;
    COPYDATASTRUCT copy = {WECOM_IME_MAGIC, bytes, packet};
    DWORD_PTR accepted = 0;
    LRESULT sent = SendMessageTimeoutW(ipc, WM_COPYDATA, (WPARAM)root,
        (LPARAM)&copy, SMTO_ABORTIFHUNG, 1000, &accepted);
    debug_log("transfer comp=%lu result=%lu sent=%ld accepted=%lu",
        packet->comp_len, packet->result_len, (long)sent,
        (unsigned long)accepted);
release:
    SecureZeroMemory(composition, allocation);
    SecureZeroMemory(packet, WECOM_IME_MAX_PACKET);
    free(composition);
    free(packet);
done:
    state.busy = FALSE;
}

__declspec(dllexport) LRESULT CALLBACK
DocsImeCallWndProc(int code, WPARAM parameter, LPARAM data)
{
    if (code >= 0) {
        InitOnceExecuteOnce(&initialized, initialize, NULL, NULL);
        transfer((const CWPSTRUCT *)data);
    }
    return CallNextHookEx(NULL, code, parameter, data);
}

__declspec(dllexport) LRESULT CALLBACK
DocsImeGetMessage(int code, WPARAM parameter, LPARAM data)
{
    if (code >= 0 && parameter == PM_REMOVE) {
        InitOnceExecuteOnce(&initialized, initialize, NULL, NULL);
        const MSG *message = (const MSG *)data;
        if (message->message == prime_message)
            prime((DWORD)message->wParam);
        else if (message->message == shutdown_message &&
                 message->wParam == state.token) cleanup();
    }
    return CallNextHookEx(NULL, code, parameter, data);
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) instance = module;
    return TRUE;
}
