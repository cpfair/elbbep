import re
import struct
import subprocess
from collections import namedtuple

MICROCODE_OFFSET = 0x8000000

PatchOverwrite = namedtuple("PatchOverwrite", "address content")
PatchRelocation = namedtuple("PatchRelocation", "address symbol offset")
PatchAppendAsm = namedtuple("PatchAppendAsm", "symbol content")
PatchDefineSymbol = namedtuple("PatchDefineSymbol", "name address")

def match_deasm(target_deasm, pattern_lines):
    pattern_composed = "\n".join((r"^\s*(?P<addr_%d>[a-f0-9]+):\s*[a-f0-9]+(?: [a-f0-9]+)?\s*%s$" % (idx, pattern) for idx, pattern in enumerate(pattern_lines)))
    print(pattern_composed)
    # Find it in the deasm
    match_exp = re.compile(pattern_composed, re.MULTILINE)
    matches = match_exp.finditer(target_deasm)
    match = None
    try:
        match = next(matches)
    except StopIteration:
        assert False, "Pattern %s not found in target" % pattern_lines
    try:
        next(matches)
        assert False, "Pattern %s is ambiguous" % pattern_lines
    except StopIteration:
        pass

    return match

def patch_inject(block_txt, target_deasm, target_bin):
    # Patch insertion points must
    # - not have any instruction in the next 5 half-words that is PC-relative
    #   (as these are copied into the proxy stub)
    # - must not have a 32-bit instruction spanning the +5 half-word offset
    #   (as it'll be broken by the copy)
    # - not rely on the LR being saved.

    dest_symbol = re.match(r"(?:\w+\s)+(\w+)\(", block_txt.split("\n")[-1]).group(1)

    # Pull pattern
    pattern_lines = [x.strip("/ ") for x in block_txt.split("\n") if x.strip("/ ").split(" ")[0] not in ("PATCH") and x.startswith("/")]
    jump_insert_instr_idx = pattern_lines.index("JUMP")
    pattern_lines.remove("JUMP")
    match = match_deasm(target_deasm, pattern_lines)

    # The jump point that gets pasted into the located signature
    jmp_mcode = [
        0x01, 0xb4, # PUSH {r0} - we don't push any more registers here as that uses another half-word.
        0x00, 0x48, # LDR r0, [pc, #0]
        0x80, 0x47, # BLX r0 - NB we mangle the LR past its destination
        0xDE, 0xAD, 0xCA, 0xFE # Will be rewritten by the relocator
    ]
    jmp_insert_addr = int(match.group("addr_%d" % jump_insert_instr_idx), 16)
    print("0x%x" % (jmp_insert_addr + MICROCODE_OFFSET))
    yield PatchOverwrite(jmp_insert_addr, jmp_mcode)
    yield PatchRelocation(jmp_insert_addr + 6, "%s__proxy" % dest_symbol, 1)

    # Grab the stuff we're going to overwrite
    overwrote_mcode = target_bin[jmp_insert_addr:jmp_insert_addr + len(jmp_mcode)]

    # Assembly the proxy function to be assembled and linked
    proxy_asm = ".syntax unified\n"
    # Mangle LR past our offset word
    proxy_asm += "MOV r0, #4\n"
    proxy_asm += "ADD lr, lr, r0\n"

    # Preserve the registers of the caller
    proxy_asm += "PUSH.W {r1-r12, lr}\n"
    # Fish any register parameters they requested out of the stack.
    # We assume the function doesn't have any other parameters...
    requested_register_matches = re.findall(r"REGISTER_MATCH\(([^)]+)\)", block_txt.split("\n")[-1])
    assert len(requested_register_matches) <= 4, "Maximum 4 register-matched parameters"
    for arg_reg_no, requested_match in enumerate(requested_register_matches):
        # We use global_matches here since our matches are guaranteed to have overwritten any older ones.
        # So, no need to go looking both places.
        if requested_match.startswith("r"):
            reg_no = int(requested_match.strip("r"))
        else:
            reg_no = int(match.group(requested_match).strip("r"))
        if reg_no == 0:
            stack_off = 13 * 4
        else:
            stack_off = reg_no * 4 - 4
        proxy_asm += "LDR r%d, [sp, #%d]\n" % (arg_reg_no, stack_off)

    # Jump to injected function
    proxy_asm += "BLX %s\n" % dest_symbol
    # Restore caller variables
    proxy_asm += "POP.W {r1-r12, lr}\n"
    proxy_asm += "POP {r0}\n"
    # Perform whatever actions we overwrote.
    # overwrote_mcode = overwrote_mcode[:2][::-1] + overwrote_mcode[2:]
    for byte in overwrote_mcode:
        proxy_asm += ".byte 0x%x\n" % ord(byte)
    # Return to original site
    proxy_asm += "BX lr\n"

    yield PatchAppendAsm("%s__proxy" % dest_symbol, proxy_asm)

