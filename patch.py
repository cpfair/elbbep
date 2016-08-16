from patch_tools import Patcher

bin_path = "/Volumes/MacintoshHD/Users/collinfair/Library/Application Support/Pebble SDK/SDKs/3.14/sdk-core/pebble/aplite/qemu/qemu_micro_flash.orig.bin"

p = Patcher(
    target_bin_path=bin_path,
    emu_elf_path="/Volumes/MacintoshHD/Users/collinfair/Library/Application Support/Pebble SDK/SDKs/3.14/sdk-core/pebble/aplite/qemu/aplite_sdk_debug.elf",
    emu_bin_path=bin_path,
    patch_c_path="runtime/patch.c",
    other_c_paths=["runtime/text_shaper.c", "runtime/text_shaper_lut.c", "runtime/utf8.c", "runtime/rtl.c"]
)

p.wrap("graphics_draw_text_patch", p.match_symbol("graphics_draw_text"))

p.define_macro("LINEEND_SP_OFF", p.match("""
    mov r0.+
    add r1, sp, #(?P<off>\d+).*
    mov r2.+
    mov r3.+
    bl .+
    ldr .+
    str .+
    movs .+
""").groups["off"])

p.define_macro("RENDERHDLR_SP_OFF", p.match("""
    ldr\..+
    ldr r\d, \[sp, #(?P<off>\d+)\].*
    ldr r\d, \[r\d, #4\]
    cbz .+
""").groups["off"])

p.define_macro("RENDERHDLR_ARG3_SP_OFF", p.match("""
    mov r0, .+
    mov r1, .+
    ldr r2, \[sp, #(?P<off>\d+)\].*
    blx .+
    b.+
""").groups["off"])

p.inject("render_wrap", p.match("""
    mov r0, .+
    mov r1, .+
    JUMP
    ldr r2, .+
    blx .+
    b.+
"""), supplant=True)

p.finalize("/Volumes/MacintoshHD/Users/collinfair/Library/Application Support/Pebble SDK/SDKs/3.14/sdk-core/pebble/aplite/qemu/qemu_micro_flash.bin")
