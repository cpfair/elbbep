#include "pebble.h"
#include "patch.auto.h"
#include "platform.h"
#include "rtl.h"
#include "rtl_ranges.h"
#include "text_shaper.h"
#include "utf8.h"

void *memset(void* dest, int val, size_t size) {
    while (size--) {
        *((char*)dest++) = (char)val;
    }
    return NULL; // Whatever.
}

GSize graphics_text_layout_get_content_size_patch(char* text, GFont const font, const GRect box, const GTextOverflowMode overflow_mode, GTextAlignment alignment) {
    bool did_shape_text = false;
    if (text >= (char*)SRAM_BASE && *text) {
        did_shape_text = shape_text(text);
    }
    GSize res = PASSTHRU(graphics_text_layout_get_content_size_patch, text, font, box, overflow_mode, alignment);
    if (did_shape_text) {
        unshape_text(text);
    }
    return res;
}

GSize graphics_text_layout_get_content_size_with_attributes_patch(char* text, GFont const font, const GRect box, const GTextOverflowMode overflow_mode, GTextAlignment alignment, GTextAttributes* text_attributes) {
    bool did_shape_text = false;
    if (text >= (char*)SRAM_BASE && *text) {
        did_shape_text = shape_text(text);
    }
    GSize res = PASSTHRU(graphics_text_layout_get_content_size_with_attributes_patch, text, font, box, overflow_mode, alignment, text_attributes);
    if (did_shape_text) {
        unshape_text(text);
    }
    return res;
}

void graphics_draw_text_patch(GContext* ctx, char* text, GFont const font, const GRect box, const GTextOverflowMode overflow_mode, GTextAlignment alignment, GTextAttributes* text_attributes) {
    bool did_shape_text = false;
    if (text >= (char*)SRAM_BASE && *text) {
        did_shape_text = shape_text(text);

        // Mangle alignment to be kind of maybe proper.
        // If the first opinionated character in the string is RTL, the alignment will be swapped.
        if (alignment == GTextAlignmentLeft) {
            char* ptr = text;
            while (*ptr) {
                uint16_t codept = read_utf8(&ptr);
                if (is_neutral(codept) || is_weak_ltr(codept)) {
                    continue;
                } else if (is_rtl(codept)) {
                    alignment = GTextAlignmentRight;
                    break;
                } else {
                    break;
                }
            }
        }
    }
    PASSTHRU(graphics_draw_text_patch, ctx, text, font, box, overflow_mode, alignment, text_attributes);
    if (did_shape_text) {
        unshape_text(text);
    }
}

typedef void (*RenderHandler)(void*, void*, void*);
void render_wrap(void* gcontext, char** layout, bool more_text, RenderHandler handler, char* callsite_sp) {
    // First, apply RTL transforms.
    char* line_start, *line_end, *line_end_1, *line_end_2;
    line_start = *layout;
    bool did_rtl_transform = false;
    if (line_start >= (char*)SRAM_BASE && *line_start) {
        line_end_1 = *(char**)(callsite_sp + LINEEND_SP_OFF);
        line_end_2 = *(char**)(callsite_sp + LINEEND_SP_OFF + 4);
        line_end = more_text ? line_end_1 : line_end_2;
        while (line_end > line_start && *(line_end - 1) == ' ') line_end--;
        while (*line_start == ' ') line_start++;
        did_rtl_transform = rtl_apply(line_start, line_end);
    }

    // Call through to actual render handler.
    void* mystery_argument = *(void**)(callsite_sp + RENDERHDLR_ARG3_SP_OFF);
    handler(gcontext, layout, mystery_argument);

    // If we applied the RTL operations once, do them again to undo the changes.
    if (did_rtl_transform) {
        rtl_apply(line_start, line_end);
    }
}