def patch_wrap(block_txt, target_deasm, target_bin):
    # Patch insertion points must
    # - occur at the beginning of a procedure, before the stack has been modified.
    #   (though maybe not if one doesn't need args from the stack, i.e. r0-r3 are fine).
    # - not have any instruction in the next 5 half-words that is PC-relative
    #   (as these are copied into the proxy stub)
    # - must not have a 32-bit instruction spanning the +5 half-word offset
    #   (as it'll be broken by the copy)

    dest_symbol = re.match(r"(?:\w+\s)+(\w+)\(", block_txt.split("\n")[-1]).group(1)

    # Pull pattern
    pattern_lines = [x.strip("/ ") for x in block_txt.split("\n") if x.strip("/ ").split(" ")[0] not in ("PATCH") and x.startswith("/")]
    jump_insert_instr_idx = pattern_lines.index("JUMP")
    pattern_lines.remove("JUMP")
    match = match_deasm(target_deasm, pattern_lines)

    # The jump point that gets pasted into the located signature
    jmp_mcode = [
        0x01, 0xb4, # PUSH {r0} - as we're about to overwrite both.
        0x00, 0x48, # LDR r0, [pc, #0] - r0, as we can't use LR due to instr encoding.
        0x00, 0x47, # BX r0 - we jump to an absolute offset after this patch to resume.
        0xDE, 0xAD, 0xCA, 0xFE # Will be rewritten by the relocator
    ]
    jmp_insert_addr = int(match.group("addr_%d" % jump_insert_instr_idx), 16)
    # The LDR must be aligned.
    if jmp_insert_addr % 4 == 0:
        jmp_mcode[2] = 1 # Make LDR ...#4
        jmp_mcode = jmp_mcode[:6] + [0, 0] + jmp_mcode[6:]
    print(jmp_insert_addr)
    yield PatchOverwrite(jmp_insert_addr, jmp_mcode)
    yield PatchRelocation(jmp_insert_addr + len(jmp_mcode) - 4, "%s__proxy" % dest_symbol, 1)

    # Grab the stuff we're going to overwrite
    overwrote_mcode = target_bin[jmp_insert_addr:jmp_insert_addr + len(jmp_mcode)]

    # Assemble the proxy function that will hand off to the wrapper.
    proxy_asm = ".syntax unified\n"
    # Restore r0 we stashed on the stack
    proxy_asm += "POP {r0}\n"
    # Jump to injected function.
    # when it returns, it will return to the /caller/ of the wrapped fcn!
    proxy_asm += "B %s\n" % dest_symbol
    yield PatchAppendAsm("%s__proxy" % dest_symbol, proxy_asm)

    # Make the pass-through function for the wrapper to call, should it elect to do so.
    passthru_asm = ".syntax unified\n"
    # Prep to return to the original function
    passthru_asm += "LDR ip, =0x%x\n" % (MICROCODE_OFFSET + jmp_insert_addr + len(jmp_mcode) + 1)
    # Perform whatever actions we overwrote.
    for byte in overwrote_mcode:
        passthru_asm += ".byte 0x%x\n" % ord(byte)

    # Return to original site
    passthru_asm += "BX ip\n"
    yield PatchAppendAsm("%s__passthru" % dest_symbol, passthru_asm)

def patch_locate(block_txt, target_deasm):
    dest_symbol = re.match(r"(?:(?:\w|\*)+\s)+(\w+)\(", block_txt.split("\n")[-1]).group(1)

    # Pull pattern
    pattern_lines = [x.strip("/ ") for x in block_txt.split("\n") if x.strip("/ ").split(" ")[0] not in ("PATCH") and x.startswith("/")]
    jump_insert_instr_idx = pattern_lines.index("TARGET")
    pattern_lines.remove("TARGET")
    match = match_deasm(target_deasm, pattern_lines)

    target_addr = int(match.group("addr_%d" % jump_insert_instr_idx), 16)
    print("0x%x" % target_addr)

    # I'm not convinced this is actually required - but my attempt at just defining the symbol didn't work.
    proxy_asm = ".syntax unified\n"
    proxy_asm += "MOV ip, r4\n"
    proxy_asm += "LDR r4, =0x%x\n" % (target_addr + MICROCODE_OFFSET + 1)
    # xor swap
    proxy_asm += "EOR r4, ip\n"
    proxy_asm += "EOR ip, r4\n"
    proxy_asm += "EOR r4, ip\n"
    proxy_asm += "BX ip\n"
    yield PatchAppendAsm(dest_symbol, proxy_asm)


