/* External Wine 11.22 common-dialog bridge. Never injects executable code.
 * Layout constants are verified against Wine 11.22 filedlgbrowser.h and
 * OPENFILENAME; the supervisor must also verify the installed DLL hashes.
 * Protocol: JSON events on stdout, ASCII commands on stdin. Both must be pipes.
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commdlg.h>
#include <shlobj.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>

#define MAX_TEXT 32768
#define MAX_FILTERS 64
#define MAX_INPUT (MAX_TEXT * 8)
#define TIMEOUT_MS 500
#define FOD_SAVEDLG 1
#define ID_FILENAME_EDIT 1152
#define ID_FILENAME_COMBO 1148
#define ID_FILETYPE 1136

typedef struct
{
    HWND hwnd;
    DWORD pid;
    uintptr_t property;
} Identity;

typedef struct
{
    Identity id;
    HANDLE process;
    BOOL target32, unicode, hidden, com;
    uintptr_t ofn, output_buffer;
    DWORD flags, max_file, filter_index, ofn_size;
    HWND filename, filetype;
    WCHAR title[1024], accept[128], initial[MAX_TEXT], current[MAX_PATH];
    WCHAR filters[MAX_TEXT];
    unsigned filter_count;
    /* phase 0 = advertised; 1 = hidden; 2 = navigation; 3 = committing. */
    unsigned phase;
    ULONGLONG started, phase_started;
    WCHAR *selection, *field;
    unsigned selection_count;
} Dialog;

static Dialog active;
static Identity ignored[128];
static unsigned ignored_count;
static WCHAR test_exe[MAX_TEXT];
static volatile LONG stopping;
typedef struct CommandNode
{
    struct CommandNode* next;
    size_t size;
    char text[];
} CommandNode;
static CRITICAL_SECTION commands_lock;
static CommandNode *commands_head, *commands_tail;
static size_t queued_bytes;

/* Wine represents inherited Unix pipes as file descriptors that need not
 * implement PeekNamedPipe. A dedicated blocking reader also avoids polling
 * partial commands and reliably observes supervisor EOF. */
static DWORD WINAPI read_commands(void* opaque)
{
    HANDLE input = (HANDLE)opaque;
    char* buffer = (char*)malloc(MAX_INPUT + 1);
    size_t used = 0;
    if (!buffer)
    {
        InterlockedExchange(&stopping, 1);
        return 1;
    }
    while (!stopping)
    {
        DWORD count;
        size_t position, start = 0;
        if (used == MAX_INPUT
            || !ReadFile(
                input, buffer + used, (DWORD)(MAX_INPUT - used), &count, NULL)
            || !count)
        {
            if (test_exe[0])
                fprintf(stderr, "test input closed/error=%lu\n",
                    (unsigned long)GetLastError());
            break;
        }
        used += count;
        buffer[used] = 0;
        for (position = 0; position < used; ++position)
            if (buffer[position] == '\n')
            {
                size_t size = position - start;
                CommandNode* node
                    = (CommandNode*)malloc(sizeof(*node) + size + 1);
                if (!node)
                {
                    InterlockedExchange(&stopping, 1);
                    break;
                }
                node->next = NULL;
                node->size = size;
                memcpy(node->text, buffer + start, size);
                node->text[size] = 0;
                EnterCriticalSection(&commands_lock);
                if (queued_bytes + size > 2 * MAX_INPUT)
                {
                    LeaveCriticalSection(&commands_lock);
                    free(node);
                    InterlockedExchange(&stopping, 1);
                    break;
                }
                if (commands_tail)
                    commands_tail->next = node;
                else
                    commands_head = node;
                commands_tail = node;
                queued_bytes += size;
                LeaveCriticalSection(&commands_lock);
                start = position + 1;
            }
        if (start)
        {
            memmove(buffer, buffer + start, used - start);
            used -= start;
        }
    }
    free(buffer);
    InterlockedExchange(&stopping, 1);
    return 0;
}

static CommandNode* take_command(void)
{
    CommandNode* node;
    EnterCriticalSection(&commands_lock);
    node = commands_head;
    if (node)
    {
        commands_head = node->next;
        if (!commands_head)
            commands_tail = NULL;
        queued_bytes -= node->size;
    }
    LeaveCriticalSection(&commands_lock);
    return node;
}

static BOOL remote_read(
    HANDLE process, uintptr_t address, void* out, SIZE_T size)
{
    SIZE_T actual = 0;
    return address
        && ReadProcessMemory(process, (LPCVOID)address, out, size, &actual)
        && actual == size;
}

static uint32_t u32(const BYTE* p, unsigned offset)
{
    uint32_t value;
    memcpy(&value, p + offset, sizeof(value));
    return value;
}

static uintptr_t pointer(const BYTE* p, unsigned offset, BOOL is32)
{
    uint64_t value = 0;
    memcpy(&value, p + offset, is32 ? 4 : 8);
    return (uintptr_t)value;
}

