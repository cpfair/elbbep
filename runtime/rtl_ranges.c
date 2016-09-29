#include "rtl_ranges.h"
#include "font_ranges.h"
#include "range.h"
#include "text_shaper_lut.h"

bool is_rtl(uint16_t cp) {
  return RANGE(cp, 0x60E,
               0x660) || // First part of Arabic block - up to numerals
         RANGE(cp, 0x66D, 0x6FF + 1) || // Balance of Arabic block
         RANGE(cp, 0x750,
               0x77F + 1) || // Arabic-Extended - not that it's supported.
         RANGE(cp, 0x590, 0x600) || // Hebrew
         ARABIC_SHAPER_RANGE(cp);   // Since the RTL routine runs after the
                                    // shaper, we need to include its fake
                                    // codepoints.
}

bool is_neutral(uint16_t cp) {
  return RANGE(cp, 0x20, 0x23) ||   // Latin punctuation - excl #$% etc.
         RANGE(cp, 0x26, 0x30) ||   // ...
         RANGE(cp, 0x3A, 0x41) ||   // ...
         RANGE(cp, 0x5B, 0x61) ||   // ...
         RANGE(cp, 0x7B, 0xA2) ||   // ...
         RANGE(cp, 0xA6, 0xA7) ||   // ...
         RANGE(cp, 0xA8, 0xB0) ||   // ...
         RANGE(cp, 0xB7, 0xBf) ||   // ...
         RANGE(cp, 0x600, 0x60E) || // Arabic punctuation & stuff.
         is_zero_width(cp); // So invisible characters don't break stuff.
}

bool is_weak_ltr(uint16_t cp) {
  return RANGE(cp, 0x30, 0x3A) || // Arabic numerals
         RANGE(cp, 0x660, 0x66D); // Indic numerals
}
