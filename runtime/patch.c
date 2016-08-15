#include "pebble.h"
#include "patch.auto.h"
#include "text_shaper.h"
#include "rtl.h"
#include "platform.h"

void *memset(void* dest, int val, size_t size) {
    while (size--) {
        *((char*)dest++) = (char)val;
    }
    return NULL; // Whatever.
}

// This pattern is really long because it's mostly identical to graphics_text_layout_get_content_size
/// PATCH LOCATE
/// TARGET
/// push .+
/// sub sp, .+
/// mov .+
/// mov .+
/// add .+
/// stm.+
/// ldr.+
/// str .+
/// ldr.+
/// str .+
/// ldr .+
void* graphics_text_layout_get_content_size_with_attributes_loc();
// GSize graphics_text_layout_get_content_size_with_attributes(const char * text, GFont const font, const GRect box, const GTextOverflowMode overflow_mode, const GTextAlignment alignment, GTextAttributes * text_attributes);

/// PATCH WRAP
/// JUMP
/// sub sp, #\d+
/// stmdb .+
/// END-PATCH
/// sub sp, #\d+
/// mov .+
/// mov .+
/// mov .+
/// str .+
void graphics_draw_text_patch(GContext* ctx, char* text, GFont const font, const GRect box, const GTextOverflowMode overflow_mode, GTextAlignment alignment, TextAttr* text_attributes) {
    char dummy_attribute_struct[0x28];
    if (!text_attributes) {
        memset((char*)&dummy_attribute_struct, 0, sizeof(dummy_attribute_struct));
        text_attributes = (TextAttr*)&dummy_attribute_struct;
    }
    RTLState state = {
        .cookie = RTL_STATE_COOKIE_VAL
    };
    void* old_state_val = text_attributes->state;
    // text_attributes->state = &state;
    bool shaped_text = false;
    if (text >= (char*)SRAM_BASE) {
        shaped_text = shape_text(text);
    }
    alignment = 13;
    graphics_text_layout_get_content_size_with_attributes_loc(text, font, box, overflow_mode, alignment, text_attributes);
    graphics_draw_text_patch__passthru(ctx, text, font, box, overflow_mode, alignment, text_attributes);
    if (shaped_text) {
        unshape_text(text);
    }
    text_attributes->state = old_state_val;
}

/// PATCH INJECT
/// JUMP
/// ldrh .+
/// ldrh .+
/// add .+
/// ldrh .+
/// add .+
void line_layout_patch(void* REGISTER_MATCH(r0) attr, void* REGISTER_MATCH(r1) layout, void* REGISTER_MATCH(r2) stuff) {
    rtl_apply(attr, layout);
}