static BOOL remote_text(HANDLE process, uintptr_t address, WCHAR* out,
    unsigned capacity, BOOL multisz)
{
    unsigned i;
    if (!address)
    {
        out[0] = 0;
        if (capacity > 1)
            out[1] = 0;
        return TRUE;
    }
    for (i = 0; i < capacity; ++i)
    {
        if (!remote_read(
                process, address + i * sizeof(WCHAR), out + i, sizeof(WCHAR)))
            return FALSE;
        if (!out[i] && (!multisz || (i && !out[i - 1])))
            return TRUE;
    }
    return FALSE;
}

static BOOL send_message(
    HWND hwnd, UINT message, WPARAM wp, LPARAM lp, DWORD_PTR* result)
{
    DWORD_PTR temporary = 0;
    return SendMessageTimeoutW(hwnd, message, wp, lp,
               SMTO_ABORTIFHUNG | SMTO_BLOCK, TIMEOUT_MS,
               result ? result : &temporary)
        != 0;
}

static BOOL same_dialog(const Dialog* dialog)
{
    DWORD pid = 0, code = 0;
    return dialog->process && GetExitCodeProcess(dialog->process, &code)
        && code == STILL_ACTIVE && IsWindow(dialog->id.hwnd)
        && GetWindowThreadProcessId(dialog->id.hwnd, &pid)
        && pid == dialog->id.pid
        && (dialog->com
                   ? (uintptr_t)GetWindowLongPtrW(
                         dialog->id.hwnd, GWLP_USERDATA)
                   : (uintptr_t)GetPropW(dialog->id.hwnd, L"FileOpenDlgInfos"))
        == dialog->id.property;
}

static void json_wstring(const WCHAR* value)
{
    int size = WideCharToMultiByte(CP_UTF8, 0, value, -1, NULL, 0, NULL, NULL);
    char* utf8 = (char*)malloc((size_t)size);
    unsigned char* p;
    if (!utf8)
    {
        fputs("null", stdout);
        return;
    }
    WideCharToMultiByte(CP_UTF8, 0, value, -1, utf8, size, NULL, NULL);
    putchar('"');
    for (p = (unsigned char*)utf8; *p; ++p)
    {
        if (*p == '"' || *p == '\\')
            printf("\\%c", *p);
        else if (*p < 32)
            printf("\\u%04x", *p);
        else
            putchar(*p);
    }
    putchar('"');
    free(utf8);
}

static void simple_event(const char* event, const char* reason)
{
    printf("{\"event\":\"%s\",\"hwnd\":%llu,\"pid\":%lu", event,
        (unsigned long long)(uintptr_t)active.id.hwnd,
        (unsigned long)active.id.pid);
    if (reason)
        printf(",\"reason\":\"%s\"", reason);
    puts("}");
    fflush(stdout);
}

static void remember(const Identity* id)
{
    unsigned i;
    if (ignored_count >= sizeof(ignored) / sizeof(ignored[0]))
    {
        for (i = 1; i < ignored_count; ++i)
            ignored[i - 1] = ignored[i];
        --ignored_count;
    }
    ignored[ignored_count++] = *id;
}

static void restore(void)
{
    if (active.hidden && same_dialog(&active))
    {
        ShowWindowAsync(active.id.hwnd, SW_SHOW);
        SetForegroundWindow(active.id.hwnd);
    }
    active.hidden = FALSE;
}

static void clear_active(void)
{
    if (active.process)
        CloseHandle(active.process);
    free(active.selection);
    free(active.field);
    ZeroMemory(&active, sizeof(active));
}

static void fallback(const char* reason)
{
    restore();
    remember(&active.id);
    simple_event("fallback", reason);
    clear_active();
}

/* Copy the opaque absolute PIDL, using only documented SHITEMID lengths.
 * Conversion is local. This avoids pointers in cross-process WM_USER messages
 * and avoids a delayed receiver writing into a freed remote scratch buffer. */
static BOOL current_directory(Dialog* dialog, WCHAR* out)
{
    BYTE pointer_bytes[8] = { 0 };
    BYTE* pidl;
    uintptr_t address;
    size_t offset = 0;
    BOOL ok = FALSE;
    /* COM selections use complete paths and never depend on this value. */
    if (dialog->com || !same_dialog(dialog))
        return FALSE;
    if (!remote_read(dialog->process,
            dialog->id.property + (dialog->target32 ? 80 : 136), pointer_bytes,
            dialog->target32 ? 4 : 8))
        return FALSE;
    address = pointer(pointer_bytes, 0, dialog->target32);
    pidl = (BYTE*)calloc(65536, 1);
    if (!pidl)
        return FALSE;
    while (offset + sizeof(USHORT) < 65536)
    {
        USHORT size = 0;
        if (!remote_read(
                dialog->process, address + offset, &size, sizeof(size)))
            break;
        if (!size)
        {
            memcpy(pidl + offset, &size, sizeof(size));
            ok = same_dialog(dialog)
                && SHGetPathFromIDListW((LPCITEMIDLIST)pidl, out);
            break;
        }
        if (size < sizeof(USHORT) || offset + size + sizeof(USHORT) > 65536
            || !remote_read(
                dialog->process, address + offset, pidl + offset, size))
            break;
        offset += size;
    }
    free(pidl);
    return ok && out[0] != 0;
}

