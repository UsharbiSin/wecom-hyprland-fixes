#include <xcb-imdkit/imdkit.h>
#include <xcb/xcb_aux.h>
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

int main(void)
{
    const char *display_name = getenv("DISPLAY");
    if (!display_name || strcmp(display_name, ":177")) {
        fputs("Refuse DISPLAY other than the runner's reserved :177\n", stderr);
        return 2;
    }
    int screen_number;
    xcb_connection_t *connection = xcb_connect(NULL, &screen_number);
    xcb_screen_t *screen = xcb_aux_get_screen(connection, screen_number);
    assert(screen);
    xcb_window_t window = xcb_generate_id(connection);
    xcb_create_window(connection, XCB_COPY_FROM_PARENT, window, screen->root,
                      0, 0, 1, 1, 0, XCB_WINDOW_CLASS_INPUT_OUTPUT,
                      screen->root_visual, 0, NULL);
    uint32_t styles[] = {XCB_IM_PreeditNothing | XCB_IM_StatusNothing};
    char *encodings[] = {"COMPOUND_TEXT"};
    xcb_im_styles_t input_styles = {1, styles};
    xcb_im_encodings_t encoding_list = {1, encodings};
    xcb_im_t *im = xcb_im_create(connection, screen_number, window,
                               "wecom_reconnect_test", XCB_IM_ALL_LOCALES,
                               &input_styles, NULL, NULL, &encoding_list,
                               0, NULL, NULL);
    assert(im && xcb_im_open_im(im));
    xcb_flush(connection);
    xcb_generic_event_t *event;
    while ((event = xcb_wait_for_event(connection))) {
        xcb_im_filter_event(im, event);
        free(event);
    }
    xcb_im_destroy(im);
    xcb_disconnect(connection);
    return 0;
}
