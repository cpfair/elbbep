import sys
from patch_tools import Patcher, CallsiteValue

if len(sys.argv) < 5:
    print("patch.py platform tintin_fw.bin libpebble.a tintin_fw.out.bin")

platform = sys.argv[1]
PLATFORM_UNSHAPE_MAP = {
    "aplite": False
}

TEXT_UNSHAPE = PLATFORM_UNSHAPE_MAP.get(platform, True)

p = Patcher(
    platform=platform,
    target_bin_path=sys.argv[2],
    libpebble_a_path=sys.argv[3],
    patch_c_path="runtime/patch.c",
    other_c_paths=[
        "runtime/patch.S",
        "runtime/text_shaper.c",
        "runtime/text_shaper_lut.c",
        "runtime/utf8.c",
        "runtime/rtl.c",
        "runtime/rtl_ranges.c",
        "runtime/font_ranges.c"
    ],
    cflags=["-DTEXT_UNSHAPE"] if TEXT_UNSHAPE else []
)

gdt_match = p.match_symbol("graphics_draw_text")
gdt_end_match = p.match(r"""JUMP
add sp, .+
ldmia.w sp!.+
.+
bx\s+lr""", start=gdt_match.start, n=0)
print("GDT %x - %x" % (gdt_match.start, gdt_end_match.start))

if TEXT_UNSHAPE:
    # As it turns out, the Pebble text renderer runs with <16 bytes of stack free in some situations
    # So we need to make sure the calls through to the OS use 0 more bytes stack than the original.
    # For unshaping, we need to preserve the *text arg for the unshape call.
    # We find that in graphics_draw_text's stack frame.
    # However, we also need to intercept the return of the wrapped graphics_draw_text for this reshaping.
    # So we can't just branch back to the old graphics_draw_text (we won't get execution back)
    # Nor can we BL - since then we lose the caller's return site.
    # So, need to patch the end of graphics_draw_text to return to our code without the use of LR.
    gdt_return_overwrote_mcode = p.target_bin[gdt_end_match.start:p.addr_step(gdt_end_match.end, 2)]

    # The text is stored in a struct or something on the stack, passed to the first call of graphics_draw_text.
    first_call_match = p.match("bl .+", start=gdt_match.start, end=gdt_end_match.start, n=0)
    first_call_r0_match = p.match(r"mov r0, (?P<reg>r\d+)", n=-1, start=gdt_match.start, end=first_call_match.start)
    text_struct_sp_off = int(p.match(r"add %s, sp, #(?P<off>\d+)" % first_call_r0_match.groups["reg"], n=-1, start=gdt_match.start, end=first_call_r0_match.start).groups["off"])


    # The end of graphics_draw_text is a stack pointer op, wide pop, another stack ptr op, then bx lr
    # We need to grab a value of a register before popping - the one that points to *text.
    text_ptr_match = p.match(r"mov r1, (?P<reg>r\d+)", end=gdt_end_match.start, n=-1)
    unshape_asm = """
    LDR r0, [sp, #""" + str(text_struct_sp_off) + """]
    """ + \
    "\n".join((".byte 0x%x" % ord(mc) for mc in gdt_return_overwrote_mcode[:8])) + """
    @ At this point, we're back to immediately after the BL...
    @ Run the un-shaper...
    PUSH {ip, lr}
    BL unshape_text
    @ Done!
    POP {ip, pc}
    """
    p.inject("graphics_draw_text_unshape", gdt_end_match, asm=unshape_asm)


p.wrap("graphics_draw_text_patch", gdt_match)

p.wrap("graphics_text_layout_get_content_size_patch",
       p.match_symbol("graphics_text_layout_get_content_size"),
       "GSize", passthru=False)
p.wrap("graphics_text_layout_get_content_size_with_attributes_patch",
       p.match_symbol("graphics_text_layout_get_content_size_with_attributes"),
       "GSize")

# Find the layout driver function - it's the last call graphics_draw_text makes.
layout_driver_match = p.match(r"bl\s+0x(?P<fnc>[0-9a-f]+)$", start=gdt_match.start, end=gdt_end_match.end, n=-1)
layout_driver_addr = int(layout_driver_match.groups["fnc"], 16)

print("Layout driver start %x" % layout_driver_addr)