static BOOL allowed_process(HANDLE process)
{
    WCHAR path[MAX_TEXT];
    DWORD size = MAX_TEXT;
    WCHAR* base;
    static const WCHAR root[] = L"C:\\Program Files (x86)\\WXWork\\";
    if (!QueryFullProcessImageNameW(process, 0, path, &size))
        return FALSE;
    if (test_exe[0])
        return !_wcsicmp(path, test_exe);
    if (_wcsnicmp(path, root, wcslen(root)))
        return FALSE;
    if (wcsstr(path, L"\\..\\") || wcsstr(path, L"\\.\\"))
        return FALSE;
    base = wcsrchr(path, L'\\');
    return base
        && (!_wcsicmp(base + 1, L"WXWork.exe")
            || !_wcsicmp(base + 1, L"WXWorkWeb.exe"));
}

/* Wine process enumeration is scoped to this prefix's server. Windowless
 * tray processes still count; the helper must not keep an exited app alive. */
static int target_processes_present(void)
{
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    PROCESSENTRY32W entry;
    BOOL more;
    int present = 0;
    if (snapshot == INVALID_HANDLE_VALUE)
        return -1;
    ZeroMemory(&entry, sizeof(entry));
    entry.dwSize = sizeof(entry);
    more = Process32FirstW(snapshot, &entry);
    if (!more && GetLastError() != ERROR_NO_MORE_FILES)
    {
        CloseHandle(snapshot);
        return -1;
    }
    for (; more; more = Process32NextW(snapshot, &entry))
    {
        HANDLE process;
        WCHAR* base = wcsrchr(entry.szExeFile, L'\\');
        base = base ? base + 1 : entry.szExeFile;
        if (!test_exe[0] && _wcsicmp(base, L"WXWork.exe")
            && _wcsicmp(base, L"WXWorkWeb.exe"))
            continue;
        process = OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, FALSE, entry.th32ProcessID);
        if (process)
        {
            present = allowed_process(process);
            CloseHandle(process);
            if (present)
                break;
        }
    }
    CloseHandle(snapshot);
    return present;
}

static BOOL comdlg_vtable(DWORD pid, uintptr_t address)
{
    HANDLE snapshot = CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    MODULEENTRY32W module;
    BOOL found = FALSE, more;
    if (snapshot == INVALID_HANDLE_VALUE)
        return FALSE;
    ZeroMemory(&module, sizeof(module));
    module.dwSize = sizeof(module);
    for (more = Module32FirstW(snapshot, &module); more;
        more = Module32NextW(snapshot, &module))
    {
        uintptr_t base = (uintptr_t)module.modBaseAddr;
        if (!_wcsicmp(module.szModule, L"comdlg32.dll") && address >= base
            && address - base < module.modBaseSize)
        {
            found = TRUE;
            break;
        }
    }
    CloseHandle(snapshot);
    return found;
}

/* Verified Wine 11.22 FileDialogImpl, using pointer-size-specific offsets.
 * Its classic-looking COM dialog stores This in GWLP_USERDATA, not in the
 * FileOpenDlgInfos property. File selections still go through on_idok and
 * IFileDialogEvents::OnFileOk in the owning process. */
