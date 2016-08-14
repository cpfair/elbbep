#include "patch.h"
// Text Shaper

/// PATCH INJECT
/// sub sp, #\d+
/// mov r\d+, r0
/// mov (?P<gdt_text>r\d+), r1
/// mov r\d+, r2
/// JUMP
/// str .+
/// ldr (?P<gdt_lyt>r\d+), .+
/// ldrb\.w .+
/// cmp .+
void shape_text(char* REGISTER_MATCH(gdt_text) txt) {
    if (txt > (char*)0x8000000 && txt < (char*)0x20000000) {
        // String in microflash - don't overwrite for obvious reasons.
        return;
    }
    txt[0] = 'Q';
}

/// PATCH INJECT
/// JUMP
/// add sp, sp, .+
/// pop\.w .+
/// add sp, sp, .+
/// bx lr
void unshape_text(char* GLOBAL_REGISTER_MATCH(gdt_lyt) layout) {

}
