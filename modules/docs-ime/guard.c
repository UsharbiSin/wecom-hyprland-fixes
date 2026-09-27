/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Prefix-local lifecycle and verified window discovery for docs IME.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#include "bridge-abi.h"

#define MAX_THREADS 128
struct watched_thread {
    DWORD tid, pid;
    HANDLE thread;
    HHOOK calls, messages;
    HWND receiver;
    BOOL acknowledged;
};
static struct watched_thread watched[MAX_THREADS];
static struct bridge_config *config;
static HMODULE library;
static HOOKPROC call_proc, get_proc;
static HANDLE stop_event, done_event, clean_event, source_process;
static UINT prime_message, shutdown_message, ack_message;
static DWORD source_pid;
static BOOL stopping;

static void pump(void)
{
    MSG message;
    while (PeekMessageW(&message, NULL, 0, 0, PM_REMOVE)) {
        if (message.message == WM_QUIT && stop_event) SetEvent(stop_event);
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
}

static struct watched_thread *find_thread(DWORD tid)
{
    for (unsigned i = 0; i < MAX_THREADS; ++i)
        if (watched[i].tid == tid) return &watched[i];
    return NULL;
}

static LRESULT CALLBACK control_proc(HWND window, UINT message,
                                     WPARAM parameter, LPARAM data)
{
    struct watched_thread *thread = find_thread((DWORD)parameter);
    if (message == prime_message && thread) {
        DWORD pid;
        DWORD tid = GetWindowThreadProcessId((HWND)data, &pid);
        if (tid == thread->tid && pid == thread->pid &&
            ime_is_class((HWND)data, IME_CLASS)) thread->receiver = (HWND)data;
        return 0;
    }
    if (message == ack_message && thread && config &&
        (DWORD)data == config->token) {
        thread->acknowledged = TRUE;
        return 0;
    }
    return DefWindowProcW(window, message, parameter, data);
}

static BOOL WINAPI console_event(DWORD event)
{
    if (event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT ||
        event == CTRL_CLOSE_EVENT || event == CTRL_LOGOFF_EVENT ||
        event == CTRL_SHUTDOWN_EVENT) {
        if (stop_event) SetEvent(stop_event);
        return TRUE;
    }
    return FALSE;
}

static void release_thread(struct watched_thread *thread)
{
    if (thread->calls) UnhookWindowsHookEx(thread->calls);
    if (thread->messages) UnhookWindowsHookEx(thread->messages);
    if (thread->thread) CloseHandle(thread->thread);
    memset(thread, 0, sizeof(*thread));
}

static void watch_thread(DWORD tid, DWORD pid)
{
    if (!tid || stopping || find_thread(tid)) return;
    struct watched_thread *entry = NULL;
    for (unsigned i = 0; i < MAX_THREADS; ++i)
        if (!watched[i].tid) { entry = &watched[i]; break; }
    if (!entry) return;
    HANDLE thread = OpenThread(SYNCHRONIZE | THREAD_QUERY_LIMITED_INFORMATION,
                               FALSE, tid);
    if (!thread) return;
    if (GetProcessIdOfThread(thread) != pid) {
        CloseHandle(thread);
        return;
    }
    HHOOK calls = SetWindowsHookExW(WH_CALLWNDPROC, call_proc, library, tid);
    HHOOK messages = SetWindowsHookExW(WH_GETMESSAGE, get_proc, library, tid);
    if (!calls || !messages) {
        if (calls) UnhookWindowsHookEx(calls);
        if (messages) UnhookWindowsHookEx(messages);
        CloseHandle(thread);
        fprintf(stderr, "docs-ime: hook failed for thread %lu\n", tid);
        return;
    }
    *entry = (struct watched_thread){tid, pid, thread, calls, messages,
                                     NULL, FALSE};
    if (config->debug)
        fprintf(stderr, "docs-ime: watching pid=%lu tid=%lu\n", pid, tid);
}

static BOOL CALLBACK discover_child(HWND window, LPARAM parameter)
{
    HWND root = (HWND)parameter;
    if (ime_document_window(config, window, root)) {
        DWORD pid;
        DWORD tid = GetWindowThreadProcessId(window, &pid);
        watch_thread(tid, pid);
    }
    return TRUE;
}

static BOOL CALLBACK discover_root(HWND window, LPARAM parameter)
{
    (void)parameter;
    if (!ime_source_window(config, window)) return TRUE;
    DWORD pid;
    DWORD tid = GetWindowThreadProcessId(window, &pid);
    if (!source_pid) {
        source_process = OpenProcess(SYNCHRONIZE, FALSE, pid);
        if (!source_process) return TRUE;
        source_pid = pid;
        config->source_pid = pid;
        MemoryBarrier();
        config->magic = IME_CONFIG_MAGIC;
    }
    if (pid != source_pid) return TRUE;
    watch_thread(tid, pid);
    EnumChildWindows(window, discover_child, (LPARAM)window);
    return TRUE;
}

static void discover(void)
{
    for (unsigned i = 0; i < MAX_THREADS; ++i)
        if (watched[i].thread &&
            WaitForSingleObject(watched[i].thread, 0) != WAIT_TIMEOUT)
            release_thread(&watched[i]);
    EnumWindows(discover_root, 0);
    for (unsigned i = 0; i < MAX_THREADS; ++i)
        if (watched[i].tid)
            PostThreadMessageW(watched[i].tid, prime_message,
                               config->token, 0);
}

static BOOL stop_hooks(void)
{
    stopping = TRUE;
    SetEvent(stop_event);
    for (unsigned i = 0; i < MAX_THREADS; ++i) {
        struct watched_thread *thread = &watched[i];
        if (!thread->tid) continue;
        thread->acknowledged = FALSE;
        PostThreadMessageW(thread->tid, shutdown_message, config->token, 0);
        if (thread->receiver)
            PostMessageW(thread->receiver, shutdown_message, config->token, 0);
    }
    ULONGLONG start = GetTickCount64();
    BOOL completed;
    do {
        completed = TRUE;
        pump();
        for (unsigned i = 0; i < MAX_THREADS; ++i) {
            struct watched_thread *thread = &watched[i];
            if (thread->tid && !thread->acknowledged &&
                WaitForSingleObject(thread->thread, 0) == WAIT_TIMEOUT)
                completed = FALSE;
        }
        if (!completed) Sleep(20);
    } while (!completed && GetTickCount64() - start < 5000);
    for (unsigned i = 0; i < MAX_THREADS; ++i) release_thread(&watched[i]);
    if (!completed)
        fputs("docs-ime: cleanup acknowledgement timed out; bridge disabled, "
              "resident DLL retained until process exit\n", stderr);
    return completed;
}

static int request_stop(void)
{
    HANDLE mutex = OpenMutexW(SYNCHRONIZE | MUTEX_MODIFY_STATE,
                               FALSE, IME_MUTEX);
    if (!mutex) return GetLastError() == ERROR_FILE_NOT_FOUND ? 0 : 1;
    DWORD lock = WaitForSingleObject(mutex, 0);
    if (lock == WAIT_OBJECT_0 || lock == WAIT_ABANDONED) {
        ReleaseMutex(mutex);
        CloseHandle(mutex);
        return 0;
    }
    if (lock != WAIT_TIMEOUT) {
        CloseHandle(mutex);
        return 1;
    }
    HANDLE stop = OpenEventW(EVENT_MODIFY_STATE, FALSE, IME_STOP);
    if (!stop) {
        DWORD error = GetLastError();
        CloseHandle(mutex);
        if (error == ERROR_FILE_NOT_FOUND) return 0;
        fputs("docs-ime: cannot open stop event\n", stderr);
        return 1;
    }
    HANDLE done = OpenEventW(SYNCHRONIZE, FALSE, IME_DONE);
    HANDLE clean = OpenEventW(SYNCHRONIZE, FALSE, IME_CLEAN);
    BOOL sent = SetEvent(stop);
    DWORD result = done && sent ? WaitForSingleObject(done, 8000) : WAIT_FAILED;
    BOOL acknowledged = clean &&
        WaitForSingleObject(clean, 0) == WAIT_OBJECT_0;
    CloseHandle(stop);
    if (done) CloseHandle(done);
    if (clean) CloseHandle(clean);
    BOOL released = FALSE;
    if (result == WAIT_OBJECT_0) {
        lock = WaitForSingleObject(mutex, 2000);
        if (lock == WAIT_OBJECT_0 || lock == WAIT_ABANDONED) {
            ReleaseMutex(mutex);
            released = TRUE;
        }
    }
    CloseHandle(mutex);
    if (result == WAIT_OBJECT_0 && acknowledged && released) return 0;
    fputs("docs-ime: graceful stop did not complete\n", stderr);
    return 1;
}

static BOOL configure(struct bridge_config *cfg, const WCHAR *version)
{
    memset(cfg, 0, sizeof(*cfg));
    if (!version || !*version || wcslen(version) >= 64) return FALSE;
    for (const WCHAR *p = version; *p; ++p)
        if ((*p < L'0' || *p > L'9') && *p != L'.') return FALSE;
    DWORD length = GetEnvironmentVariableW(L"WECOM_DOCS_IME_APP_DIR",
                                           cfg->app_dir, IME_PATH_MAX);
    if (!length || length >= IME_PATH_MAX) return FALSE;
    while (length && (cfg->app_dir[length - 1] == L'\\' ||
                      cfg->app_dir[length - 1] == L'/'))
        cfg->app_dir[--length] = 0;
    if (!wecom_ime_absolute_path(cfg->app_dir)) return FALSE;
    wcscpy(cfg->app_version, version);
    WCHAR debug[8];
    DWORD count = GetEnvironmentVariableW(L"WECOM_DOCS_IME_DEBUG", debug, 8);
    cfg->debug = count == 1 && debug[0] == L'1';
    if (cfg->debug) {
        count = GetEnvironmentVariableW(L"WECOM_DOCS_IME_DEBUG_LOG",
                                        cfg->debug_log, IME_PATH_MAX);
        if (count >= IME_PATH_MAX || (count &&
            !wecom_ime_absolute_path(cfg->debug_log))) return FALSE;
    }
    cfg->size = sizeof(*cfg);
    cfg->owner_pid = GetCurrentProcessId();
    cfg->token = GetTickCount() ^ cfg->owner_pid;
    if (!cfg->token) cfg->token = 1;
    return TRUE;
}

int wmain(int argc, WCHAR **argv)
{
    if (argc == 2 && !wcscmp(argv[1], L"--stop")) return request_stop();
    if (argc != 3 || wcscmp(argv[1], L"--app-version")) {
        fputs("Usage: docs-ime-guard.exe --app-version VERSION | --stop\n",
              stderr);
        return 2;
    }
    struct bridge_config settings;
    if (!configure(&settings, argv[2])) {
        fputs("docs-ime: invalid version or WECOM_DOCS_IME_APP_DIR\n", stderr);
        return 2;
    }
    HANDLE mutex = CreateMutexW(NULL, FALSE, IME_MUTEX);
    if (!mutex) return 1;
    DWORD lock = WaitForSingleObject(mutex, 0);
    if (lock != WAIT_OBJECT_0 && lock != WAIT_ABANDONED) {
        fputs("docs-ime: a guard already owns this Wine prefix\n", stderr);
        CloseHandle(mutex);
        return 0;
    }
    int result = 1;
    HANDLE map = NULL;
    HWND controller = NULL;
    stop_event = CreateEventW(NULL, TRUE, FALSE, IME_STOP);
    done_event = CreateEventW(NULL, TRUE, FALSE, IME_DONE);
    clean_event = CreateEventW(NULL, TRUE, FALSE, IME_CLEAN);
    if (!stop_event || !done_event || !clean_event) goto done;
    ResetEvent(stop_event);
    ResetEvent(done_event);
    ResetEvent(clean_event);
    prime_message = RegisterWindowMessageW(IME_PRIME);
    shutdown_message = RegisterWindowMessageW(IME_SHUTDOWN);
    ack_message = RegisterWindowMessageW(IME_ACK);
    WCHAR path[IME_PATH_MAX];
    DWORD length = GetModuleFileNameW(NULL, path, IME_PATH_MAX);
    if (!length || length >= IME_PATH_MAX) goto done;
    WCHAR *slash = wcsrchr(path, L'\\');
    if (!slash || (size_t)(slash - path) + 22 >= IME_PATH_MAX) goto done;
    wcscpy(slash + 1, L"docs-ime-bridge.dll");
    library = LoadLibraryW(path);
    if (!library) goto done;
    FARPROC call_address = GetProcAddress(library, "DocsImeCallWndProc");
    FARPROC get_address = GetProcAddress(library, "DocsImeGetMessage");
    _Static_assert(sizeof(call_proc) == sizeof(call_address), "pointer ABI");
    _Static_assert(sizeof(get_proc) == sizeof(get_address), "pointer ABI");
    memcpy(&call_proc, &call_address, sizeof(call_proc));
    memcpy(&get_proc, &get_address, sizeof(get_proc));
    if (!call_proc || !get_proc) goto done;
    WNDCLASSW cls = {0};
    cls.lpfnWndProc = control_proc;
    cls.hInstance = GetModuleHandleW(NULL);
    cls.lpszClassName = L"WeComDocsImeGuardControl-v1";
    if (!RegisterClassW(&cls)) goto done;
    controller = CreateWindowExW(0, cls.lpszClassName, L"", 0, 0, 0, 0, 0,
                                HWND_MESSAGE, NULL, cls.hInstance, NULL);
    if (!controller) goto done;
    settings.controller = (DWORD)(ULONG_PTR)controller;
    map = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE,
                            0, sizeof(settings), IME_MAPPING);
    if (!map) goto done;
    config = MapViewOfFile(map, FILE_MAP_WRITE, 0, 0, sizeof(settings));
    if (!config) goto done;
    config->magic = 0;
    MemoryBarrier();
    memcpy(config, &settings, sizeof(settings));
    SetConsoleCtrlHandler(console_event, TRUE);
    ULONGLONG started = GetTickCount64(), previous = 0;
    result = 0;
    while (WaitForSingleObject(stop_event, 0) == WAIT_TIMEOUT) {
        pump();
        if (source_process &&
            WaitForSingleObject(source_process, 0) != WAIT_TIMEOUT) break;
        if (!source_pid && GetTickCount64() - started >= 60000) {
            fputs("docs-ime: no matching WXWork window within 60 seconds\n",
                  stderr);
            result = 1;
            break;
        }
        if (GetTickCount64() - previous >= 500) {
            discover();
            previous = GetTickCount64();
        }
        Sleep(20);
    }
    if (stop_hooks()) SetEvent(clean_event);
    else result = 1;
done:
    if (stop_event) SetEvent(stop_event);
    if (config) UnmapViewOfFile(config);
    if (map) CloseHandle(map);
    if (controller) DestroyWindow(controller);
    if (source_process) CloseHandle(source_process);
    if (library) FreeLibrary(library);
    if (done_event) {
        SetEvent(done_event);
        CloseHandle(done_event);
    }
    if (clean_event) CloseHandle(clean_event);
    if (stop_event) CloseHandle(stop_event);
    ReleaseMutex(mutex);
    CloseHandle(mutex);
    return result;
}
