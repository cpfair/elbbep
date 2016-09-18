.syntax unified
.thumb

#ifndef TEXT_UNSHAPE
.global graphics_text_layout_get_content_size_with_attributes_patch
graphics_text_layout_get_content_size_with_attributes_patch:
    PUSH {r0-r3, ip, lr}
    BL shape_text
    POP {r0-r3, ip, lr}
    B graphics_text_layout_get_content_size_with_attributes_patch__passthru
#endif


# Params are: GContext *ctx, char *text, GFont const font,
#             const GRect box,
#             const GTextOverflowMode overflow_mode,
#             GTextAlignment alignment,
#             GTextAttributes *text_attributes
.global graphics_draw_text_patch
graphics_draw_text_patch:
    # Text shaper wants *text as input
    PUSH {r0-r3, ip, lr}
    MOV r0, r1
    BL shape_text
    POP {r0-r3, ip, lr}
    # Fix alignment - takes *text, alignment (which is a stacked arg)
    PUSH {r0-r3, ip, lr}
    MOV r0, r1
    LDR r1, [sp, #32]
    BL gdt_alignment_step
    STR r0, [sp, #32]
    POP {r0-r3, ip, lr}
    B graphics_draw_text_patch__passthru
