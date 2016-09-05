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
  bool did_shape_text = false;
  did_shape_text = shape_text(text);

  GSize res = PASSTHRU(graphics_text_layout_get_content_size_with_attributes_patch, text, font, box, overflow_mode, alignment, text_attributes);

  // Magic cookie used by the diagnostics app when checking if the firmware is installed...
  if (did_shape_text && overflow_mode != 0xE5) {
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

void graphics_draw_text_patch(GContext *ctx, char *text, GFont const font,
                              const GRect box,
                              const GTextOverflowMode overflow_mode,
                              GTextAlignment alignment,
                              GTextAttributes *text_attributes) {
#ifdef TEXT_UNSHAPE
  bool did_shape_text = false;
  did_shape_text = shape_text(text);
#else
  shape_text(text);
#endif

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
        alignment = GTextAlignmentRight;
        break;
      } else {
        break;
      }
    }
  }
  PASSTHRU(graphics_draw_text_patch, ctx, text, font, box, overflow_mode,
           alignment, text_attributes);
#ifdef TEXT_UNSHAPE
  if (did_shape_text) {
    unshape_text(text);
  }
#endif
}

void* render_wrap_pre(char* line_start, bool more_text, char* callsite_sp) {
  char *line_end, *line_end_1, *line_end_2;
  bool did_rtl_transform = false;
  if (line_start >= (char *)SRAM_BASE && *line_start) {
    line_end_1 = *(char **)(callsite_sp + LINEEND_SP_OFF);
    line_end_2 = *(char **)(callsite_sp + LINEEND_SP_OFF + 4);
    line_end = more_text ? line_end_1 : line_end_2;
    while (line_end > line_start && *(line_end - 1) == ' ') {
      line_end--;
    }
    if (rtl_apply(line_start, line_end)) {
      return line_end;
    }
  }
  return NULL;
}
