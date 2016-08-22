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

GSize graphics_text_layout_get_content_size_patch(
    char *text, GFont const font, const GRect box,
    const GTextOverflowMode overflow_mode, GTextAlignment alignment) {
  return PASSTHRU(graphics_text_layout_get_content_size_with_attributes_patch,
                  text, font, box, overflow_mode, alignment, NULL);
}

void graphics_draw_text_patch(GContext *ctx, char *text, GFont const font,
                              const GRect box,
                              const GTextOverflowMode overflow_mode,
                              GTextAlignment alignment,
                              GTextAttributes *text_attributes) {
  shape_text(text);

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
}

typedef void (*RenderHandler)(void *, void *, void *);
void render_wrap(void *gcontext, char **layout, void *parameter3,
                 RenderHandler handler, bool more_text, char *callsite_sp) {
  // First, apply RTL transforms.
  char *line_start, *line_end, *line_end_1, *line_end_2;
  line_start = *layout;
  bool did_rtl_transform = false;
  // Occasionally on hardware, callsite_sp is total nonsense - 0x21000000 - and
  // we'd hardfault reading it. Only ever saw this on the Aplite home screen
  // while scrolling back and forth (rare, often took many minutes to repro).
  // Why? I have no idea. Shouldn't be a stack overflow overwriting the arg
  // value, since rendering uses much more on top of what we use here and it
  // works fine. In any case, checking it here stops the crashes, at the risk of
  // occasionally showing flashes of backwards text when whatever unusual
  // situation arises to corrupt the value. Could maybe solve it by hand-writing
  // the entire routine in asm so as to avoid the extra parameter entirely,
  // since $sp itself is valid.
  if (line_start >= (char *)SRAM_BASE && *line_start &&
      callsite_sp < SRAM_EXTENT) {
    line_end_1 = *(char **)(callsite_sp + LINEEND_SP_OFF);
    line_end_2 = *(char **)(callsite_sp + LINEEND_SP_OFF + 4);
    line_end = more_text ? line_end_1 : line_end_2;
    while (line_end > line_start && *(line_end - 1) == ' ') {
      line_end--;
    }
    did_rtl_transform = rtl_apply(line_start, line_end);
  }

  // Call through to actual render handler.
  handler(gcontext, layout, parameter3);

  // If we applied the RTL operations once, do them again to undo the changes.
  if (did_rtl_transform) {
    rtl_apply(line_start, line_end);
  }
}
