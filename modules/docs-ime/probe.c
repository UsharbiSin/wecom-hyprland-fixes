/*
 * 无账号跨进程输入法复现器；只创建自己的空白 EDIT 控件。
 *
 * 编译：i686-w64-mingw32-gcc -Wall -Wextra -Werror -municode
 *       probe.c -o docs-ime-probe.exe -limm32 -luser32
 *
 * wine docs-ime-probe.exe           同进程父子窗口（对照组）
 * wine docs-ime-probe.exe --cross   跨进程父子窗口（复现组）
 * wine docs-ime-probe.exe --dump    只读取本复现器窗口并输出 UTF-16
 *
 * 手动点击空白框，使用 fcitx5 输入“你好”；--dump 应包含 4f60 597d。
 * 关闭测试窗口会结束其编辑子进程。没有网络、剪贴板或文档文件操作。
 * 正式桥接器应拒绝此进程；不可为验收放宽正式进程白名单。
 */
#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#include <windows.h>
#include <imm.h>
#include <stdio.h>
#include <wchar.h>
static WNDPROC edit_proc;

static void log_state(const wchar_t *label, HWND window, UINT message,
                      WPARAM wparam, LPARAM lparam)
{
    HIMC context = ImmGetContext(window);
    GUITHREADINFO info = {.cbSize = sizeof(info)};
    GetGUIThreadInfo(0, &info);
    wprintf(L"%ls pid=%lu tid=%lu hwnd=%p msg=%04x w=%Ix l=%Ix "
            L"focus=%p active=%p IMC=%p\n", label, GetCurrentProcessId(),
            GetCurrentThreadId(), window, message, wparam, lparam,
            info.hwndFocus, info.hwndActive, context);
    if (context) ImmReleaseContext(window, context);
    fflush(stdout);
}

static LRESULT CALLBACK edit_cb(HWND window, UINT message,
                                 WPARAM wparam, LPARAM lparam)
{
    if (message == WM_DESTROY) PostQuitMessage(0);
    if (message == WM_SETFOCUS || message == WM_KILLFOCUS ||
        (message >= WM_IME_STARTCOMPOSITION && message <= WM_IME_COMPOSITION) ||
        (message >= WM_IME_SETCONTEXT && message <= WM_IME_KEYUP))
        log_state(L"edit", window, message, wparam, lparam);
    return CallWindowProcW(edit_proc, window, message, wparam, lparam);
}

static HWND create_edit(HWND parent)
{
    DWORD style = WS_CHILD | WS_VISIBLE | ES_MULTILINE |
                  ES_AUTOVSCROLL | WS_VSCROLL;
    HWND window = CreateWindowExW(WS_EX_CLIENTEDGE, L"EDIT", L"", style,
        20, 40, 520, 200, parent, NULL, GetModuleHandleW(NULL), NULL);
    edit_proc = (WNDPROC)SetWindowLongPtrW(window, GWLP_WNDPROC,
                                         (LONG_PTR)edit_cb);
    log_state(L"created", window, 0, 0, 0);
    SetFocus(window);
    return window;
}

static LRESULT CALLBACK main_cb(HWND window, UINT message,
                                 WPARAM wparam, LPARAM lparam)
{
    if (message == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    if (message == WM_IME_NOTIFY || message == WM_IME_SETCONTEXT ||
        message == WM_SETFOCUS || message == WM_KILLFOCUS)
        log_state(L"main", window, message, wparam, lparam);
    return DefWindowProcW(window, message, wparam, lparam);
}

static BOOL CALLBACK dump_cb(HWND window, LPARAM unused)
{
    (void)unused;
    wchar_t class_name[64], title[128], value[1024];
    DWORD_PTR result;
    GetClassNameW(window, class_name, 64);
    if (wcscmp(class_name, L"ImeCrossProcessProbe")) return TRUE;
    GetWindowTextW(window, title, 128);
    HWND edit = FindWindowExW(window, NULL, L"EDIT", NULL);
    if (edit && SendMessageTimeoutW(edit, WM_GETTEXT, 1024, (LPARAM)value,
                                   SMTO_ABORTIFHUNG, 1000, &result)) {
        wprintf(L"window=%p title=%ls utf16=", window, title);
        for (unsigned i = 0; i < result; ++i)
            wprintf(L"%04x ", (unsigned)value[i]);
        wprintf(L"\n");
    }
    return TRUE;
}

int wmain(int argc, wchar_t **argv)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc > 1 && !wcscmp(argv[1], L"--help")) {
        puts("Usage: docs-ime-probe.exe [--cross | --dump]");
        puts("Type into the blank test edit; --dump prints its UTF-16 units.");
        return 0;
    }
    if (argc > 1 && !wcscmp(argv[1], L"--dump")) {
        EnumWindows(dump_cb, 0);
        return 0;
    }
    if (argc > 2 && !wcscmp(argv[1], L"--child")) {
        HWND parent = (HWND)(UINT_PTR)wcstoull(argv[2], NULL, 16);
        create_edit(parent);
        MSG message;
        while (IsWindow(parent) && GetMessageW(&message, NULL, 0, 0) > 0) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        return 0;
    }
    WNDCLASSW cls = {0};
    cls.lpfnWndProc = main_cb;
    cls.hInstance = GetModuleHandleW(NULL);
    cls.lpszClassName = L"ImeCrossProcessProbe";
    cls.hCursor = LoadCursorW(NULL, IDC_ARROW);
    cls.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    RegisterClassW(&cls);
    BOOL cross = argc > 1 && !wcscmp(argv[1], L"--cross");
    const WCHAR *title = cross ? L"IME probe - cross-process child" :
                                 L"IME probe - same-process child";
    HWND window = CreateWindowW(cls.lpszClassName, title,
        WS_OVERLAPPEDWINDOW | WS_VISIBLE, 120, 120, 590, 310,
        NULL, NULL, cls.hInstance, NULL);
    if (cross) {
        wchar_t executable[32768], command[32768];
        GetModuleFileNameW(NULL, executable, 32768);
        swprintf(command, 32768, L"\"%ls\" --child %Ix", executable,
                 (UINT_PTR)window);
        STARTUPINFOW startup = {.cb = sizeof(startup)};
        PROCESS_INFORMATION process;
        if (!CreateProcessW(NULL, command, NULL, NULL, TRUE, 0, NULL, NULL,
                            &startup, &process))
            wprintf(L"CreateProcess failed %lu\n", GetLastError());
        else {
            CloseHandle(process.hThread);
            CloseHandle(process.hProcess);
        }
    } else create_edit(window);
    MSG message;
    while (GetMessageW(&message, NULL, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return 0;
}
