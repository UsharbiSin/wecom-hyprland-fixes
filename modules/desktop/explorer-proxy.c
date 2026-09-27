#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#include <stdint.h>
#include <string.h>
#include <wchar.h>
#include <wctype.h>
#include <windows.h>

/* 只转交普通本地目录；桌面和其他 Explorer 参数回退原程序。 */
typedef char *(__cdecl *unix_name_fn)(const WCHAR *);
typedef WCHAR *(__cdecl *dos_name_fn)(const char *);

static WCHAR *environment_path(const WCHAR *name) {
  WCHAR value[32768];
  DWORD size = GetEnvironmentVariableW(name, value, 32768);
  if (!size || size >= 32768 || value[0] != L'/')
    return NULL;
  int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1,
                                   NULL, 0, NULL, NULL);
  if (!length)
    return NULL;
  char *native = HeapAlloc(GetProcessHeap(), 0, length);
  if (!native)
    return NULL;
  if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, native,
                           length, NULL, NULL)) {
    HeapFree(GetProcessHeap(), 0, native);
    return NULL;
  }
  dos_name_fn convert = (dos_name_fn)(void *)GetProcAddress(
      GetModuleHandleW(L"kernel32.dll"), "wine_get_dos_file_name");
  WCHAR *result = convert ? convert(native) : NULL;
  HeapFree(GetProcessHeap(), 0, native);
  return result;
}

static WCHAR *quote(WCHAR *out, const WCHAR *in) {
  *out++ = L'"';
  while (*in) {
    unsigned slashes = 0;
    while (*in == L'\\') {
      ++slashes;
      ++in;
    }
    unsigned count = (*in == L'"' || !*in) ? slashes * 2 : slashes;
    while (count--)
      *out++ = L'\\';
    if (*in == L'"')
      *out++ = L'\\';
    if (*in)
      *out++ = *in++;
  }
  *out++ = L'"';
  *out = 0;
  return out;
}

static int fallback(const WCHAR *args) {
#ifdef _WIN64
  const WCHAR *variable = L"WECOM_EXPLORER_ORIGINAL64";
#else
  const WCHAR *variable = L"WECOM_EXPLORER_ORIGINAL32";
#endif
  WCHAR *original = environment_path(variable);
  if (!original)
    return 1;
  size_t capacity = 2 * wcslen(original) + wcslen(args) + 4;
  WCHAR *command = HeapAlloc(GetProcessHeap(), 0, capacity * sizeof(WCHAR));
  if (!command) {
    HeapFree(GetProcessHeap(), 0, original);
    return 1;
  }
  WCHAR *end = quote(command, original);
  *end++ = L' ';
  wcscpy(end, args);
  STARTUPINFOW startup = {.cb = sizeof(startup)};
  PROCESS_INFORMATION process;
  BOOL ok = CreateProcessW(original, command, NULL, NULL, FALSE, 0, NULL, NULL,
                           &startup, &process);
  HeapFree(GetProcessHeap(), 0, command);
  HeapFree(GetProcessHeap(), 0, original);
  if (!ok)
    return 1;
  CloseHandle(process.hThread);
  DWORD status = 0;
  if (!_wcsnicmp(args, L"/desktop", 8)) {
    WaitForSingleObject(process.hProcess, INFINITE);
    GetExitCodeProcess(process.hProcess, &status);
  }
  CloseHandle(process.hProcess);
  return (int)status;
}

static int enqueue(const char *native) {
  WCHAR *request = environment_path(L"WECOM_THUNAR_QUEUE");
  if (!request)
    return 1;
  /* 文件由宿主桥接器以 0600 建立；不存在则回退，不在这里创建。 */
  HANDLE file =
      CreateFileW(request, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                  NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
  HeapFree(GetProcessHeap(), 0, request);
  if (file == INVALID_HANDLE_VALUE)
    return 1;
  LARGE_INTEGER size;
  size_t length = strlen(native);
  if (!length || length > 32768 || !GetFileSizeEx(file, &size) ||
      size.QuadPart > 8 * 1024 * 1024) {
    CloseHandle(file);
    return 1;
  }
  DWORD total = (DWORD)length + sizeof(uint32_t);
  BYTE *record = HeapAlloc(GetProcessHeap(), 0, total);
  if (!record) {
    CloseHandle(file);
    return 1;
  }
  uint32_t header = (uint32_t)length;
  memcpy(record, &header, sizeof(header));
  memcpy(record + sizeof(header), native, length);
  DWORD written = 0;
  /* 单次追加完整记录，避免同一请求被拆为多个写入。 */
  BOOL ok = WriteFile(file, record, total, &written, NULL);
  CloseHandle(file);
  HeapFree(GetProcessHeap(), 0, record);
  return ok && written == total ? 0 : 1;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, WCHAR *cmd,
                    int show) {
  (void)instance;
  (void)previous;
  (void)show;
  WCHAR path[32768];
  WCHAR *p = cmd;
  while (iswspace(*p))
    ++p;
  BOOL select = !_wcsnicmp(p, L"/select,", 8);
  if (select) {
    p += 8;
    while (iswspace(*p))
      ++p;
  }
  size_t length;
  if (*p == L'"') {
    ++p;
    length = wcscspn(p, L"\"");
    if (p[length] != L'"')
      return fallback(cmd);
    const WCHAR *tail = p + length + 1;
    while (iswspace(*tail))
      ++tail;
    if (*tail)
      return fallback(cmd);
  } else {
    length = wcslen(p);
    while (length && iswspace(p[length - 1]))
      --length;
  }
  if (length < 3 || length >= 32768)
    return fallback(cmd);
  wmemcpy(path, p, length);
  path[length] = 0;
  if (!iswalpha(path[0]) || path[1] != L':' || path[2] != L'\\')
    return fallback(cmd);
  DWORD attr = GetFileAttributesW(path);
  if (select && attr != INVALID_FILE_ATTRIBUTES &&
      !(attr & FILE_ATTRIBUTE_DIRECTORY)) {
    WCHAR *last = wcsrchr(path, L'\\');
    if (last == path + 2)
      last[1] = 0;
    else if (last)
      *last = 0;
    attr = GetFileAttributesW(path);
  }
  if (attr == INVALID_FILE_ATTRIBUTES || !(attr & FILE_ATTRIBUTE_DIRECTORY))
    return fallback(cmd);
  unix_name_fn convert = (unix_name_fn)(void *)GetProcAddress(
      GetModuleHandleW(L"kernel32.dll"), "wine_get_unix_file_name");
  char *native = convert ? convert(path) : NULL;
  if (!native)
    return fallback(cmd);
  int status = enqueue(native);
  HeapFree(GetProcessHeap(), 0, native);
  return status ? fallback(cmd) : 0;
}
