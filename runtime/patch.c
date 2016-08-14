#include "pebble.h"
#include "patch.h"
#include "text_shaper.h"
#define SRAM_BASE 0x20000000



/// PATCH WRAP
/// JUMP
/// sub sp, #\d+
/// stmdb .+
/// sub sp, #\d+
/// mov .+
/// mov .+
/// mov .+
/// str .+
void graphics_draw_text_patch(GContext* ctx, char* text, GFont const font, const GRect box, const GTextOverflowMode overflow_mode, const GTextAlignment alignment, GTextAttributes* text_attributes) {
    bool shaped_text = false;
    if (text >= (char*)SRAM_BASE) {
        shaped_text = shape_text(text);
    }
    graphics_draw_text_patch__passthru(ctx, text, font, box, overflow_mode, alignment, text_attributes);
    if (shaped_text) {
        unshape_text(text);
    }
}

