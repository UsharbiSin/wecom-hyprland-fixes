#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <objidl.h>
#include <shellapi.h>
#include <gdiplus.h>

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <string>
#include <vector>

#include "policy.h"
#include "prefix-config.h"

// 仅允许构建时指定的 Wine 前缀。
static const wchar_t kWeComExe[] =
    L"C:\\Program Files (x86)\\WXWork\\WXWork.exe";
static const wchar_t kWindowClass[] = L"WeComPngOutboundScopedV1";
static constexpr SIZE_T kByteLimit = policy::byte_limit;
static constexpr UINT_PTR kRepairTimer = 1, kLifetimeTimer = 2;
static constexpr unsigned kRetryLimit = 8;
static HWND helper_window;
static HANDLE watched_process;
static UINT message_format, png_format;
static DWORD handled_sequence, pending_sequence;
static bool have_handled_sequence, busy, pending_valid;
static unsigned retries;
static ULONGLONG started;

struct Handle {
    HANDLE value = nullptr;
    ~Handle() {
        if (value && value != INVALID_HANDLE_VALUE)
            CloseHandle(value);
    }
    Handle() = default;
    Handle(const Handle &) = delete;
    Handle &operator=(const Handle &) = delete;
};

struct GlobalBlock {
    HGLOBAL value = nullptr;
    ~GlobalBlock() {
        if (value)
            GlobalFree(value);
    }
    GlobalBlock() = default;
    GlobalBlock(const GlobalBlock &) = delete;
    GlobalBlock &operator=(const GlobalBlock &) = delete;
};

struct ClipboardLock {
    bool open;
    explicit ClipboardLock(HWND window)
        : open(OpenClipboard(window) != FALSE) {}
    ~ClipboardLock() {
        if (open)
            CloseClipboard();
    }
    bool close() {
        if (!open)
            return true;
        bool ok = CloseClipboard() != FALSE;
        if (ok)
            open = false;
        return ok;
    }
};

struct Snapshot {
    HWND owner = nullptr;
    DWORD pid = 0, sequence = 0;
    Handle process;
    std::wstring file;
    std::vector<UINT> formats;
};

enum class Result { Done, Retry, Stop };

static void log_result(const char *status, const char *reason) {
    std::printf("%s %s\n", status, reason);
    std::fflush(stdout);
}

static bool arm_timer(HWND window, UINT_PTR timer, UINT interval) {
    if (SetTimer(window, timer, interval, nullptr))
        return true;
    log_result("停止", "无法创建定时器，退出以避免辅助程序无限等待");
    PostQuitMessage(2);
    return false;
}

static void mark_handled(DWORD sequence) {
    handled_sequence = sequence;
    have_handled_sequence = true;
}

static bool process_is_wecom(HANDLE process) {
    if (!process || WaitForSingleObject(process, 0) != WAIT_TIMEOUT)
        return false;
    wchar_t path[32768] = {};
    DWORD length = static_cast<DWORD>(std::size(path));
    return QueryFullProcessImageNameW(process, 0, path, &length) &&
           _wcsicmp(path, kWeComExe) == 0;
}

static bool same_owner(const Snapshot &snapshot) {
    if (GetClipboardOwner() != snapshot.owner ||
        !process_is_wecom(snapshot.process.value))
        return false;
    DWORD pid = 0;
    return GetWindowThreadProcessId(snapshot.owner, &pid) != 0 &&
           pid == snapshot.pid;
}

static bool clipboard_formats(std::vector<UINT> &formats) {
    formats.clear();
    UINT format = 0;
    for (;;) {
        SetLastError(ERROR_SUCCESS);
        format = EnumClipboardFormats(format);
        if (!format) {
            if (GetLastError() != ERROR_SUCCESS)
                return false;
            std::sort(formats.begin(), formats.end());
            return true;
        }
        if (formats.size() >= 256)
            return false;
        formats.push_back(format);
    }
}

static bool required_formats() {
    // 已声明的 PNG 即使为空或延迟渲染，也由原应用管理。
    return policy::eligible_formats(IsClipboardFormatAvailable(message_format),
                                    IsClipboardFormatAvailable(CF_HDROP),
                                    IsClipboardFormatAvailable(png_format));
}

