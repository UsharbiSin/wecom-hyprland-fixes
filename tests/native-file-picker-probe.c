/* Standalone dialog probe: operates only on its own dialog and test hooks. */
#define COBJMACROS
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <commdlg.h>
#include <shobjidl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#define BUFFER_CHARS 65536
#define MAX_PATHS 1024

static WCHAR* selected[MAX_PATHS];
static unsigned selected_count;
static BOOL hook_enabled, hook_veto;
static unsigned hook_fileok;

typedef struct ProbeEvents
{
    IFileDialogEvents iface;
    LONG references;
} ProbeEvents;

static ULONG STDMETHODCALLTYPE events_addref(IFileDialogEvents* iface)
{
    ProbeEvents* events = (ProbeEvents*)iface;
    return (ULONG)InterlockedIncrement(&events->references);
}

static ULONG STDMETHODCALLTYPE events_release(IFileDialogEvents* iface)
{
    ProbeEvents* events = (ProbeEvents*)iface;
    ULONG references = (ULONG)InterlockedDecrement(&events->references);
    if (!references)
        free(events);
    return references;
}

static HRESULT STDMETHODCALLTYPE events_query(
    IFileDialogEvents* iface, REFIID iid, void** object)
{
    if (!object)
        return E_POINTER;
    *object = NULL;
    if (!IsEqualIID(iid, &IID_IUnknown)
        && !IsEqualIID(iid, &IID_IFileDialogEvents))
        return E_NOINTERFACE;
    *object = iface;
    events_addref(iface);
    return S_OK;
}

static HRESULT STDMETHODCALLTYPE events_fileok(
    IFileDialogEvents* iface, IFileDialog* dialog)
{
    IOleWindow* window = NULL;
    HWND hwnd = NULL;
    HRESULT hr;
    (void)iface;
    ++hook_fileok;
    if (!hook_veto || hook_fileok != 1)
        return S_OK;
    hr = IFileDialog_QueryInterface(
        dialog, &IID_IOleWindow, (void**)&window);
    if (SUCCEEDED(hr))
    {
        hr = IOleWindow_GetWindow(window, &hwnd);
        IOleWindow_Release(window);
    }
    if (SUCCEEDED(hr)
        && (!hwnd || !PostMessageW(hwnd, WM_COMMAND,
                         MAKEWPARAM(IDCANCEL, BN_CLICKED), 0)))
        hr = E_FAIL;
    if (FAILED(hr))
        IFileDialog_Close(dialog, hr);
    /* The original modal loop processes cancellation after this veto. */
    return S_FALSE;
}

static HRESULT STDMETHODCALLTYPE events_folder_changing(
    IFileDialogEvents* iface, IFileDialog* dialog, IShellItem* folder)
{
    (void)iface;
    (void)dialog;
    (void)folder;
    return E_NOTIMPL;
}

static HRESULT STDMETHODCALLTYPE events_noop(
    IFileDialogEvents* iface, IFileDialog* dialog)
{
    (void)iface;
    (void)dialog;
    return E_NOTIMPL;
}

static HRESULT STDMETHODCALLTYPE events_share_violation(
    IFileDialogEvents* iface, IFileDialog* dialog, IShellItem* item,
    FDE_SHAREVIOLATION_RESPONSE* response)
{
    (void)iface;
    (void)dialog;
    (void)item;
    if (response)
        *response = FDESVR_DEFAULT;
    return E_NOTIMPL;
}

static HRESULT STDMETHODCALLTYPE events_overwrite(
    IFileDialogEvents* iface, IFileDialog* dialog, IShellItem* item,
    FDE_OVERWRITE_RESPONSE* response)
{
    (void)iface;
    (void)dialog;
    (void)item;
    if (response)
        *response = FDEOR_DEFAULT;
    return E_NOTIMPL;
}

static IFileDialogEventsVtbl events_vtable = { events_query, events_addref,
    events_release, events_fileok, events_folder_changing, events_noop,
    events_noop, events_share_violation, events_noop, events_overwrite };

static UINT_PTR CALLBACK probe_hook(
    HWND hwnd, UINT message, WPARAM parameter, LPARAM data)
{
    (void)parameter;
    if (message == WM_INITDIALOG)
        SetDlgItemTextW(GetParent(hwnd), IDOK, L"发送");
    else if (message == WM_NOTIFY && data && ((NMHDR*)data)->code == CDN_FILEOK)
    {
        ++hook_fileok;
        if (hook_veto && hook_fileok == 1)
        {
            SetWindowLongPtrW(hwnd, DWLP_MSGRESULT, 1);
            SetTimer(hwnd, 42, 100, NULL);
            return 1;
        }
    }
    else if (message == WM_TIMER && parameter == 42)
    {
        KillTimer(hwnd, 42);
        PostMessageW(
            GetParent(hwnd), WM_COMMAND, MAKEWPARAM(IDCANCEL, BN_CLICKED), 0);
    }
    return 0;
}

