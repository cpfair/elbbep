// clang-format off
#include "pebble.h"
// clang-format on
#include "patch.auto.h"
#include "platform.h"
#include "rtl.h"
#include "rtl_ranges.h"
#include "text_shaper.h"
#include "utf8.h"

GSize graphics_text_layout_get_content_size_with_attributes_patch(
    char *text, GFont const font, const GRect box,
    const GTextOverflowMode overflow_mode, GTextAlignment alignment,
    GTextAttributes *text_attributes);

#ifdef TEXT_UNSHAPE
GSize graphics_text_layout_get_content_size_with_attributes_patch(
    char *text, GFont const font, const GRect box,
    const GTextOverflowMode overflow_mode, GTextAlignment alignment,
    GTextAttributes *text_attributes) {
  shape_text(text);

  GSize res = PASSTHRU(graphics_text_layout_get_content_size_with_attributes_patch, text, font, box, overflow_mode, alignment, text_attributes);

  // Magic cookie used by the diagnostics app when checking if the firmware is installed...
  if (overflow_mode != 0xE5) {
    unshape_text(text);
  }
  return res;
}

#endif

GSize graphics_text_layout_get_content_size_patch(
    char *text, GFont const font, const GRect box,
    const GTextOverflowMode overflow_mode, GTextAlignment alignment) {
  return graphics_text_layout_get_content_size_with_attributes_patch(text, font,
                       box, overflow_mode, alignment, NULL);
}

GTextAlignment gdt_alignment_step(char* text, GTextAlignment alignment){
  // Mangle alignment to be kind of maybe proper.
  // If the first opinionated character in the string is RTL, the alignment
  // will be swapped.
  if (alignment == GTextAlignmentLeft) {
    char *ptr = text;
    while (*ptr) {
      uint16_t codept = read_utf8(&ptr);
      if (is_neutral(codept) || is_weak_ltr(codept)) {
        continue;
      } else if (is_rtl(codept)) {
        return GTextAlignmentRight;
      } else {
        break;
      }
    }
  }
  return alignment;
}

void render_rtl_step(char* line_start, bool more_text, char* callsite_sp) {
  char *line_end, *line_end_1, *line_end_2;
  if (line_start >= (char *)SRAM_BASE && *line_start) {
#ifdef LINEEND_INDIRECT_SP_OFF
    // 4.1 breaks the old LINEEND_SP_OFF_1 stuff.
    // But, after an hour or two of rifling through memory, this seems to be the same...
    line_end_1 = *((*(char***)(callsite_sp + LINEEND_INDIRECT_SP_OFF) + 2));
    line_end_2 = *((*(char***)(callsite_sp + LINEEND_INDIRECT_SP_OFF) + 3));
#else
    line_end_1 = *(char **)(callsite_sp + LINEEND_SP_OFF);
    line_end_2 = *(char **)(callsite_sp + LINEEND_SP_OFF + 4);
#endif
    line_end = more_text ? line_end_1 : line_end_2;
    while (line_end > line_start && *(line_end - 1) == ' ') {
      line_end--;
    }
    rtl_apply(line_start, line_end);
  }
}