static bool single_local_file(std::wstring &path) {
    HANDLE drop = GetClipboardData(CF_HDROP);
    if (!drop ||
        DragQueryFileW(static_cast<HDROP>(drop), 0xffffffff, nullptr, 0) != 1)
        return false;
    const UINT length = DragQueryFileW(static_cast<HDROP>(drop), 0, nullptr, 0);
    if (length < 3 || length >= 32767)
        return false;
    std::vector<wchar_t> buffer(static_cast<size_t>(length) + 1);
    if (DragQueryFileW(static_cast<HDROP>(drop), 0, buffer.data(),
                       length + 1) != length)
        return false;
    path.assign(buffer.data(), length);
    // 只接受 Wine 盘符绝对路径；不读 UNC 或相对路径。
    return policy::local_drive_path(path);
}

static bool take_snapshot(Snapshot &snapshot) {
    snapshot.sequence = GetClipboardSequenceNumber();
    snapshot.owner = GetClipboardOwner();
    if (!snapshot.owner ||
        !GetWindowThreadProcessId(snapshot.owner, &snapshot.pid))
        return false;
    snapshot.process.value = OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, snapshot.pid);
    if (!process_is_wecom(snapshot.process.value) || !required_formats())
        return false;
    if (!single_local_file(snapshot.file) ||
        !clipboard_formats(snapshot.formats))
        return false;
    return same_owner(snapshot) &&
           GetClipboardSequenceNumber() == snapshot.sequence &&
           required_formats();
}

static bool copy_global(const void *data, SIZE_T size, GlobalBlock &output) {
    if (!data || !size || size > kByteLimit)
        return false;
    output.value = GlobalAlloc(GMEM_MOVEABLE, size);
    if (!output.value)
        return false;
    void *destination = GlobalLock(output.value);
    if (!destination)
        return false;
    std::memcpy(destination, data, size);
    GlobalUnlock(output.value);
    return true;
}

static bool png_from_file(const std::wstring &path, GlobalBlock &output,
                          UINT &width, UINT &height) {
    // 解码期间不持有剪贴板锁。文件禁止共享写入或删除，保持字节稳定。
    Handle file;
    file.value = CreateFileW(
        path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file.value == INVALID_HANDLE_VALUE)
        return false;
    LARGE_INTEGER size = {};
    if (!GetFileSizeEx(file.value, &size) ||
        !policy::bounded_bytes(size.QuadPart))
        return false;
    std::vector<BYTE> bytes(static_cast<size_t>(size.QuadPart));
    SIZE_T total = 0;
    while (total < bytes.size()) {
        DWORD count = 0;
        if (!ReadFile(file.value, bytes.data() + total,
                      static_cast<DWORD>(bytes.size() - total), &count,
                      nullptr) ||
            !count)
            return false;
        total += count;
    }
    GlobalBlock encoded;
    if (!copy_global(bytes.data(), bytes.size(), encoded))
        return false;
    IStream *source = nullptr;
    if (FAILED(CreateStreamOnHGlobal(encoded.value, FALSE, &source)))
        return false;
    bool success = false;
    {
        Gdiplus::Bitmap bitmap(source, FALSE);
        width = bitmap.GetWidth();
        height = bitmap.GetHeight();
        if (bitmap.GetLastStatus() == Gdiplus::Ok &&
            policy::bounded_image(width, height)) {
            IStream *destination = nullptr;
            if (SUCCEEDED(CreateStreamOnHGlobal(nullptr, TRUE, &destination))) {
                const CLSID png = {
                    0x557cf406,
                    0x1a04,
                    0x11d3,
                    {0x9a, 0x73, 0x00, 0x00, 0xf8, 0x1e, 0xf3, 0x2e}};
                STATSTG info = {};
                HGLOBAL storage = nullptr;
                if (bitmap.Save(destination, &png, nullptr) == Gdiplus::Ok &&
                    SUCCEEDED(destination->Stat(&info, STATFLAG_NONAME)) &&
                    info.cbSize.QuadPart >= 8 &&
                    info.cbSize.QuadPart <= kByteLimit &&
                    SUCCEEDED(GetHGlobalFromStream(destination, &storage))) {
                    void *data = GlobalLock(storage);
                    const unsigned char signature[] = {137, 80, 78, 71,
                                                       13,  10, 26, 10};
                    if (data) {
                        success =
                            std::memcmp(data, signature, sizeof(signature)) ==
                                0 &&
                            copy_global(
                                data, static_cast<SIZE_T>(info.cbSize.QuadPart),
                                output);
                        GlobalUnlock(storage);
                    }
                }
                destination->Release();
            }
        }
    }
    source->Release();
    return success;
}