static void json_string(const char* value)
{
    const unsigned char* p = (const unsigned char*)value;
    putchar('"');
    for (; *p; ++p)
    {
        if (*p == '"' || *p == '\\')
            printf("\\%c", *p);
        else if (*p < 32)
            printf("\\u%04x", *p);
        else
            putchar(*p);
    }
    putchar('"');
}

static void json_wstring(const WCHAR* value)
{
    int length
        = WideCharToMultiByte(CP_UTF8, 0, value, -1, NULL, 0, NULL, NULL);
    char* utf8 = (char*)malloc((size_t)length);
    if (!utf8)
    {
        fputs("null", stdout);
        return;
    }
    WideCharToMultiByte(CP_UTF8, 0, value, -1, utf8, length, NULL, NULL);
    json_string(utf8);
    free(utf8);
}

static int append_path(const WCHAR* directory, const WCHAR* name)
{
    size_t size = wcslen(name) + 1;
    WCHAR* path;
    if (selected_count >= MAX_PATHS)
        return 0;
    if (directory)
        size += wcslen(directory) + 1;
    path = (WCHAR*)calloc(size, sizeof(WCHAR));
    if (!path)
        return 0;
    if (directory)
    {
        wcscpy(path, directory);
        wcscat(path, L"\\");
    }
    wcscat(path, name);
    selected[selected_count++] = path;
    return 1;
}

static int parse_paths(const WCHAR* buffer, BOOL multi)
{
    const WCHAR* next = buffer + wcslen(buffer) + 1;
    if (!multi || !*next)
        return append_path(NULL, buffer);
    for (; *next; next += wcslen(next) + 1)
        if (!append_path(buffer, next))
            return 0;
    return 1;
}

static const WCHAR filter_w[] = L"All files\0*.*\0Text files\0*.txt\0\0";
static const char filter_a[] = "All files\0*.*\0Text files\0*.txt\0\0";

/* 0 = selected, 1 = cancelled, 2 = API or probe failure. */
static int classic_w(
    BOOL multi, const WCHAR* initial, DWORD* error, UINT* filter_index)
{
    OPENFILENAMEW ofn;
    WCHAR* buffer = (WCHAR*)calloc(BUFFER_CHARS, sizeof(WCHAR));
    BOOL result;
    if (!buffer)
    {
        *error = ERROR_NOT_ENOUGH_MEMORY;
        return 2;
    }
    ZeroMemory(&ofn, sizeof(ofn));
    ofn.lStructSize = sizeof(ofn);
    ofn.lpstrFilter = filter_w;
    ofn.nFilterIndex = 1;
    ofn.lpstrFile = buffer;
    ofn.nMaxFile = BUFFER_CHARS;
    ofn.lpstrInitialDir = initial;
    ofn.lpstrTitle = L"WeCom isolated probe - GetOpenFileNameW";
    ofn.Flags = OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST
        | OFN_NOCHANGEDIR | (multi ? OFN_ALLOWMULTISELECT : 0);
    if (hook_enabled)
    {
        ofn.Flags |= OFN_ENABLEHOOK;
        ofn.lpfnHook = probe_hook;
    }
    result = GetOpenFileNameW(&ofn);
    *filter_index = ofn.nFilterIndex;
    if (!result)
    {
        *error = CommDlgExtendedError();
        free(buffer);
        return *error ? 2 : 1;
    }
    result = parse_paths(buffer, multi);
    free(buffer);
    if (!result)
    {
        *error = ERROR_NOT_ENOUGH_MEMORY;
        return 2;
    }
    return 0;
}