def patch(target_bin_path, patch_c_path, other_c_paths):
    target_bin = open(target_bin_path, "rb").read()
    target_deasm = subprocess.check_output(["arm-none-eabi-objdump", "-b", "binary", "-marm", "-Mforce-thumb", "-D", target_bin_path]).replace("\t", " ")

    patch_c = open(patch_c_path, "r").read()
    pending_operations = []
    for patch_block in re.finditer(r"/// PATCH (?P<mode>\S+)(?:\s+(?P<param>\S+))?(\n/// .+)+(\n.+)", patch_c):
        if patch_block.group("mode") == "INJECT":
            pending_operations += list(patch_inject(patch_block.group(0), target_deasm, target_bin))
        elif patch_block.group("mode") == "WRAP":
            pending_operations += list(patch_wrap(patch_block.group(0), target_deasm, target_bin))
        elif patch_block.group("mode") == "LOCATE":
            pending_operations += list(patch_locate(patch_block.group(0), target_deasm))
        else:
            raise RuntimeError("Unknown patch mode %s at %s" % (patch_block.group("mode"), patch_block.start()))

    # Produce the final C file to compile.
    patch_c_composed = patch_c
    for op in pending_operations:
        if type(op) is PatchAppendAsm:
            patch_c_composed = ("void* %s ();\n" % op.symbol) + patch_c_composed
            patch_c_composed += "__attribute__((naked)) void* %s () {\n" % op.symbol
            patch_c_composed += "  __asm__(\"%s\");\n" % op.content.replace("\n", "\\n")
            patch_c_composed += "}\n"
    
    cflags = ["-std=c99", "-mcpu=cortex-m3", "-mthumb", "-g", "-fPIC", "-fPIE", "-nostdlib", "-Wl,-Tpatch.comp.ld", "-Wl,-Map,patch.comp.map,--emit-relocs", "-D_TIME_H_", "-Iruntime", "-Os"]

    # Define new symbols via the linker.
    for op in pending_operations:
        if type(op) is PatchDefineSymbol:
            cflags += ["-Wl,--defsym=%s=0x%x" % (op.name, op.address)]

    # Compile this C to an object file.
    open("patch.comp.c", "w").write(patch_c_composed)
    ldscript = open("patch.ld", "r").read()
    ldscript = ldscript.replace("@TARGET_END@", "0x%x" % (len(target_bin) + MICROCODE_OFFSET))
    open("patch.comp.ld", "w").write(ldscript)
    subprocess.check_call(["arm-none-eabi-gcc"] + cflags + ["-o", "patch.comp.o", "patch.comp.c"] + other_c_paths)

    # Perform requested overwrites on input binary.
    for op in pending_operations:
        if type(op) is PatchOverwrite:
            target_bin = target_bin[:op.address] + "".join((chr(x) for x in op.content)) + target_bin[op.address + len(op.content):]

    # And relocations.
    # First, we need the symbols from the compiled patch.
    symtab_txt = subprocess.check_output(["arm-none-eabi-nm", "patch.comp.o"])
    symtab = {
        m.group("name"): int(m.group("addr"), 16) for m in re.finditer(r"(?P<addr>[a-f0-9]+)\s+\w+\s+(?P<name>\w+)$", symtab_txt, re.MULTILINE)
    }
    for op in pending_operations:
        if type(op) is PatchRelocation:
            final_addr = symtab[op.symbol] + op.offset
            target_bin = target_bin[:op.address] + struct.pack("<L", final_addr) + target_bin[op.address + 4:]

    # Finally, append the patch code to the target binary
    subprocess.check_call(["arm-none-eabi-objcopy", "patch.comp.o", "-S", "-O", "binary", "patch.comp.bin"])
    target_bin += open("patch.comp.bin", "rb").read()
    open(target_bin_path.replace(".orig", ""), "wb").write(target_bin)
    # open("final.bin", "wb").write(target_bin)


patch("/Volumes/MacintoshHD/Users/collinfair/Library/Application Support/Pebble SDK/SDKs/3.14/sdk-core/pebble/aplite/qemu/qemu_micro_flash.orig.bin", "runtime/patch.c", ["runtime/text_shaper.c", "runtime/text_shaper_lut.c", "runtime/utf8.c", "runtime/rtl.c"])
