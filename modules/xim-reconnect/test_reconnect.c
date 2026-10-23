#define _POSIX_C_SOURCE 200809L
#include <X11/Xlib.h>
#include <assert.h>
#include <locale.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

struct test_callback { int calls; int unregister_self; };
static void ready(Display *display, XPointer user, XPointer unused)
{
    struct test_callback *state = (void *)user;
    (void)unused;
    ++state->calls;
    if (state->unregister_self)
        assert(XUnregisterIMInstantiateCallback(display, NULL, NULL, NULL,
                                               ready, user));
}

static void pump(Display *display, double seconds)
{
    struct timespec started, now, pause = {0, 10000000};
    clock_gettime(CLOCK_MONOTONIC, &started);
    do {
        while (XPending(display)) {
            XEvent event;
            XNextEvent(display, &event);
            XFilterEvent(&event, None);
        }
        nanosleep(&pause, NULL);
        clock_gettime(CLOCK_MONOTONIC, &now);
    } while ((now.tv_sec - started.tv_sec) +
             (now.tv_nsec - started.tv_nsec) / 1e9 < seconds);
}

int main(int argc, char **argv)
{
    assert(argc == 3);
    const char *mode = argv[1];
    const char *display_name = getenv("DISPLAY");
    if (!display_name || strcmp(display_name, ":177")) {
        fputs("Refuse DISPLAY other than the runner's reserved :177\n", stderr);
        return 2;
    }
    assert(setlocale(LC_ALL, "C.UTF-8"));
    assert(XSetLocaleModifiers("@im=wecom_reconnect_test"));
    Display *a = XOpenDisplay(NULL), *b = XOpenDisplay(NULL);
    assert(a && b);
    struct test_callback first = {0}, second = {0};
    int same = !strcmp(mode, "same-display-remove-first") ||
               !strcmp(mode, "deferred-unregister");
    Display *target = same ? a : b;
    if (!strcmp(mode, "deferred-unregister")) first.unregister_self = 1;
    assert(XRegisterIMInstantiateCallback(a, NULL, NULL, NULL,
                                         ready, (XPointer)&first));
    assert(XRegisterIMInstantiateCallback(target, NULL, NULL, NULL,
                                         ready, (XPointer)&second));
    if (strstr(mode, "remove-first"))
        assert(XUnregisterIMInstantiateCallback(a, NULL, NULL, NULL,
                                               ready, (XPointer)&first));
    assert(!first.calls && !second.calls);
    pid_t child = fork();
    assert(child >= 0);
    if (!child) { execl(argv[2], argv[2], NULL); _exit(127); }
    /* Only the target connection processes events.  A callback for it must
       not depend on the first connection being pumped by another thread. */
    int only_first = !strcmp(mode, "first-display-only");
    pump(only_first ? a : target, 1.2);
    int ok = second.calls == (only_first ? 0 : 1) &&
             first.calls ==
                 (only_first || !strcmp(mode, "deferred-unregister") ? 1 : 0);
    printf("%s first=%d second=%d %s\n", mode, first.calls,
           second.calls, ok ? "PASS" : "FAIL");
    fflush(stdout);
    kill(child, SIGTERM);
    waitpid(child, NULL, 0);
    XCloseDisplay(b);
    XCloseDisplay(a);
    return ok ? 0 : 1;
}