static BOOL read_com_dialog(Dialog* dialog)
{
    BYTE info[304] = { 0 }, spec[16];
    BOOL is32 = dialog->target32;
    unsigned i, used = 0, list_offset = is32 ? 152 : 264;
    uintptr_t specs, title;
    WCHAR cls[64];
    HWND hwnd = dialog->id.hwnd;
    if (!remote_read(
            dialog->process, dialog->id.property, info, is32 ? 172 : 304)
        || u32(info, is32 ? 8 : 16) != 0
        || pointer(info, is32 ? 84 : 152, is32) != (uintptr_t)hwnd
        || !u32(info, is32 ? 32 : 64)
        || !comdlg_vtable(dialog->id.pid, pointer(info, 0, is32)))
        return FALSE;
    dialog->flags = u32(info, is32 ? 36 : 68);
    dialog->filter_count = u32(info, is32 ? 44 : 80);
    dialog->filter_index = u32(info, is32 ? 48 : 84);
    specs = pointer(info, is32 ? 40 : 72, is32);
    if ((dialog->flags & FOS_PICKFOLDERS) || dialog->filter_count > MAX_FILTERS
        || (dialog->filter_count
            && (!specs || dialog->filter_index >= dialog->filter_count)))
        return FALSE;
    /* Extra controls would lose their choices in a simple file chooser. */
    if (pointer(info, list_offset, is32) != dialog->id.property + list_offset
        || pointer(info, list_offset + (is32 ? 4 : 8), is32)
            != dialog->id.property + list_offset
        || pointer(info, is32 ? 168 : 296, is32))
        return FALSE;
    dialog->filename = GetDlgItem(hwnd, ID_FILENAME_EDIT);
    dialog->filetype = GetDlgItem(hwnd, ID_FILETYPE);
    if (!dialog->filename || !dialog->filetype || !GetDlgItem(hwnd, IDOK)
        || !GetDlgItem(hwnd, IDCANCEL))
        return FALSE;
    GetClassNameW(dialog->filename, cls, 64);
    if (_wcsicmp(cls, L"Edit"))
        return FALSE;
    GetClassNameW(dialog->filetype, cls, 64);
    if (_wcsicmp(cls, L"ComboBox"))
        return FALSE;
    for (i = 0; i < dialog->filter_count; ++i)
    {
        if (!remote_read(dialog->process, specs + i * (is32 ? 8 : 16), spec,
                is32 ? 8 : 16)
            || !remote_text(dialog->process, pointer(spec, 0, is32),
                dialog->filters + used, MAX_TEXT - used, FALSE))
            return FALSE;
        used += (unsigned)wcslen(dialog->filters + used) + 1;
        if (used >= MAX_TEXT
            || !remote_text(dialog->process, pointer(spec, is32 ? 4 : 8, is32),
                dialog->filters + used, MAX_TEXT - used, FALSE))
            return FALSE;
        used += (unsigned)wcslen(dialog->filters + used) + 1;
        if (used >= MAX_TEXT)
            return FALSE;
    }
    dialog->filters[used] = 0;
    dialog->output_buffer = specs; /* Immutable filter-array snapshot. */
    dialog->unicode = TRUE;
    dialog->max_file
        = MAX_TEXT - 1; /* Guard's edit-field cap, no OFN buffer. */
    if (dialog->filter_count)
        ++dialog->filter_index; /* Protocol and IFileDialog use 1-based IDs. */
    title = pointer(info, is32 ? 104 : 192, is32);
    if (!remote_text(dialog->process, title, dialog->title, 1024, FALSE))
        return FALSE;
    if (!dialog->title[0])
        GetWindowTextW(hwnd, dialog->title, 1024);
    send_message(
        GetDlgItem(hwnd, IDOK), WM_GETTEXT, 128, (LPARAM)dialog->accept, NULL);
    return TRUE;
}

