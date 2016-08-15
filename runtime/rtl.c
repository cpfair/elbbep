#include "rtl.h"
#include "utf8.h"
#include "pebble.h"
#include "platform.h"

static void reverse_span(char* start, char* end) {
    // Start by doing a naive byte-wise reversal.
    char* start_iter = start;
    char* end_iter = end;
    for (--end_iter; start_iter < end_iter; start_iter++, end_iter--) {
        char swap = *start_iter;
        *start_iter = *end_iter;
        *end_iter = swap;
    }
}

void rtl_apply(char* line_start, char* line_end) {
    reverse_span(line_start, line_end);
}
