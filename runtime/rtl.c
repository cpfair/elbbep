#include "rtl.h"
#include "utf8.h"
#include "pebble.h"
#include "platform.h"

// One in 4bn strings will break this, oh well.

typedef struct LineLayoutData {
    char* line_start;
    // Sadly, no line_end it appears :(
    // Some other stuff probably?
} LineLayoutData;

static void reverse_span(char* start, char* end) {
    // Start by doing a naive byte-wise reversal.
    for (int i = 0; i < (end - start + 1) >> 1; ++i) {
        char swap = start[i];
        start[i] = *(end - i);
        *(end - i) = swap;
    }
}

void rtl_apply(TextAttr* attr, LineLayoutData* layout) {
    if (attr->state < SRAM_BASE || attr->state > SRAM_EXTENT - sizeof(RTLState) || attr->state->cookie != RTL_STATE_COOKIE_VAL) {
        // We're not rendering - the textattribute struct hasn't been mangled.
        return;
    }
    if (layout->line_start < (char*)SRAM_BASE || layout->line_start > (char*)SRAM_EXTENT) {
        // String in microflash.
        return;
    }
    // We simply run through the line, from line_start to line_end, and reverse the RTL spans.
    char* ptr = layout->line_start;
    while (*(++ptr)) {}
    reverse_span(layout->line_start, ptr - 1);
}
