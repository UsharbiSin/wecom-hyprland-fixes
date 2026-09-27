// 企业微信专用桌面采集桥；不写画面文件、不采集音频、不连接网络。
// 非桌面的 X11 操作直接转交原函数。
#include "policy.h"
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <libportal/portal.h>
#include <mutex>
#include <pipewire/pipewire.h>
#include <spa/buffer/meta.h>
#include <spa/param/video/format-utils.h>
#include <strings.h>
#include <thread>
#include <unistd.h>
#include <vector>

static long long now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}
struct Capture {
    std::atomic<long long> requested{0};
    std::atomic<bool> stream_failed{false};
    std::once_flag worker;
    std::mutex pixels_mutex;
    std::vector<unsigned char> pixels;
    unsigned width = 0, height = 0;
    XdpPortal *portal = nullptr;
    XdpSession *session = nullptr;
    GCancellable *cancel = nullptr;
    gulong closed_handler = 0;
    bool pending = false, blocked = false, first_frame = false;
    long long opened = 0;
    unsigned generation = 0;
    pw_thread_loop *loop = nullptr;
    pw_context *context = nullptr;
    pw_core *core = nullptr;
    pw_stream *stream = nullptr;
    spa_hook listener{};
    spa_video_info_raw format{};
};
static Capture &capture() {
    static auto *c = new Capture;
    return *c;
}
struct Request {
    Capture *c;
    unsigned generation;
};