static Result repair() {
    const DWORD initial_sequence = GetClipboardSequenceNumber();
    if (have_handled_sequence && initial_sequence == handled_sequence)
        return Result::Done;
    Snapshot snapshot;
    {
        ClipboardLock clipboard(helper_window);
        if (!clipboard.open)
            return Result::Retry;
        if (!take_snapshot(snapshot)) {
            mark_handled(initial_sequence);
            return Result::Done;
        }
        if (!clipboard.close())
            return Result::Stop;
    }
    GlobalBlock png;
    UINT width = 0, height = 0;
    if (!png_from_file(snapshot.file, png, width, height)) {
        mark_handled(snapshot.sequence);
        log_result("跳过", "来源不是大小符合限制的可解码图片");
        return Result::Done;
    }
    ClipboardLock clipboard(helper_window);
    if (!clipboard.open)
        return Result::Retry;
    std::vector<UINT> before;
    std::wstring file_now;
    if (!same_owner(snapshot) ||
        GetClipboardSequenceNumber() != snapshot.sequence ||
        !required_formats() || !clipboard_formats(before) ||
        before != snapshot.formats || !single_local_file(file_now) ||
        file_now != snapshot.file || !same_owner(snapshot) ||
        GetClipboardSequenceNumber() != snapshot.sequence ||
        !required_formats()) {
        mark_handled(snapshot.sequence);
        log_result("跳过", "追加前剪贴板来源或内容已变化");
        return Result::Done;
    }
    // 唯一剪贴板写入：追加原先不存在的 PNG，保持所有者和全部原格式。
    HANDLE published = SetClipboardData(png_format, png.value);
    if (!published) {
        mark_handled(snapshot.sequence);
        log_result("跳过", "追加 PNG 失败");
        return Result::Done;
    }
    png.value = nullptr; // 此新 PNG 内存块的所有权已移交。
    std::vector<UINT> after;
    const bool retained =
        same_owner(snapshot) && clipboard_formats(after) &&
        policy::contains_original_formats(after, snapshot.formats) &&
        IsClipboardFormatAvailable(message_format) &&
        IsClipboardFormatAvailable(CF_HDROP) &&
        IsClipboardFormatAvailable(png_format) &&
        GetClipboardData(png_format) == published;
    const DWORD after_sequence = GetClipboardSequenceNumber();
    mark_handled(after_sequence);
    if (!clipboard.close()) {
        log_result("停止", "追加后无法关闭剪贴板");
        return Result::Stop;
    }
    if (!retained) {
        // 回写会覆盖实时剪贴板；后置条件失败时直接停止。
        log_result("停止", "所有者或原格式未能保留");
        return Result::Stop;
    }
    if (GetClipboardSequenceNumber() != after_sequence ||
        !same_owner(snapshot)) {
        log_result("已追加", "解锁后剪贴板已变化");
        return Result::Done;
    }
    std::printf("已追加 PNG 尺寸=%ux%u 所有者进程=%lu 所有者保留=1 "
                "原格式保留数量=%zu\n",
                width, height, static_cast<unsigned long>(snapshot.pid),
                snapshot.formats.size());
    std::fflush(stdout);
    return Result::Done;
}

static BOOL CALLBACK find_wecom(HWND window, LPARAM) {
    wchar_t class_name[80] = {};
    if (!GetClassNameW(window, class_name,
                       static_cast<int>(std::size(class_name))) ||
        std::wcscmp(class_name, L"WeWorkWindow"))
        return TRUE;
    DWORD pid = 0;
    if (!GetWindowThreadProcessId(window, &pid))
        return TRUE;
    HANDLE process = OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (process_is_wecom(process)) {
        watched_process = process;
        return FALSE;
    }
    if (process)
        CloseHandle(process);
    return TRUE;
}

static void schedule(HWND window) {
    const DWORD sequence = GetClipboardSequenceNumber();
    if (have_handled_sequence && sequence == handled_sequence)
        return;
    if (!pending_valid || pending_sequence != sequence) {
        pending_valid = true;
        pending_sequence = sequence;
        retries = 0;
    }
    arm_timer(window, kRepairTimer, 120);
}

static LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM parameter,
                                    LPARAM data) {
    if (message == WM_CLIPBOARDUPDATE) {
        schedule(window);
        return 0;
    }
    if (message == WM_TIMER && parameter == kLifetimeTimer) {
        if (!watched_process) {
            EnumWindows(find_wecom, 0);
            if (watched_process)
                schedule(window);
        }
        if ((watched_process &&
             WaitForSingleObject(watched_process, 0) != WAIT_TIMEOUT) ||
            (!watched_process && GetTickCount64() - started > 60000))
            PostQuitMessage(0);
        return 0;
    }
    if (message == WM_TIMER && parameter == kRepairTimer) {
        KillTimer(window, kRepairTimer);
        if (busy)
            return 0;
        if (!watched_process) {
            EnumWindows(find_wecom, 0);
            if (!watched_process)
                return 0;
        }
        busy = true;
        const Result result = repair();
        busy = false;
        if (result == Result::Stop)
            PostQuitMessage(2);
        else if (result == Result::Retry) {
            if (++retries < kRetryLimit)
                arm_timer(window, kRepairTimer, 150);
            else {
                mark_handled(GetClipboardSequenceNumber());
                log_result("跳过", "剪贴板忙，已达到重试上限");
            }
        }
        return 0;
    }
    return DefWindowProcW(window, message, parameter, data);
}

int wmain(int argc, wchar_t **argv) {
    const bool once = argc == 2 && std::wcscmp(argv[1], L"--once") == 0;
    if (argc != 1 && !once) {
        log_result("错误", "用法：wecom-png-outbound.exe [--once]");
        return 2;
    }
    wchar_t prefix[32768] = {};
    const DWORD prefix_length = GetEnvironmentVariableW(
        L"WINEPREFIX", prefix, static_cast<DWORD>(std::size(prefix)));
    if (!prefix_length || prefix_length >= std::size(prefix) ||
        !policy::prefix_matches(prefix, kPrefix)) {
        log_result("错误", "WINEPREFIX 缺失或与构建前缀不一致");
        return 2;
    }
    Handle mutex;
    mutex.value =
        CreateMutexW(nullptr, FALSE, L"Local\\WeComPngOutboundScopedV1");
    if (!mutex.value)
        return 2;
    if (GetLastError() == ERROR_ALREADY_EXISTS)
        return 0;
    Gdiplus::GdiplusStartupInput gdiplus_input;
    ULONG_PTR gdiplus_token = 0;
    if (Gdiplus::GdiplusStartup(&gdiplus_token, &gdiplus_input, nullptr) !=
        Gdiplus::Ok)
        return 2;
    message_format = RegisterClipboardFormatW(L"WeWork Message");
    png_format = RegisterClipboardFormatW(L"PNG");
    int exit_code = 2;
    if (message_format && png_format) {
        if (once) {
            const Result result = repair();
            exit_code = result == Result::Stop    ? 2
                        : result == Result::Retry ? 4
                                                  : 0;
            if (result == Result::Retry)
                log_result("跳过", "剪贴板忙");
        } else {
            WNDCLASSW cls = {};
            cls.lpfnWndProc = window_proc;
            cls.hInstance = GetModuleHandleW(nullptr);
            cls.lpszClassName = kWindowClass;
            if (RegisterClassW(&cls)) {
                helper_window = CreateWindowW(kWindowClass, L"", 0, 0, 0, 0, 0,
                                              HWND_MESSAGE, nullptr,
                                              cls.hInstance, nullptr);
                if (helper_window &&
                    AddClipboardFormatListener(helper_window)) {
                    started = GetTickCount64();
                    EnumWindows(find_wecom, 0);
                    if (arm_timer(helper_window, kLifetimeTimer, 1000))
                        schedule(helper_window);
                    MSG message;
                    BOOL status;
                    while ((status = GetMessageW(&message, nullptr, 0, 0)) >
                           0) {
                        TranslateMessage(&message);
                        DispatchMessageW(&message);
                    }
                    exit_code =
                        status == 0 ? static_cast<int>(message.wParam) : 2;
                    RemoveClipboardFormatListener(helper_window);
                }
                if (helper_window)
                    DestroyWindow(helper_window);
                UnregisterClassW(kWindowClass, cls.hInstance);
            }
        }
    }
    if (watched_process)
        CloseHandle(watched_process);
    Gdiplus::GdiplusShutdown(gdiplus_token);
    return exit_code;
}