static BOOL read_dialog(HWND hwnd, Dialog* dialog)
{
    BYTE info[208] = { 0 }, ofn[104] = { 0 };
    WCHAR cls[64];
    BOOL wow64 = FALSE;
    unsigned index, offset, rejection_line = 0;
#define REJECT                                                                 \
    do                                                                         \
    {                                                                          \
        rejection_line = __LINE__;                                             \
        goto reject;                                                           \
    } while (0)
    uintptr_t custom, initdir, title, filters;
    ZeroMemory(dialog, sizeof(*dialog));
    if (!IsWindowVisible(hwnd) || !GetClassNameW(hwnd, cls, 64)
        || wcscmp(cls, L"#32770"))
        return FALSE;
    dialog->id.hwnd = hwnd;
    GetWindowThreadProcessId(hwnd, &dialog->id.pid);
    dialog->id.property = (uintptr_t)GetPropW(hwnd, L"FileOpenDlgInfos");
    if (!dialog->id.property)
    {
        dialog->id.property = (uintptr_t)GetWindowLongPtrW(hwnd, GWLP_USERDATA);
        dialog->com = TRUE;
        if (!dialog->id.property)
            return FALSE;
    }
    for (index = 0; index < ignored_count; ++index)
        if (ignored[index].hwnd == hwnd && ignored[index].pid == dialog->id.pid
            && ignored[index].property == dialog->id.property)
            return FALSE;
    dialog->process
        = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ,
            FALSE, dialog->id.pid);
    if (!dialog->process || !allowed_process(dialog->process)
        || !IsWow64Process(dialog->process, &wow64))
        REJECT;
    dialog->target32 = wow64;
    if (dialog->com)
    {
        if (!read_com_dialog(dialog))
            REJECT;
        goto metadata_ready;
    }
    if (!remote_read(
            dialog->process, dialog->id.property, info, wow64 ? 116 : 204))
        REJECT;
    dialog->ofn = pointer(info, 0, wow64);
    dialog->unicode = u32(info, wow64 ? 4 : 8);
    if (dialog->unicode != 0 && dialog->unicode != 1)
        REJECT;
    if (u32(info, wow64 ? 112 : 200) & FOD_SAVEDLG)
        REJECT;
    dialog->filename = (HWND)pointer(info, wow64 ? 96 : 168, wow64);
    dialog->filetype = (HWND)pointer(info, wow64 ? 88 : 152, wow64);
    if (!IsChild(hwnd, dialog->filename) || !IsChild(hwnd, dialog->filetype))
        REJECT;
    index = (unsigned)GetDlgCtrlID(dialog->filename);
    if (index != ID_FILENAME_EDIT && index != ID_FILENAME_COMBO)
        REJECT;
    if (GetDlgCtrlID(dialog->filetype) != ID_FILETYPE || !GetDlgItem(hwnd, IDOK)
        || !GetDlgItem(hwnd, IDCANCEL))
        REJECT;
    GetClassNameW(dialog->filename, cls, 64);
    if (_wcsicmp(cls, L"Edit") && _wcsicmp(cls, L"ComboBoxEx32")
        && _wcsicmp(cls, L"ComboBox"))
        REJECT;
    GetClassNameW(dialog->filetype, cls, 64);
    if (_wcsicmp(cls, L"ComboBox"))
        REJECT;
    if (!remote_read(dialog->process, dialog->ofn, ofn, wow64 ? 60 : 104))
        REJECT;
    index = u32(ofn, 0);
    if (index != (wow64 ? 88u : 152u) && index != (wow64 ? 76u : 136u))
        REJECT;
    dialog->flags = u32(ofn, wow64 ? 52 : 96);
    dialog->max_file = u32(ofn, wow64 ? 32 : 56);
    dialog->filter_index = u32(ofn, wow64 ? 24 : 44);
    dialog->ofn_size = u32(ofn, 0);
    dialog->output_buffer = pointer(ofn, wow64 ? 28 : 48, wow64);
    if (!(dialog->flags & OFN_EXPLORER) || !dialog->max_file
        || dialog->max_file > 16 * 1024 * 1024)
        REJECT;
    /* This combination can treat a directory as a completed selection. */
    if ((dialog->flags & OFN_NOVALIDATE)
        && !(dialog->flags & OFN_FILEMUSTEXIST))
        REJECT;
    custom = pointer(info, wow64 ? 28 : 56, wow64);
    if (custom)
        REJECT; /* Custom filter index zero needs a separate contract. */
    initdir = pointer(info, wow64 ? 8 : 16, wow64);
    title = pointer(info, wow64 ? 16 : 32, wow64);
    filters = pointer(info, wow64 ? 24 : 48, wow64);
    if (!remote_text(dialog->process, initdir, dialog->initial, MAX_TEXT, FALSE)
        || !remote_text(dialog->process, title, dialog->title, 1024, FALSE)
        || !remote_text(
            dialog->process, filters, dialog->filters, MAX_TEXT, TRUE))
        REJECT;
    if (!dialog->title[0])
        GetWindowTextW(hwnd, dialog->title, 1024);
    send_message(
        GetDlgItem(hwnd, IDOK), WM_GETTEXT, 128, (LPARAM)dialog->accept, NULL);
    for (offset = 0; dialog->filters[offset];)
    {
        offset += (unsigned)wcslen(dialog->filters + offset) + 1;
        if (offset >= MAX_TEXT || !dialog->filters[offset])
            REJECT;
        offset += (unsigned)wcslen(dialog->filters + offset) + 1;
        if (offset >= MAX_TEXT || ++dialog->filter_count > MAX_FILTERS)
            REJECT;
    }
metadata_ready:
    if (!same_dialog(dialog))
        REJECT;
    current_directory(dialog, dialog->current);
    dialog->started = GetTickCount64();
    return TRUE;
reject:
    if (test_exe[0])
    {
        static ULONGLONG last_diagnostic;
        ULONGLONG now = GetTickCount64();
        if (now - last_diagnostic > 2000)
        {
            fprintf(stderr,
                "test dialog rejected line=%u hwnd=%llu pid=%lu target32=%d "
                "unicode=%d filename_id=%d filetype_id=%d ofn_size=%u "
                "flags=%lu max_file=%lu\n",
                rejection_line, (unsigned long long)(uintptr_t)hwnd,
                (unsigned long)dialog->id.pid, dialog->target32,
                dialog->unicode, GetDlgCtrlID(dialog->filename),
                GetDlgCtrlID(dialog->filetype), u32(ofn, 0),
                (unsigned long)dialog->flags, (unsigned long)dialog->max_file);
            fflush(stderr);
            last_diagnostic = now;
        }
    }
#undef REJECT
    if (dialog->process)
        CloseHandle(dialog->process);
    ZeroMemory(dialog, sizeof(*dialog));
    return FALSE;
}

