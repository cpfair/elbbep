#include "rtl.h"
#include "utf8.h"
#include "pebble.h"
#include "platform.h"
#define SWAP(a, b) {swap = *(a); *(a) = *(b); *(b) = swap;}

static void reverse_span(char* start, char* end) {
    char swap;
    // Start by doing a naive byte-wise reversal.
    char* start_iter = start;
    char* end_iter = end;
    for (--end_iter; start_iter < end_iter; start_iter++, end_iter--) {
        SWAP(start_iter, end_iter);
    }

    // Then run through again, rotating the UTF8 runes back.
    end_iter = end;
    while (--end_iter >= start) {
        switch ((*end_iter) >> 4) {
        case 0xF:
            // 4 bytes.
            SWAP(end_iter - 3, end_iter);
            SWAP(end_iter - 2, end_iter - 1);
            end_iter -= 3;
            break;
        case 0xE:
            // 3 bytes
            SWAP(end_iter - 2, end_iter);
            end_iter -= 2;
            break;
        case 0xC: // 0b1100
        case 0xD: // 0b1101
            SWAP(end_iter - 1, end_iter);
            end_iter--;
            break;
        }
    }
}

void rtl_apply(char* line_start, char* line_end) {
    reverse_span(line_start, line_end);
}