static int classic_a(
    BOOL multi, const WCHAR* initial, DWORD* error, UINT* filter_index)
{
    OPENFILENAMEA ofn;
    char* buffer = (char*)calloc(BUFFER_CHARS, 1);
    WCHAR* wide = (WCHAR*)calloc(BUFFER_CHARS, sizeof(WCHAR));
    char initial_a[32768];
    BOOL result;
    size_t offset = 0, wide_offset = 0;
    if (!buffer || !wide)
    {
        free(buffer);
        free(wide);
        *error = ERROR_NOT_ENOUGH_MEMORY;
        return 2;
    }
    if (!WideCharToMultiByte(
            CP_ACP, 0, initial, -1, initial_a, sizeof(initial_a), NULL, NULL))
    {
        *error = GetLastError();
        free(buffer);
        free(wide);
        return 2;
    }
    ZeroMemory(&ofn, sizeof(ofn));
    ofn.lStructSize = sizeof(ofn);
    ofn.lpstrFilter = filter_a;
    ofn.nFilterIndex = 1;
    ofn.lpstrFile = buffer;
    ofn.nMaxFile = BUFFER_CHARS;
    ofn.lpstrInitialDir = initial_a;
    ofn.lpstrTitle = "WeCom isolated probe - GetOpenFileNameA";
    ofn.Flags = OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST
        | OFN_NOCHANGEDIR | (multi ? OFN_ALLOWMULTISELECT : 0);
    if (hook_enabled)
    {
        ofn.Flags |= OFN_ENABLEHOOK;
        ofn.lpfnHook = probe_hook;
    }
    result = GetOpenFileNameA(&ofn);
    *filter_index = ofn.nFilterIndex;
    if (!result)
    {
        *error = CommDlgExtendedError();
        free(buffer);
        free(wide);
        return *error ? 2 : 1;
    }
    do
    {
        size_t size = strlen(buffer + offset) + 1;
        int written = MultiByteToWideChar(CP_ACP, 0, buffer + offset, -1,
            wide + wide_offset, BUFFER_CHARS - (int)wide_offset);
        if (!written)
        {
            *error = GetLastError();
            free(buffer);
            free(wide);
            return 2;
        }
        offset += size;
        wide_offset += (size_t)written;
    } while (multi && offset < BUFFER_CHARS - 1 && buffer[offset]);
    result = parse_paths(wide, multi);
    free(buffer);
    free(wide);
    if (!result)
    {
        *error = ERROR_NOT_ENOUGH_MEMORY;
        return 2;
    }
    return 0;
}

static int com_dialog(
    BOOL multi, const WCHAR* initial, DWORD* error, UINT* filter_index)
{
    IFileOpenDialog* dialog = NULL;
    IShellItem* folder = NULL;
    IShellItemArray* items = NULL;
    ProbeEvents* events = NULL;
    FILEOPENDIALOGOPTIONS options;
    COMDLG_FILTERSPEC filters[]
        = { { L"All files", L"*.*" }, { L"Text files", L"*.txt" } };
    DWORD count = 0, i, cookie = 0;
    BOOL advised = FALSE;
    HRESULT hr = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    BOOL initialized = SUCCEEDED(hr);
    if (FAILED(hr))
        goto done;
    hr = CoCreateInstance(&CLSID_FileOpenDialog, NULL, CLSCTX_INPROC_SERVER,
        &IID_IFileOpenDialog, (void**)&dialog);
    if (FAILED(hr))
        goto done;
    hr = IFileOpenDialog_GetOptions(dialog, &options);
    if (FAILED(hr))
        goto done;
    hr = IFileOpenDialog_SetOptions(dialog,
        options | FOS_FORCEFILESYSTEM | FOS_FILEMUSTEXIST | FOS_PATHMUSTEXIST
            | (multi ? FOS_ALLOWMULTISELECT : 0));
    if (FAILED(hr))
        goto done;
    hr = IFileOpenDialog_SetFileTypes(dialog, 2, filters);
    if (FAILED(hr))
        goto done;
    hr = IFileOpenDialog_SetTitle(
        dialog, L"WeCom isolated probe - IFileOpenDialog");
    if (FAILED(hr))
        goto done;
    if (hook_enabled)
    {
        events = (ProbeEvents*)calloc(1, sizeof(*events));
        if (!events)
        {
            hr = E_OUTOFMEMORY;
            goto done;
        }
        events->iface.lpVtbl = &events_vtable;
        events->references = 1;
        hr = IFileOpenDialog_Advise(dialog, &events->iface, &cookie);
        if (FAILED(hr))
            goto done;
        advised = TRUE;
        hr = IFileOpenDialog_SetOkButtonLabel(dialog, L"发送");
        if (FAILED(hr))
            goto done;
    }
    hr = SHCreateItemFromParsingName(
        initial, NULL, &IID_IShellItem, (void**)&folder);
    if (FAILED(hr))
        goto done;
    hr = IFileOpenDialog_SetFolder(dialog, folder);
    if (FAILED(hr))
        goto done;
    hr = IFileOpenDialog_Show(dialog, NULL);
    if (FAILED(hr))
        goto done;
    hr = IFileOpenDialog_GetFileTypeIndex(dialog, filter_index);
    if (FAILED(hr))
        goto done;
    hr = IFileOpenDialog_GetResults(dialog, &items);
    if (FAILED(hr))
        goto done;
    hr = IShellItemArray_GetCount(items, &count);
    if (FAILED(hr))
        goto done;
    for (i = 0; i < count; ++i)
    {
        IShellItem* item = NULL;
        PWSTR path = NULL;
        hr = IShellItemArray_GetItemAt(items, i, &item);
        if (FAILED(hr))
            break;
        hr = IShellItem_GetDisplayName(item, SIGDN_FILESYSPATH, &path);
        IShellItem_Release(item);
        if (SUCCEEDED(hr) && !append_path(NULL, path))
            hr = E_OUTOFMEMORY;
        CoTaskMemFree(path);
        if (FAILED(hr))
            break;
    }
done:
    if (advised)
        IFileOpenDialog_Unadvise(dialog, cookie);
    if (items)
        IShellItemArray_Release(items);
    if (folder)
        IShellItem_Release(folder);
    if (dialog)
        IFileOpenDialog_Release(dialog);
    if (events)
        IFileDialogEvents_Release(&events->iface);
    if (initialized)
        CoUninitialize();
    *error = (DWORD)hr;
    if (hr == HRESULT_FROM_WIN32(ERROR_CANCELLED))
        return 1;
    return FAILED(hr) ? 2 : 0;
}

