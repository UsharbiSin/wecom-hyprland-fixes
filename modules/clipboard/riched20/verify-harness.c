/* i386 独立测试：用模拟 COM 执行 DLL，不访问 Wine 或界面。 */
typedef unsigned int U;
typedef unsigned short W;
typedef int H;
#define STDCALL __attribute__((stdcall))
typedef int __attribute__((regparm(3))) (*Paste)(void *, U, void *, int);
static unsigned char editor[0x600];
static void *data_vtable[6], *callback_vtable[13];
static void **data_object = data_vtable, **callback_object = callback_vtable;
static U queries, releases, callbacks, gets, bad, offered;
static U override_cf, do_override;
static U expected_cf, expected_really;
static H callback_hr;
static H clipboard_hr;

static H STDCALL get_clipboard(void **out) {
    if (clipboard_hr)
        return clipboard_hr;
    *out = &data_object;
    return 0;
}
static U STDCALL release(void *self) {
    if (self != &data_object)
        bad |= 1;
    releases++;
    return 1;
}
static H STDCALL query(void *self, void *format) {
    if (self != &data_object)
        bad |= 2;
    queries++;
    return *(W *)format == offered ? 0 : (H)0x80040064;
}
static H STDCALL get_data(void *self, void *format, void *medium) {
    if (self != &data_object || *(W *)format != offered)
        bad |= 4;
    (void)medium;
    gets++;
    return (H)0x80004005;
}
static H STDCALL accept(void *self, void *data, W *format, U reco, int really,
                        void *meta) {
    if (self != &callback_object || data != &data_object || reco || meta)
        bad |= 8;
    if (*format != expected_cf || (U)really != expected_really)
        bad |= 16;
    callbacks++;
    if (do_override)
        *format = (W)override_cf;
    __asm__ volatile("movl $0xdeadbeef, %%edx; movl $0xcafebabe, %%ecx" ::
                         : "edx", "ecx");
    return callback_hr;
}

struct test_case {
    U callback;
    H hr;
    U check, initial, override, use_override, available, result,
        expected_queries;
    H clipboard_result;
    U expected_gets;
};
static const struct test_case cases[] = {
    {0, 0, 1, 0, 0, 0, 13, 1, 2, 0, 0},
    {0, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0},
    {1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0},
    {1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0},
    {1, 2, 0, 0, 0, 0, 0, 1, 0, 0, 0},
    {1, (H)0x80004005, 0, 0, 0, 0, 13, 0, 0, 0, 0},
    {1, 0, 1, 0, 13, 1, 13, 1, 1, 0, 0},
    {1, 0, 1, 0, 1, 1, 13, 1, 1, 0, 0},
    {1, 0, 1, 0, 8, 1, 13, 0, 0, 0, 0},
    {1, 0, 1, 0, 0, 0, 0, 0, 3, 0, 0},
    {1, 0, 1, 1, 0, 1, 13, 1, 2, 0, 0},
    {1, 0, 1, 1, 0, 0, 13, 1, 1, 0, 0},
    {1, (H)0x80000000, 1, 0, 0, 0, 13, 0, 0, 0, 0},
    {1, 0x7fffffff, 0, 0, 0, 0, 0, 1, 0, 0, 0},
    /* OleGetClipboard failure must skip the callback and Release. */
    {0, 0, 0, 0, 0, 0, 13, 0, 0, (H)0x80004005, 0},
    {1, 1, 0, 0, 0, 0, 13, 0, 0, (H)0x80004005, 0},
    /* GetData failure in the original path must still release once. */
    {0, 0, 0, 0, 0, 0, 13, 0, 2, 0, 1},
    {1, 0, 0, 0, 13, 1, 13, 0, 1, 0, 1},
};

int test_main(void) {
    *(void **)(LOAD_BASE + 0x694dc) = get_clipboard;
    *(U *)(LOAD_BASE + 0x6502c) = 1; /* 跳过格式注册，不调用操作系统。 */
    *(W *)(LOAD_BASE + 0x3f160) = 0xc001;
    data_vtable[2] = release;
    data_vtable[3] = get_data;
    data_vtable[5] = query;
    callback_vtable[8] = accept;
    for (U index = 0; index < sizeof(cases) / sizeof(cases[0]); index++) {
        const struct test_case *test = &cases[index];
        queries = releases = callbacks = gets = bad = 0;
        callback_hr = test->hr;
        clipboard_hr = test->clipboard_result;
        offered = test->available;
        override_cf = test->override;
        do_override = test->use_override;
        expected_cf = test->initial;
        expected_really = !test->check;
        *(void **)(editor + 0x4b4) =
            test->callback ? &callback_object : (void *)0;
        int result = ((Paste)(LOAD_BASE + 0x32c30))(editor, test->initial,
                                                    (void *)0, test->check);
        if ((U)result != test->result || queries != test->expected_queries ||
            releases != !clipboard_hr ||
            callbacks != (test->callback && !clipboard_hr) ||
            gets != test->expected_gets || bad) {
            U report[] = {
                index, (U)result, queries, releases, callbacks, gets, bad,
            };
            __asm__ volatile("int $0x80" ::"a"(4), "b"(1), "c"(report),
                             "d"(sizeof(report))
                             : "memory");
            return 1;
        }
    }
    return 0;
}