layout_driver_end_match = p.match(r"""
    add sp, #(?P<sz1>\d+).*
    ldm.+\{(?P<popregs>.+)\}
    """, start=layout_driver_addr, n=0)
layout_driver_frame_size = int(layout_driver_end_match.groups["sz1"]) + (layout_driver_end_match.groups["popregs"].count(",") + 1) * 4

if platform == "aplite":
    # Dig out a pointer to the structure that holds the input iteration state.
    # The layout function has two fast-exit checks, then enters a setup block.
    # The last call in this setup block is to the thing that sets up the desired structure.
    # Its r1 is what we want.
    layour_driver_setup_end = p.match(r"b(?:ne|eq).+", start=layout_driver_addr, n=2).start
    layout_driver_last_call = p.match("bl.+", start=layout_driver_addr, end=layour_driver_setup_end, n=-1).start
    lineend_sp_off = int(p.match("add r1, sp, #(?P<off>\d+).*", start=layout_driver_addr, end=layout_driver_last_call, n=-1).groups["off"])
    print("Line-end stack pointer offset %x" % lineend_sp_off)
    p.define_macro("LINEEND_SP_OFF", lineend_sp_off)
else:
    # Empirically determined - probably only works on >=4.1.
    p.define_macro("LINEEND_INDIRECT_SP_OFF", 56)

# This is the part that actually calls the render callback - which we intend to wrap.
render_handler_call_match = p.match(r"""
    mov r0, (?P<gctx_reg>r\d+)
    mov r1, (?P<layout_reg>r\d+)
    JUMP
    ldr r2, \[sp, #(?P<arg3_sp_off>\d+)\].*
    blx (?P<hdlr_reg>r\d+)
    b.+
""")

more_text_reg_match = p.match(r"c(mp|bnz).+(?P<reg>r\d+).*", start=layout_driver_addr, end=render_handler_call_match.start, n=-1)
more_text_reg_value = CallsiteValue(register=more_text_reg_match.groups["reg"])
print("More-text register %s" % more_text_reg_match.groups["reg"])

print("Render handler call %x" % render_handler_call_match.start)
layout_reg_match = CallsiteValue(register=render_handler_call_match.groups["layout_reg"])

hdlr_reg_value = CallsiteValue(register=render_handler_call_match.groups["hdlr_reg"])
print("Handler function pointer register %s" % hdlr_reg_value.register)

if int(more_text_reg_value.register.strip("r")) < 4:
    # We need more_text to survive the call through to the handler.
    # So, it can't be in r0-r3
    more_text_reg_match = p.match(r"c(mp|bnz).+(?P<reg>r\d+).*", start=layout_driver_addr, end=layout_driver_end_match.start, n=-3)
    more_text_reg_value = CallsiteValue(register=more_text_reg_match.groups["reg"])
    print("More-text register was in r0-r3!")
    print("More-text register re-matched to %s" % more_text_reg_match.groups["reg"])
assert int(more_text_reg_value.register.strip("r")) > 2

p.define_macro("RENDERHDLR_ARG3_SP_OFF", render_handler_call_match.groups["arg3_sp_off"])

# The magic number 4 in this block is the stack offset for what we push here.
render_wrap_asm = """
    @ At this point, we have the render handler args 1 (gcontext) and 2 (layout) in r0/r1, and were about to load the 3rd (??) from wherever.
    @ The handler itself is in r3, probably
    @ render_rtl_step wants *text, more_text, and callsite SP
    @ So, first back up the render handler args
    PUSH {r0-r3}
    LDR r0, [""" + layout_reg_match.register + """]
    MOV r1, """ + more_text_reg_value.register + """
    ADD r2, sp, #16
    @ We don't need to preserve LR!
    BL render_rtl_step
    POP {r0-r3}
    @ Load the final handler argument
    LDR r2, [sp, #""" + render_handler_call_match.groups["arg3_sp_off"] + """]
    @ Run render handler
    BLX """ + hdlr_reg_value.register + """
    @ Run the RTL routine again
    @ This time we need not preserve r0-r3
    LDR r0, [""" + layout_reg_match.register + """]
    MOV r1, """ + more_text_reg_value.register + """
    MOV r2, sp
    BL render_rtl_step
    # Return to original site
    B render_wrap__return
"""

p.inject("render_wrap", render_handler_call_match, asm=render_wrap_asm)

p.finalize(sys.argv[4])
