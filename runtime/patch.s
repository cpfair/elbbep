.syntax unified
.thumb

.global graphics_text_layout_get_content_size_with_attributes_patch
graphics_text_layout_get_content_size_with_attributes_patch:
    PUSH {r0-r3, ip, lr}
    BL shape_text
    POP {r0-r3, ip, lr}
    B graphics_text_layout_get_content_size_with_attributes_patch__passthru