static void emit_open(void)
{
    unsigned i, offset = 0;
    printf("{\"event\":\"open\",\"hwnd\":%llu,\"pid\":%lu,\"flags\":%lu,"
           "\"multi\":%s,\"max_file\":%lu,\"unicode\":%s,\"filter_index\":%lu,"
           "\"owner\":%llu,\"title\":",
        (unsigned long long)(uintptr_t)active.id.hwnd,
        (unsigned long)active.id.pid, (unsigned long)active.flags,
        active.flags & OFN_ALLOWMULTISELECT ? "true" : "false",
        (unsigned long)active.max_file, active.unicode ? "true" : "false",
        (unsigned long)active.filter_index,
        (unsigned long long)(uintptr_t)GetWindow(active.id.hwnd, GW_OWNER));
    json_wstring(active.title);
    printf(",\"kind\":\"%s\"", active.com ? "com" : "classic");
    fputs(",\"accept\":", stdout);
    json_wstring(active.accept);
    fputs(",\"initial_dir\":", stdout);
    json_wstring(active.initial);
    fputs(",\"current_dir\":", stdout);
    json_wstring(active.current);
    fputs(",\"filters\":[", stdout);
    for (i = 0; i < active.filter_count; ++i)
    {
        if (i)
            putchar(',');
        fputs("{\"name\":", stdout);
        json_wstring(active.filters + offset);
        offset += (unsigned)wcslen(active.filters + offset) + 1;
        fputs(",\"pattern\":", stdout);
        json_wstring(active.filters + offset);
        offset += (unsigned)wcslen(active.filters + offset) + 1;
        putchar('}');
    }
    puts("]}");
    fflush(stdout);
}

static BOOL CALLBACK discover(HWND hwnd, LPARAM unused)
{
    Dialog candidate;
    (void)unused;
    if (read_dialog(hwnd, &candidate))
    {
        active = candidate;
        emit_open();
        return FALSE;
    }
    return TRUE;
}

static BOOL set_text(const WCHAR* text)
{
    size_t size = wcslen(text) + 1;
    WCHAR* copy = (WCHAR*)calloc(size, sizeof(WCHAR));
    BOOL ok = copy && same_dialog(&active)
        && send_message(active.filename, WM_SETTEXT, 0, (LPARAM)text, NULL)
        && send_message(
            active.filename, WM_GETTEXT, (WPARAM)size, (LPARAM)copy, NULL)
        && !wcscmp(text, copy);
    free(copy);
    return ok;
}

static BOOL post_ok(void)
{
    BYTE info[8] = { 0 }, ofn[104] = { 0 };
    BOOL is32 = active.target32;
    if (active.com)
    {
        BYTE item[160] = { 0 };
        if (!same_dialog(&active)
            || !remote_read(
                active.process, active.id.property, item, is32 ? 88 : 160)
            || u32(item, is32 ? 8 : 16) != 0
            || u32(item, is32 ? 36 : 68) != active.flags
            || u32(item, is32 ? 44 : 80) != active.filter_count
            || pointer(item, is32 ? 40 : 72, is32) != active.output_buffer
            || pointer(item, is32 ? 84 : 152, is32)
                != (uintptr_t)active.id.hwnd)
            return FALSE;
        return PostMessageW(active.id.hwnd, WM_COMMAND,
            MAKEWPARAM(IDOK, BN_CLICKED),
            (LPARAM)GetDlgItem(active.id.hwnd, IDOK));
    }
    /* Filter, edit, and navigation notifications can invoke application
     * hooks. Recheck the output contract after those callbacks, immediately
     * before every original IDOK, including the final multi-file commit. */
    if (!same_dialog(&active)
        || !remote_read(active.process, active.id.property, info, is32 ? 4 : 8)
        || pointer(info, 0, is32) != active.ofn
        || !remote_read(active.process, active.ofn, ofn, is32 ? 60 : 104)
        || u32(ofn, 0) != active.ofn_size
        || u32(ofn, is32 ? 52 : 96) != active.flags
        || u32(ofn, is32 ? 32 : 56) != active.max_file
        || pointer(ofn, is32 ? 28 : 48, is32) != active.output_buffer)
        return FALSE;
    return PostMessageW(active.id.hwnd, WM_COMMAND,
        MAKEWPARAM(IDOK, BN_CLICKED), (LPARAM)GetDlgItem(active.id.hwnd, IDOK));
}

static BOOL change_filter(unsigned index)
{
    DWORD_PTR value;
    if (!active.filter_count)
        return index == 0;
    if (!index || index > active.filter_count)
        return FALSE;
    if (!send_message(active.filetype, CB_GETCURSEL, 0, 0, &value))
        return FALSE;
    if (value == index - 1)
        return TRUE;
    if (!send_message(active.filetype, CB_SETCURSEL, index - 1, 0, &value)
        || value == (DWORD_PTR)CB_ERR)
        return FALSE;
    return send_message(active.id.hwnd, WM_COMMAND,
        MAKEWPARAM(ID_FILETYPE, active.com ? CBN_SELCHANGE : CBN_SELENDOK),
        (LPARAM)active.filetype, NULL);
}

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

static WCHAR* decode_hex(const char* hex, size_t* characters)
{
    size_t length = strlen(hex), i;
    WCHAR* decoded;
    if (length < 12 || length % 4 || length / 4 >= MAX_TEXT)
        return NULL;
    decoded = (WCHAR*)calloc(length / 4 + 1, sizeof(WCHAR));
    if (!decoded)
        return NULL;
    for (i = 0; i < length / 4; ++i)
    {
        int a = hex_nibble(hex[4 * i]), b = hex_nibble(hex[4 * i + 1]);
        int c = hex_nibble(hex[4 * i + 2]), d = hex_nibble(hex[4 * i + 3]);
        if (a < 0 || b < 0 || c < 0 || d < 0)
        {
            free(decoded);
            return NULL;
        }
        decoded[i] = (WCHAR)((a * 16 + b) | ((c * 16 + d) << 8));
    }
    *characters = length / 4;
    return decoded;
}