static void stop(Capture &c) {
    ++c.generation;
    if (c.cancel) {
        g_cancellable_cancel(c.cancel);
        g_clear_object(&c.cancel);
    }
    if (c.loop)
        pw_thread_loop_stop(c.loop);
    if (c.stream)
        pw_stream_destroy(c.stream);
    if (c.core)
        pw_core_disconnect(c.core);
    if (c.context)
        pw_context_destroy(c.context);
    if (c.loop)
        pw_thread_loop_destroy(c.loop);
    c.stream = nullptr;
    c.core = nullptr;
    c.context = nullptr;
    c.loop = nullptr;
    if (c.session) {
        if (c.closed_handler)
            g_signal_handler_disconnect(c.session, c.closed_handler);
        c.closed_handler = 0;
        xdp_session_close(c.session);
        g_clear_object(&c.session);
    }
    c.pending = false;
    c.first_frame = false;
    std::lock_guard<std::mutex> lock(c.pixels_mutex);
    c.pixels.clear();
    c.width = c.height = 0;
    c.stream_failed = false;
}
static void process(void *data) {
    auto &c = *static_cast<Capture *>(data);
    pw_buffer *b = pw_stream_dequeue_buffer(c.stream);
    if (!b)
        return;
    pw_buffer *next;
    while ((next = pw_stream_dequeue_buffer(c.stream))) {
        pw_stream_queue_buffer(c.stream, b);
        b = next;
    }
    const auto &f = c.format;
    if (b->buffer->n_datas && f.size.width && f.size.height) {
        const auto &d = b->buffer->datas[0];
        const unsigned w = f.size.width, h = f.size.height;
        if (d.data && d.chunk && d.chunk->size &&
            !(d.chunk->flags & SPA_CHUNK_FLAG_CORRUPTED) && w <= 8192 &&
            h <= 8192 && uint64_t(w) * h <= 33554432 && d.chunk->stride > 0 &&
            unsigned(d.chunk->stride) >= w * 4 &&
            d.chunk->offset <= d.maxsize &&
            uint64_t(h - 1) * d.chunk->stride + w * 4 <= d.chunk->size &&
            uint64_t(d.chunk->offset) + uint64_t(h - 1) * d.chunk->stride +
                    w * 4 <=
                d.maxsize) {
            const bool rgb = f.format == SPA_VIDEO_FORMAT_RGBx ||
                             f.format == SPA_VIDEO_FORMAT_RGBA;
            const bool bgr = f.format == SPA_VIDEO_FORMAT_BGRx ||
                             f.format == SPA_VIDEO_FORMAT_BGRA;
            if (rgb || bgr) {
                std::lock_guard<std::mutex> lock(c.pixels_mutex);
                c.pixels.resize(size_t(w) * h * 4);
                auto *source = static_cast<const unsigned char *>(d.data) +
                               d.chunk->offset;
                for (unsigned y = 0; y < h; ++y) {
                    const auto *s = source + size_t(y) * d.chunk->stride;
                    auto *p = c.pixels.data() + size_t(y) * w * 4;
                    for (unsigned x = 0; x < w; ++x, s += 4, p += 4) {
                        p[0] = s[rgb ? 2 : 0];
                        p[1] = s[1];
                        p[2] = s[rgb ? 0 : 2];
                        p[3] = 0;
                    }
                }
                c.width = w;
                c.height = h;
                if (!c.first_frame) {
                    c.first_frame = true;
                    fprintf(stderr, "[wecom-screen] 首帧 %ux%u\n", w, h);
                }
            }
        }
    }
    pw_stream_queue_buffer(c.stream, b);
}
static void parameter(void *data, uint32_t id, const spa_pod *param) {
    auto &c = *static_cast<Capture *>(data);
    if (id != SPA_PARAM_Format || !param)
        return;
    spa_video_info_raw format{};
    if (spa_format_video_raw_parse(param, &format) < 0)
        return;
    c.format = format;
    unsigned char storage[256];
    spa_pod_builder builder = SPA_POD_BUILDER_INIT(storage, sizeof(storage));
    const spa_pod *p = static_cast<spa_pod *>(spa_pod_builder_add_object(
        &builder, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers,
        SPA_PARAM_BUFFERS_dataType,
        SPA_POD_CHOICE_FLAGS_Int((1 << SPA_DATA_MemPtr) |
                                 (1 << SPA_DATA_MemFd))));
    pw_stream_update_params(c.stream, &p, 1);
}
static void state(void *data, pw_stream_state, pw_stream_state current,
                  const char *error) {
    if (current == PW_STREAM_STATE_ERROR) {
        // 回调在 PipeWire 线程；只置标记，由 GLib 线程负责停止和回收。
        // 在此处直接 stop 会等待当前线程自身，造成死锁。
        static_cast<Capture *>(data)->stream_failed = true;
        fprintf(stderr, "[wecom-screen] PipeWire 错误：%s\n",
                error ? error : "未知");
    }
}
static const pw_stream_events events = [] {
    pw_stream_events e{};
    e.version = PW_VERSION_STREAM_EVENTS;
    e.state_changed = state;
    e.param_changed = parameter;
    e.process = process;
    return e;
}();
static bool connect_stream(Capture &c, int fd, unsigned node) {
    c.loop = pw_thread_loop_new("wecom-screen", nullptr);
    if (!c.loop) {
        close(fd);
        return false;
    }
    c.context = pw_context_new(pw_thread_loop_get_loop(c.loop), nullptr, 0);
    if (!c.context) {
        close(fd);
        return false;
    }
    c.core = pw_context_connect_fd(c.context, fd, nullptr, 0);
    if (!c.core)
        return false;
    c.stream = pw_stream_new(
        c.core, "企业微信屏幕共享",
        pw_properties_new(PW_KEY_MEDIA_TYPE, "Video", PW_KEY_MEDIA_CATEGORY,
                          "Capture", PW_KEY_MEDIA_ROLE, "Screen", nullptr));
    if (!c.stream)
        return false;
    pw_stream_add_listener(c.stream, &c.listener, &events, &c);
    unsigned char storage[1024];
    spa_pod_builder builder = SPA_POD_BUILDER_INIT(storage, sizeof(storage));
    spa_rectangle size = SPA_RECTANGLE(1920, 1080), min = SPA_RECTANGLE(1, 1),
                  max = SPA_RECTANGLE(8192, 8192);
    spa_fraction rate = SPA_FRACTION(20, 1), low = SPA_FRACTION(0, 1),
                 high = SPA_FRACTION(30, 1);
    const spa_pod *p = static_cast<spa_pod *>(spa_pod_builder_add_object(
        &builder, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        SPA_FORMAT_VIDEO_format,
        SPA_POD_CHOICE_ENUM_Id(4, SPA_VIDEO_FORMAT_BGRx, SPA_VIDEO_FORMAT_BGRA,
                               SPA_VIDEO_FORMAT_RGBx, SPA_VIDEO_FORMAT_RGBA),
        SPA_FORMAT_VIDEO_size,
        SPA_POD_CHOICE_RANGE_Rectangle(&size, &min, &max),
        SPA_FORMAT_VIDEO_framerate,
        SPA_POD_CHOICE_RANGE_Fraction(&rate, &low, &high)));
    if (pw_stream_connect(c.stream, PW_DIRECTION_INPUT, node,
                          pw_stream_flags(PW_STREAM_FLAG_AUTOCONNECT |
                                          PW_STREAM_FLAG_MAP_BUFFERS),
                          &p, 1) < 0)
        return false;
    return pw_thread_loop_start(c.loop) >= 0;
}
static void closed(XdpSession *, void *data) {
    auto &c = *static_cast<Capture *>(data);
    c.blocked = true;
    stop(c);
}
static void started(GObject *object, GAsyncResult *result, void *data) {
    auto *request = static_cast<Request *>(data);
    auto &c = *request->c;
    const bool current = request->generation == c.generation;
    delete request;
    GError *error = nullptr;
    bool ok = xdp_session_start_finish(XDP_SESSION(object), result, &error);
    if (error) {
        fprintf(stderr, "[wecom-screen] Portal 启动：%s\n", error->message);
        g_error_free(error);
    }
    if (!current)
        return;
    c.pending = false;
    if (!ok) {
        c.blocked = true;
        stop(c);
        return;
    }
    GVariant *streams = xdp_session_get_streams(c.session);
    if (!streams || !g_variant_n_children(streams)) {
        c.blocked = true;
        stop(c);
        return;
    }
    GVariant *first = g_variant_get_child_value(streams, 0),
             *properties = nullptr;
    guint32 node = 0;
    g_variant_get(first, "(u@a{sv})", &node, &properties);
    g_variant_unref(properties);
    g_variant_unref(first);
    int fd = xdp_session_open_pipewire_remote(c.session);
    if (fd < 0 || !connect_stream(c, fd, node)) {
        c.blocked = true;
        stop(c);
        return;
    }
    fprintf(stderr, "[wecom-screen] Portal 已启动\n");
}
static void created(GObject *object, GAsyncResult *result, void *data) {
    auto *request = static_cast<Request *>(data);
    auto &c = *request->c;
    const bool current = request->generation == c.generation;
    delete request;
    GError *error = nullptr;
    XdpSession *session = xdp_portal_create_screencast_session_finish(
        XDP_PORTAL(object), result, &error);
    if (error) {
        fprintf(stderr, "[wecom-screen] Portal 创建：%s\n", error->message);
        g_error_free(error);
    }
    if (!current) {
        if (session) {
            xdp_session_close(session);
            g_object_unref(session);
        }
        return;
    }
    if (!session) {
        c.blocked = true;
        stop(c);
        return;
    }
    c.session = session;
    c.closed_handler =
        g_signal_connect(session, "closed", G_CALLBACK(closed), &c);
    xdp_session_start(session, nullptr, c.cancel, started,
                      new Request{&c, c.generation});
}
static gboolean tick(void *data) {
    auto &c = *static_cast<Capture *>(data);
    if (c.stream_failed.load()) {
        c.blocked = true;
        stop(c);
        fprintf(stderr, "[wecom-screen] 流错误后已停止并清空画面\n");
    }
    const auto last = c.requested.load();
    if (!last)
        return G_SOURCE_CONTINUE;
    if (c.pending && now_ms() - c.opened > 60000) {
        c.blocked = true;
        stop(c);
        fprintf(stderr, "[wecom-screen] 共享源选择超时\n");
    }
    if (now_ms() - last > 5000) {
        if (!c.pending) {
            if (c.session || c.loop) {
                stop(c);
                fprintf(stderr, "[wecom-screen] 采集空闲后已停止\n");
            }
            c.blocked = false;
        }
    } else if (!c.session && !c.pending && !c.blocked) {
        c.pending = true;
        c.opened = now_ms();
        c.cancel = g_cancellable_new();
        xdp_portal_create_screencast_session(
            c.portal, XDP_OUTPUT_MONITOR, XDP_SCREENCAST_FLAG_NONE,
            XDP_CURSOR_MODE_EMBEDDED, XDP_PERSIST_MODE_NONE, nullptr, c.cancel,
            created, new Request{&c, c.generation});
    }
    return G_SOURCE_CONTINUE;
}
static void request_capture() {
    auto &c = capture();
    c.requested = now_ms();
    std::call_once(c.worker, [&c] {
        std::thread([&c] {
            pw_init(nullptr, nullptr);
            GMainContext *context = g_main_context_new();
            g_main_context_push_thread_default(context);
            c.portal = xdp_portal_new();
            GSource *timer = g_timeout_source_new(250);
            g_source_set_callback(timer, tick, &c, nullptr);
            g_source_attach(timer, context);
            g_source_unref(timer);
            GMainLoop *loop = g_main_loop_new(context, FALSE);
            g_main_loop_run(loop);
        }).detach();
    });
}
static bool eligible() {
    const char *enabled = getenv("WECOM_SCREENSHARE");
    if (!enabled || strcmp(enabled, "1"))
        return false;
    // Qt 可能把主线程改名为 UIThread，因此从进程参数识别程序。
    char name[4096] = {0};
    FILE *file = fopen("/proc/self/cmdline", "rb");
    if (!file)
        return false;
    size_t length = fread(name, 1, sizeof(name) - 1, file);
    fclose(file);
    if (!length)
        return false;
    const char *base = name;
    for (const char *p = name; *p; ++p)
        if (*p == '/' || *p == '\\')
            base = p + 1;
    if (!strcasecmp(base, "wwmapp.exe"))
        return true;
    // 内置会议宿主可能把 argv[0] 改为 SDK 模块。
    // 此例外仅适用于企业微信 WeMeet 目录中的 wemeet.dll。
    if (strncmp(name, "--module=", 9) || strcasecmp(base, "wemeet.dll"))
        return false;
    for (char *p = name; *p; ++p) {
        if (*p == '\\')
            *p = '/';
        else if (*p >= 'A' && *p <= 'Z')
            *p += 32;
    }
    return strstr(name, "/wxwork/") && strstr(name, "/wemeet/");
}
static int root_screen(Display *d, Drawable drawable) {
    for (int i = 0; i < ScreenCount(d); ++i)
        if (drawable == RootWindow(d, i))
            return i;
    return -1;
}
static bool supported_visual(Display *d, int screen) {
    const auto *visual = DefaultVisual(d, screen);
    return screencast_policy::supported_visual(
        DefaultDepth(d, screen), visual->red_mask, visual->green_mask,
        visual->blue_mask);
}
static XImage *frame(Display *d, int screen, int x, int y, unsigned w,
                     unsigned h) {
    if (!w || !h || w > 8192 || h > 8192 || uint64_t(w) * h > 33554432)
        return nullptr;
    request_capture();
    XImage *image =
        XCreateImage(d, DefaultVisual(d, screen), DefaultDepth(d, screen),
                     ZPixmap, 0, nullptr, w, h, 32, 0);
    if (!image)
        return nullptr;
    image->data = static_cast<char *>(calloc(h, image->bytes_per_line));
    if (!image->data) {
        XDestroyImage(image);
        return nullptr;
    }
    auto &c = capture();
    std::lock_guard<std::mutex> lock(c.pixels_mutex);
    if (c.stream_failed.load() || !c.width || !c.height)
        return image;
    const int root_w = DisplayWidth(d, screen),
              root_h = DisplayHeight(d, screen);
    const bool direct = image->bits_per_pixel == 32 &&
                        image->byte_order == LSBFirst &&
                        image->red_mask == 0xff0000 &&
                        image->green_mask == 0xff00 && image->blue_mask == 0xff;
    if (direct && int(c.width) == root_w && int(c.height) == root_h && x >= 0 &&
        y >= 0 && uint64_t(x) + w <= c.width && uint64_t(y) + h <= c.height) {
        for (unsigned row = 0; row < h; ++row)
            memcpy(image->data + size_t(row) * image->bytes_per_line,
                   c.pixels.data() + (size_t(y + row) * c.width + x) * 4,
                   size_t(w) * 4);
    } else
        for (unsigned row = 0; row < h; ++row)
            for (unsigned col = 0; col < w; ++col) {
                long long sx =
                    (static_cast<long long>(x) + col) * c.width / root_w;
                long long sy =
                    (static_cast<long long>(y) + row) * c.height / root_h;
                if (sx < 0 || sy < 0 || sx >= c.width || sy >= c.height)
                    continue;
                const auto *p = c.pixels.data() + (sy * c.width + sx) * 4;
                unsigned long value = (static_cast<unsigned long>(p[2]) << 16) |
                                      (static_cast<unsigned long>(p[1]) << 8) |
                                      p[0];
                if (direct)
                    reinterpret_cast<uint32_t *>(
                        image->data +
                        size_t(row) * image->bytes_per_line)[col] = value;
                else
                    XPutPixel(image, col, row, value);
            }
    return image;
}
extern "C" XImage *XGetImage(Display *d, Drawable drawable, int x, int y,
                             unsigned w, unsigned h, unsigned long mask,
                             int format) {
    using Fn = XImage *(*)(Display *, Drawable, int, int, unsigned, unsigned,
                           unsigned long, int);
    static Fn original = reinterpret_cast<Fn>(dlsym(RTLD_NEXT, "XGetImage"));
    int screen;
    if (format == ZPixmap && eligible() &&
        (screen = root_screen(d, drawable)) >= 0 &&
        supported_visual(d, screen) &&
        screencast_policy::full_plane_mask(DefaultDepth(d, screen), mask))
        return frame(d, screen, x, y, w, h);
    return original(d, drawable, x, y, w, h, mask, format);
}
extern "C" int XCopyArea(Display *d, Drawable src, Drawable dst, GC gc, int x,
                         int y, unsigned w, unsigned h, int dx, int dy) {
    using Fn = int (*)(Display *, Drawable, Drawable, GC, int, int, unsigned,
                       unsigned, int, int);
    static Fn original = reinterpret_cast<Fn>(dlsym(RTLD_NEXT, "XCopyArea"));
    int screen;
    XGCValues values{};
    if (eligible() && (screen = root_screen(d, src)) >= 0 &&
        supported_visual(d, screen) &&
        XGetGCValues(d, gc, GCPlaneMask, &values) &&
        screencast_policy::full_plane_mask(DefaultDepth(d, screen),
                                           values.plane_mask)) {
        XImage *image = frame(d, screen, x, y, w, h);
        if (!image)
            return 0;
        int result = XPutImage(d, dst, gc, image, 0, 0, dx, dy, w, h);
        XDestroyImage(image);
        return result;
    }
    return original(d, src, dst, gc, x, y, w, h, dx, dy);
}
