#define WIN32_LEAN_AND_MEAN
#include <stdio.h>
#include <string.h>
#include <windows.h>

static HANDLE watched_process;
static ULONGLONG start_time;

/* 仅匹配装饰层，不匹配会议功能工具栏。 */
static const char *decoration(HWND hwnd) {
  char cls[96], path[MAX_PATH], title[96];
  DWORD pid, size = sizeof(path);
  if (!GetClassNameA(hwnd, cls, sizeof(cls)))
    return NULL;
  BOOL shadow = !strcmp(cls, "PerryShadowWnd");
  BOOL tracker = !strcmp(cls, "Qt5158QWindowIcon") &&
                 GetWindowTextA(hwnd, title, sizeof(title)) &&
                 !strcmp(title, "screen_share_tracker");
  if (!shadow && !tracker)
    return NULL;
  GetWindowThreadProcessId(hwnd, &pid);
  if (shadow) {
    HWND owner = GetWindow(hwnd, GW_OWNER);
    DWORD owner_pid = 0;
    if (!owner)
      return NULL;
    GetWindowThreadProcessId(owner, &owner_pid);
    if (owner_pid != pid)
      return NULL;
  } else {
    /* 已观察到的跟踪框是分层、鼠标穿透的工具窗口。 */
    DWORD exstyle = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
    DWORD required = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW;
    if ((exstyle & required) != required)
      return NULL;
  }
  HANDLE process =
      OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, FALSE, pid);
  if (!process)
    return NULL;
  BOOL ok = QueryFullProcessImageNameA(process, 0, path, &size);
  if (!ok) {
    CloseHandle(process);
    return NULL;
  }
  char *base = strrchr(path, '\\');
  ok = base && !_stricmp(base + 1, shadow ? "WXWork.exe" : "wwmapp.exe");
  if (ok && tracker) {
    CharLowerBuffA(path, (DWORD)strlen(path));
    ok = strstr(path, "\\wxwork\\") && strstr(path, "\\wemeet\\");
  }
  /* 跟随企业微信进程寿命，以便后续会议继续生效。 */
  if (ok && shadow && !watched_process)
    watched_process = process;
  else
    CloseHandle(process);
  return ok ? (shadow ? "PerryShadowWnd" : "screen_share_tracker") : NULL;
}

static BOOL CALLBACK suppress(HWND hwnd, LPARAM unused) {
  (void)unused;
  const char *kind = decoration(hwnd);
  if (kind && IsWindowVisible(hwnd)) {
    if (ShowWindowAsync(hwnd, SW_HIDE)) {
      printf("已隐藏装饰层 %s hwnd=%p\n", kind, hwnd);
      fflush(stdout);
    }
  }
  return TRUE;
}

static void CALLBACK event(HWINEVENTHOOK hook, DWORD kind, HWND hwnd,
                           LONG object, LONG child, DWORD thread, DWORD time) {
  (void)hook;
  (void)kind;
  (void)thread;
  (void)time;
  if (hwnd && object == OBJID_WINDOW && child == CHILDID_SELF)
    suppress(hwnd, 0);
}

static void CALLBACK check_lifetime(HWND hwnd, UINT msg, UINT_PTR id,
                                    DWORD time) {
  (void)hwnd;
  (void)msg;
  (void)id;
  (void)time;
  if ((watched_process &&
       WaitForSingleObject(watched_process, 0) == WAIT_OBJECT_0) ||
      (!watched_process && GetTickCount64() - start_time > 60000))
    PostQuitMessage(0);
}

int main(int argc, char **argv) {
  if (argc > 1 && !strcmp(argv[1], "--once"))
    return EnumWindows(suppress, 0) ? 0 : 1;
  HANDLE mutex = CreateMutexW(NULL, TRUE, L"Local\\WeComHyprlandWindowGuardV2");
  if (!mutex || GetLastError() == ERROR_ALREADY_EXISTS)
    return 0;
  HWINEVENTHOOK hook =
      SetWinEventHook(EVENT_OBJECT_SHOW, EVENT_OBJECT_SHOW, NULL, event, 0, 0,
                      WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS);
  if (!hook)
    return 2;
  start_time = GetTickCount64();
  EnumWindows(suppress, 0);
  UINT_PTR timer = SetTimer(NULL, 0, 1000, check_lifetime);
  if (!timer) {
    UnhookWinEvent(hook);
    CloseHandle(mutex);
    return 3;
  }
  MSG message;
  while (GetMessageW(&message, NULL, 0, 0) > 0) {
    TranslateMessage(&message);
    DispatchMessageW(&message);
  }
  UnhookWinEvent(hook);
  KillTimer(NULL, timer);
  if (watched_process)
    CloseHandle(watched_process);
  CloseHandle(mutex);
  return 0;
}