static BOOL prepare_selection(const char* hex)
{
    size_t size = 0, offset, directory_length, field_size = 1, result_size;
    WCHAR *selection = decode_hex(hex, &size), *field, full[MAX_PATH];
    unsigned count = 0;
    if (!selection || size < 4 || selection[size - 1] || selection[size - 2])
        goto reject;
    directory_length = wcslen(selection);
    if (directory_length < 3 || directory_length >= MAX_PATH - 2
        || selection[1] != L':' || selection[2] != L'\\'
        || !((selection[0] >= L'A' && selection[0] <= L'Z')
            || (selection[0] >= L'a' && selection[0] <= L'z'))
        || wcschr(selection, L'"'))
        goto reject;
    offset = directory_length + 1;
    while (offset < size - 1 && selection[offset])
    {
        const WCHAR* name = selection + offset;
        size_t length = wcslen(name);
        DWORD attrs;
        if (!length || length > MAX_PATH - 1 || wcspbrk(name, L"\\/:\"*?<>|")
            || !wcscmp(name, L".") || !wcscmp(name, L"..")
            || directory_length + length + 2 >= MAX_PATH)
            goto reject;
        swprintf(full, MAX_PATH, L"%ls%ls%ls", selection,
            selection[directory_length - 1] == L'\\' ? L"" : L"\\", name);
        attrs = GetFileAttributesW(full);
        if (attrs == INVALID_FILE_ATTRIBUTES
            || (attrs & FILE_ATTRIBUTE_DIRECTORY))
            goto reject;
        field_size += (active.com ? wcslen(full) : length) + 3;
        offset += length + 1;
        ++count;
    }
    if (!count || offset != size - 1
        || (count > 1 && !(active.flags & OFN_ALLOWMULTISELECT)))
        goto reject;
    result_size = count > 1 ? size : wcslen(full) + 1;
    /* Four bytes per UTF-16 code unit safely bounds supported ANSI encodings.
     */
    if ((!active.unicode && result_size > SIZE_MAX / 4)
        || (active.unicode ? result_size : result_size * 4) > active.max_file)
        goto reject;
    if (field_size >= MAX_TEXT)
        goto reject;
    field = (WCHAR*)calloc(field_size + MAX_PATH, sizeof(WCHAR));
    if (!field)
        goto reject;
    if (count == 1)
        wcscpy(field, full);
    else
    {
        for (offset = directory_length + 1; selection[offset];
            offset += wcslen(selection + offset) + 1)
        {
            if (field[0])
                wcscat(field, L" ");
            wcscat(field, L"\"");
            if (active.com)
            {
                wcscat(field, selection);
                if (selection[directory_length - 1] != L'\\')
                    wcscat(field, L"\\");
            }
            wcscat(field, selection + offset);
            wcscat(field, L"\"");
        }
    }
    active.selection = selection;
    active.selection_count = count;
    active.field = field;
    return TRUE;
reject:
    free(selection);
    return FALSE;
}

static BOOL same_directory(const WCHAR* a, const WCHAR* b)
{
    size_t na = wcslen(a), nb = wcslen(b);
    while (na > 3 && a[na - 1] == L'\\')
        --na;
    while (nb > 3 && b[nb - 1] == L'\\')
        --nb;
    return na == nb && !_wcsnicmp(a, b, na);
}

static void select_files(unsigned filter, const char* hex)
{
    WCHAR directory[MAX_PATH];
    if (!prepare_selection(hex))
    {
        fallback("invalid-selection-or-buffer-limit");
        return;
    }
    if (!change_filter(filter))
    {
        fallback("filter-update-failed");
        return;
    }
    active.phase_started = GetTickCount64();
    if (active.com || active.selection_count == 1)
    {
        if (!set_text(active.field) || !post_ok())
        {
            fallback("selection-submit-failed");
            return;
        }
        active.phase = 3;
        return;
    }
    if (current_directory(&active, directory)
        && same_directory(directory, active.selection))
    {
        if (!set_text(active.field) || !post_ok())
        {
            fallback("selection-submit-failed");
            return;
        }
        active.phase = 3;
        return;
    }
    wcscpy(directory, active.selection);
    if (directory[wcslen(directory) - 1] != L'\\')
        wcscat(directory, L"\\");
    if (!set_text(directory) || !post_ok())
    {
        fallback("directory-navigation-failed");
        return;
    }
    active.phase = 2;
}