int wmain(int argc, WCHAR** argv)
{
    const WCHAR *mode, *initial = L"Z:\\tmp\\wecom-picker-test";
    BOOL multi = FALSE;
    DWORD error = 0;
    UINT filter_index = 0;
    unsigned i;
    int status;
    if (argc < 2 || !wcscmp(argv[1], L"--help"))
    {
        puts("Usage: native-file-picker-probe.exe classic-w|classic-a|com "
             "[--multi] [--hook|--hook-veto] [--initial WINDOWS_DIRECTORY]");
        puts("Prints one UTF-8 JSON result; exit 0=selected, 3=cancelled, "
             "2=error.");
        puts("Creates no files and never sends or uploads selected files.");
        return argc < 2 ? 2 : 0;
    }
    mode = argv[1];
    for (i = 2; i < (unsigned)argc; ++i)
    {
        if (!wcscmp(argv[i], L"--multi"))
            multi = TRUE;
        else if (!wcscmp(argv[i], L"--hook"))
            hook_enabled = TRUE;
        else if (!wcscmp(argv[i], L"--hook-veto"))
            hook_enabled = hook_veto = TRUE;
        else if (!wcscmp(argv[i], L"--initial") && i + 1 < (unsigned)argc)
            initial = argv[++i];
        else
        {
            fputs("Unknown or incomplete argument\n", stderr);
            return 2;
        }
    }
    if (!wcscmp(mode, L"classic-w"))
        status = classic_w(multi, initial, &error, &filter_index);
    else if (!wcscmp(mode, L"classic-a"))
        status = classic_a(multi, initial, &error, &filter_index);
    else if (!wcscmp(mode, L"com"))
        status = com_dialog(multi, initial, &error, &filter_index);
    else
    {
        fputs("Unknown mode\n", stderr);
        return 2;
    }
    printf("{\"mode\":");
    json_wstring(mode);
    printf(",\"hook_enabled\":%s,\"hook_veto\":%s,\"hook_fileok\":%u",
        hook_enabled ? "true" : "false", hook_veto ? "true" : "false",
        hook_fileok);
    printf(",\"status\":\"%s\",\"error\":%lu,\"arch_bits\":%u,"
           "\"ansi_codepage\":%u,\"multi\":%s,\"filter_index\":%u,\"paths\":[",
        status == 0       ? "selected"
            : status == 1 ? "cancelled"
                          : "error",
        (unsigned long)error, (unsigned)(sizeof(void*) * 8), GetACP(),
        multi ? "true" : "false", filter_index);
    for (i = 0; i < selected_count; ++i)
    {
        DWORD attributes = GetFileAttributesW(selected[i]);
        if (i)
            putchar(',');
        fputs("{\"path\":", stdout);
        json_wstring(selected[i]);
        printf(",\"exists\":%s,\"directory\":%s}",
            attributes != INVALID_FILE_ATTRIBUTES ? "true" : "false",
            attributes != INVALID_FILE_ATTRIBUTES
                    && (attributes & FILE_ATTRIBUTE_DIRECTORY)
                ? "true"
                : "false");
        free(selected[i]);
    }
    puts("]}");
    return status == 1 ? 3 : status == 2 ? 2 : 0;
}
