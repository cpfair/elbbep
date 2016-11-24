#include "rtl.h"
#include "pebble.h"
#include "rtl_ranges.h"
#include "utf8.h"
#define SWAP(a, b)                                                             \
  {                                                                            \
    swap = *(a);                                                               \
    *(a) = *(b);                                                               \
    *(b) = swap;                                                               \
  }

const char RTL_SWAPS[] = {'{', '}', '[', ']', '(', ')', '<', '>'};

static void reverse_span(char *start, char *end) {
  char swap;
  // Start by doing a naive byte-wise reversal.
  char *start_iter = start;
  char *end_iter = end;
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
      // 2 bytes
      SWAP(end_iter - 1, end_iter);
      end_iter--;
      break;
    // 1 byte case is not needed...
    }
  }
}

static void apply_swaps(char *ch) {
  // RTL_SWAPS is a set of pairs of chars to be exchanged if they fall in an RTL
  // block.
  char val = *ch;
  for (size_t i = 0; i < sizeof(RTL_SWAPS); ++i) {
    if (val == RTL_SWAPS[i]) {
      *ch = RTL_SWAPS[i ^ 1];
      return;
    }
  }
}

bool rtl_apply(char *line_start, char *line_end) {
  bool did_transform = false;
  // Run through the line, reversing spans of RTL characters, and any neutral
  // characters contained.
  // Based on a highly scientific trial-and-error investigation, neutral
  // characters on the boundary of RTL/LTR switches remain in-place.
  // Similar investigative techniques were used to illuminate the behaviour of
  // "weak" LTR characters, i.e. numerals.
  char *rtl_span_start = NULL, *weak_ltr_span_start = NULL;
  char *iter = line_start;
  char *next_codept_ptr = iter;
  uint16_t next_codept = read_utf8(&iter);
  char *this_codept_ptr = NULL;
  uint16_t this_codept;

  do {
    this_codept = next_codept;
    this_codept_ptr = next_codept_ptr;

    if (iter < line_end && *iter) {
      next_codept_ptr = iter;
      next_codept = read_utf8(&iter);
    } else {
      next_codept = 0;
      next_codept_ptr = NULL;
    }

    bool rtl = is_rtl(this_codept);
    bool neutral = is_neutral(this_codept);
    bool weak_ltr = is_weak_ltr(this_codept);

    // Weak LTR spans are handled by pre-reversal.
    if (weak_ltr && rtl_span_start) {
      if (!weak_ltr_span_start) {
        weak_ltr_span_start = this_codept_ptr;
      }
      neutral = true; // Don't break RTL span.
    } else if (!weak_ltr && !neutral && rtl_span_start && weak_ltr_span_start) {
      reverse_span(weak_ltr_span_start, this_codept_ptr);
      weak_ltr_span_start = NULL;
    }

    if (rtl && !rtl_span_start && next_codept) {
      rtl_span_start = this_codept_ptr;
    } else if (neutral && rtl_span_start &&
               (is_rtl(next_codept) || is_neutral(next_codept) ||
                is_weak_ltr(next_codept) || next_codept < 0x20)) {
      // Continue - include the neutral character in the span.
      apply_swaps(this_codept_ptr);
    } else if (!rtl && rtl_span_start) {
      // Finished an RTL span - reverse it.
      did_transform = true;
      reverse_span(rtl_span_start, this_codept_ptr);
      rtl_span_start = NULL;
      weak_ltr_span_start = NULL;
    }
  } while (next_codept);
  if (weak_ltr_span_start) {
    reverse_span(weak_ltr_span_start, iter);
  }
  if (rtl_span_start) {
    did_transform = true;
    reverse_span(rtl_span_start, iter);
  }
  return did_transform;
}