static void command(char* line)
{
    char *operation = strtok(line, " \r"), *handle_text, *pid_text, *tail;
    unsigned long long handle;
    unsigned long pid;
    if (!operation)
        return;
    if (!strcmp(operation, "quit"))
    {
        InterlockedExchange(&stopping, 1);
        return;
    }
    handle_text = strtok(NULL, " \r");
    pid_text = strtok(NULL, " \r");
    if (!handle_text || !pid_text || !active.process)
        return;
    handle = strtoull(handle_text, &tail, 10);
    if (*tail)
        return;
    pid = strtoul(pid_text, &tail, 10);
    if (*tail || handle != (unsigned long long)(uintptr_t)active.id.hwnd
        || pid != active.id.pid || !same_dialog(&active))
        return;
    if (!strcmp(operation, "fallback"))
    {
        fallback("supervisor-request");
        return;
    }
    if (!strcmp(operation, "hide") && active.phase == 0)
    {
        if (strtok(NULL, " \r"))
            return;
        active.hidden = TRUE;
        active.phase = 1;
        ShowWindowAsync(active.id.hwnd, SW_HIDE);
        simple_event("hidden", NULL);
    }
    else if (!strcmp(operation, "cancel") && active.phase <= 1)
    {
        if (strtok(NULL, " \r"))
            return;
        if (!PostMessageW(active.id.hwnd, WM_COMMAND,
                MAKEWPARAM(IDCANCEL, BN_CLICKED),
                (LPARAM)GetDlgItem(active.id.hwnd, IDCANCEL)))
        {
            fallback("cancel-failed");
            return;
        }
        active.phase = 3;
        active.phase_started = GetTickCount64();
    }
    else if (!strcmp(operation, "select") && active.phase <= 1)
    {
        char *filter = strtok(NULL, " \r"), *hex = strtok(NULL, " \r");
        unsigned long index;
        if (!filter || !hex || strtok(NULL, " \r"))
        {
            fallback("invalid-command");
            return;
        }
        index = strtoul(filter, &tail, 10);
        if (*tail || index > MAX_FILTERS)
        {
            fallback("invalid-filter");
            return;
        }
        select_files((unsigned)index, hex);
    }
}

static BOOL console_handler(DWORD signal)
{
    (void)signal;
    InterlockedExchange(&stopping, 1);
    restore();
    return TRUE;
}

int wmain(int argc, WCHAR** argv)
{
    HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
    HANDLE reader;
    HRESULT com_status;
    ULONGLONG started = GetTickCount64(), last_target = started,
              last_inventory = 0;
    BOOL seen_target = FALSE;
    if (argc == 3 && !wcscmp(argv[1], L"--test-exe"))
    {
        if (wcslen(argv[2]) >= MAX_TEXT || wcslen(argv[2]) < 4
            || argv[2][1] != L':' || argv[2][2] != L'\\')
            return 2;
        wcscpy(test_exe, argv[2]);
    }
    else if (argc != 1)
        return 2;
    if (GetFileType(input) != FILE_TYPE_PIPE
        || GetFileType(GetStdHandle(STD_OUTPUT_HANDLE)) != FILE_TYPE_PIPE)
        return 2;
    com_status = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    InitializeCriticalSection(&commands_lock);
    reader = CreateThread(NULL, 0, read_commands, input, 0, NULL);
    if (!reader)
        return 2;
    SetConsoleCtrlHandler(console_handler, TRUE);
    while (!stopping && !ferror(stdout))
    {
        ULONGLONG now = GetTickCount64();
        if (now - last_inventory >= 1000)
        {
            int present = target_processes_present();
            last_inventory = now;
            if (present > 0)
            {
                seen_target = TRUE;
                last_target = now;
            }
            else if (present == 0
                && ((seen_target && now - last_target >= 5000)
                    || (!seen_target && now - started >= 60000)))
                break;
        }
        for (;;)
        {
            CommandNode* node = take_command();
            if (!node)
                break;
            command(node->text);
            free(node);
        }
        if (active.process)
        {
            now = GetTickCount64();
            if (!same_dialog(&active))
            {
                simple_event("closed", NULL);
                clear_active();
            }
            else if (active.phase == 0 && now - active.started > 30000)
                fallback("supervisor-timeout");
            else if (active.phase == 2 && now - active.phase_started >= 150)
            {
                WCHAR current[MAX_PATH];
                if (current_directory(&active, current)
                    && same_directory(current, active.selection))
                {
                    if (!set_text(active.field) || !post_ok())
                        fallback("selection-submit-failed");
                    else
                    {
                        active.phase = 3;
                        active.phase_started = now;
                    }
                }
                else if (now - active.phase_started > 3000)
                    fallback("directory-confirmation-failed");
            }
            else if (active.phase == 3 && now - active.phase_started > 1200)
                fallback("original-dialog-remained-open");
        }
        else
            EnumWindows(discover, 0);
        Sleep(40);
    }
    restore();
    clear_active();
    InterlockedExchange(&stopping, 1);
    CancelSynchronousIo(reader);
    WaitForSingleObject(reader, 1000);
    CloseHandle(reader);
    if (SUCCEEDED(com_status))
        CoUninitialize();
    return 0;
}
