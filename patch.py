import sys
from patch_tools import Patcher, CallsiteValue, CallsiteSP

if len(sys.argv) < 5:
    print("patch.py platform tintin_fw.bin libpebble.a tintin_fw.out.bin")

platform = sys.argv[1]

p = Patcher(
    platform=platform,
    target_bin_path=sys.argv[2],
    libpebble_a_path=sys.argv[3],
    patch_c_path="runtime/patch.c",
    other_c_paths=[
        "runtime/patch.s",
        "runtime/text_shaper.c",
        "runtime/text_shaper_lut.c",
        "runtime/utf8.c",
        "runtime/rtl.c",
        "runtime/rtl_ranges.c",
        "runtime/font_ranges.c"
    ]
)

gdt_match = p.match_symbol("graphics_draw_text")
gdt_end_match = p.match(r"bx\s+lr", start=gdt_match.start, n=0)
print("GDT %x - %x" % (gdt_match.start, gdt_end_match.start))

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
arbitrary_offset = 0x34

if platform == "aplite":
    # Dig out a pointer to the structure that holds the input iteration state.
    # The layout function has two fast-exit checks, then enters a setup block.
    # The last call in this setup block is to the thing that sets up the desired structure.
    # Its r1 is what we want.
    layour_driver_setup_end = p.match(r"b(?:ne|eq).+", start=layout_driver_addr, n=2).start
    layout_driver_last_call = p.match("bl.+", start=layout_driver_addr, end=layour_driver_setup_end, n=-1).start
    lineend_sp_off = int(p.match("add r1, sp, #(?P<off>\d+).*", start=layout_driver_addr, end=layout_driver_last_call, n=-1).groups["off"])
    assert lineend_sp_off == (layout_driver_frame_size - arbitrary_offset)
else:
    # I couldn't find a good place to pull this value from.
    # But, it's stable between aplite and basalt so.
    lineend_sp_off = layout_driver_frame_size - arbitrary_offset
    print(lineend_sp_off)

p.define_macro("LINEEND_SP_OFF", lineend_sp_off)

# This is the part that actually calls the render callback - which we intend to wrap.
render_handler_call_match = p.match(r"""
    mov r0, .+
    mov r1, .+
    JUMP
    ldr r2, \[sp, #(?P<arg3_sp_off>\d+)\].*
    blx (?P<hdlr_reg>r\d+)
    b.+
""")

more_text_reg_match = p.match(r"c(mp|bnz).+(?P<reg>r\d+).*", start=layout_driver_addr, end=render_handler_call_match.start, n=-1)
more_text_reg_value = CallsiteValue(register=more_text_reg_match.groups["reg"])
print("More-text register %s" % more_text_reg_match.groups["reg"])

hdlr_reg_value = CallsiteValue(register=render_handler_call_match.groups["hdlr_reg"])
print("Handler function pointer register %s" % hdlr_reg_value.register)

if int(hdlr_reg_value.register.strip("r")) == int(more_text_reg_value.register.strip("r")):
    # I saw this in the PS firmware image - the more-text register happened to be reused for the handler itself.
    # There's another place to find it - at the end of the entire function right before it loops back to render another line
    # I don't use this as the primary source as it's far removed from the actual render call
    # So the register might be repurposed by then
    more_text_reg_match = p.match(r"c(mp|bnz).+(?P<reg>r\d+).*", start=layout_driver_addr, end=layout_driver_end_match.start, n=-3)
    more_text_reg_value = CallsiteValue(register=more_text_reg_match.groups["reg"])
    print("More-text register re-used for hdlr address!")
    print("More-text register re-matched to %s" % more_text_reg_match.groups["reg"])
assert int(more_text_reg_value.register.strip("r")) > 2

p.define_macro("RENDERHDLR_ARG3_SP_OFF", render_handler_call_match.groups["arg3_sp_off"])

# The magic number 40 in this block is the combined stack offset for what we push here,
# plus what the injection wrapper pushes.
render_wrap_asm = """
    @ At this point, we have the render handler args 1 (gcontext) and 2 (layout) in r0/r1, and were about to load the 3rd (??) from wherever.
    @ The handler itself is in r3, probably
    @ render_wrap_pre wants *layout, more_text, and callsite SP
    @ So, first back up the render handler args
    PUSH {r4-r7}
    MOV ip, """ + more_text_reg_value.register + """
    MOV r6, """ + hdlr_reg_value.register + """
    MOV r4, r0
    MOV r5, r1
    @ Set up render_wrap_pre args
    LDR r7, [r1]
    MOV r0, r7
    MOV r1, ip
    ADD r2, sp, #40
    BL render_wrap_pre
    @ The return value is either line_end, or NULL if nothing was RTLed - we need this for later
    @ Reset the renderer args
    MOV r1, r5
    MOV r5, r0
    MOV r0, r4
    @ Load the final handler argument
    LDR r2, [sp, #""" + str(int(render_handler_call_match.groups["arg3_sp_off"]) + 40) + """]
    @ Run handler
    BLX r6
    @ Run rtl_apply again, if required
    CBZ r5, done
    MOV r0, r7
    MOV r1, r5
    BL rtl_apply
done:
    POP {r4-r7}
"""

p.inject("render_wrap", render_handler_call_match, supplant=True, asm=render_wrap_asm)

p.finalize(sys.argv[4])
