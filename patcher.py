import re
import struct
import subprocess
from collections import namedtuple

MICROCODE_OFFSET = 0x8000000

PatchOverwrite = namedtuple("PatchOverwrite", "address content")
PatchBranchOffset = namedtuple("PatchBranchOffset", "address symbol link")
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

def regmatch_asm(block_txt):
    # Fish any register parameters they requested out of the stack.
    # We assume the function doesn't have any other parameters...
    requested_register_matches = re.findall(r"REGISTER_MATCH\(([^)]+)\)", block_txt.split("\n")[-1])
    assert len(requested_register_matches) <= 4, "Maximum 4 register-matched parameters"
    dirtied_registers = set()
    proxy_asm = ""
    for arg_reg_no, requested_match in enumerate(requested_register_matches):
        # We use global_matches here since our matches are guaranteed to have overwritten any older ones.
        # So, no need to go looking both places.
        if requested_match.startswith("r"):
            reg_no = int(requested_match.strip("r"))
        else:
            reg_no = int(match.group(requested_match).strip("r"))
        if arg_reg_no == reg_no and arg_reg_no not in dirtied_registers:
            continue
        dirtied_registers.add(arg_reg_no)
        stack_off = reg_no * 4
        proxy_asm += "LDR r%d, [sp, #%d]\n" % (arg_reg_no, stack_off)
    return proxy_asm

def patch_inject(block_txt, target_deasm, target_bin):
    # Patch insertion points must
    # - not have any instruction in the next 2 half-words that is PC-relative
    #   (as these are copied into the proxy stub)
    # - not include any PC-relative instructions within two half-words of the jump point
    # - no include any 32-bit instructions within two half-words of the jump point

    dest_symbol = re.match(r"(?:\w+\s)+(\w+)\(", block_txt.split("\n")[-1]).group(1)

    # Pull pattern
    pattern_lines = [x.strip("/ ") for x in block_txt.split("\n") if x.strip("/ ").split(" ")[0] not in ("PATCH") and x.startswith("/")]
    jump_insert_instr_idx = pattern_lines.index("JUMP")
    pattern_lines.remove("JUMP")
    match = match_deasm(target_deasm, pattern_lines)

    # The jump point that gets pasted into the located signature
    jmp_mcode = [0, 0, 0, 0] # Filled in later by the relocator.
    jmp_insert_addr = int(match.group("addr_%d" % jump_insert_instr_idx), 16)
    end_patch_addr = jmp_insert_addr + len(jmp_mcode)
    print("0x%x" % (jmp_insert_addr + MICROCODE_OFFSET))
    yield PatchBranchOffset(jmp_insert_addr, "%s__proxy" % dest_symbol, False)

    # Grab the stuff we're going to overwrite
    overwrote_mcode = target_bin[jmp_insert_addr:jmp_insert_addr + len(jmp_mcode)]

    # Assemble the proxy function to be assembled and linked
    # Preserve all registers of the caller
    proxy_asm = "PUSH.W {r0-r12, lr}\n"
    proxy_asm += regmatch_asm(block_txt)
    # Jump to injected function
    proxy_asm += "BLX %s\n" % dest_symbol
    # Restore caller variables
    proxy_asm += "POP.W {r0-r12, lr}\n"
    # Perform whatever actions we overwrote.
    for byte in overwrote_mcode:
        proxy_asm += ".byte 0x%x\n" % ord(byte)
    # Return to original site
    proxy_asm += "B %s__return\n" % dest_symbol

    yield PatchDefineSymbol("%s__return" % dest_symbol, MICROCODE_OFFSET + end_patch_addr)
    yield PatchAppendAsm("%s__proxy" % dest_symbol, proxy_asm)

def patch_wrap(block_txt, target_deasm, target_bin):
    # Patch insertion points must
    # - occur at the beginning of a procedure, before the stack has been modified.
    #   (though maybe not if one doesn't need args from the stack, i.e. r0-r3 are fine).
    # - not include any PC-relative instructions within two half-words of the jump point
    # - explicitly encompass any 32-bit instructions that fall within two half-words of the jump point

    dest_symbol = re.match(r"(?:\w+\s)+(\w+)\(", block_txt.split("\n")[-1]).group(1)

    # Pull pattern
    pattern_lines = [x.strip("/ ") for x in block_txt.split("\n") if x.strip("/ ").split(" ")[0] not in ("PATCH") and x.startswith("/")]
    jump_insert_instr_idx = pattern_lines.index("JUMP")
    pattern_lines.remove("JUMP")
    end_patch_index = None
    if "END-PATCH" in pattern_lines:
        end_patch_index = pattern_lines.index("END-PATCH")
        pattern_lines.remove("END-PATCH")
    match = match_deasm(target_deasm, pattern_lines)

    # The jump point that gets pasted into the located signature
    jmp_insert_addr = int(match.group("addr_%d" % jump_insert_instr_idx), 16)
    if end_patch_index:
        end_patch_addr = int(match.group("addr_%d" % end_patch_index), 16)
    else:
        end_patch_addr = jmp_insert_addr + 4

    print("0x%x" % jmp_insert_addr)
    yield PatchBranchOffset(jmp_insert_addr, dest_symbol, False)

    # Grab the stuff we're going to overwrite
    overwrote_mcode = target_bin[jmp_insert_addr:jmp_insert_addr + (end_patch_addr - jmp_insert_addr)]

    # Make the pass-through function for the wrapper to call, should it elect to do so.
    passthru_asm = ""
    # Prep to return to the original function
    # passthru_asm += "LDR ip, =0x%x\n" % (MICROCODE_OFFSET + jmp_insert_addr + len(jmp_mcode) + 1)
    # Perform whatever actions we overwrote.
    for byte in overwrote_mcode:
        passthru_asm += ".byte 0x%x\n" % ord(byte)

    # Return to original site
    passthru_asm += "B %s__return\n" % dest_symbol
    yield PatchDefineSymbol("%s__return" % dest_symbol, MICROCODE_OFFSET + end_patch_addr)
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

    yield PatchDefineSymbol(dest_symbol, target_addr + MICROCODE_OFFSET)

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

    # Produce the final files to compile.
    patch_h_composed = """// THIS FILE IS AUTOMATICALLY GENERATED
#define PASSTHRU(name, ...) name ## __passthru(__VA_ARGS__)
#define REGISTER_MATCH(reg)\n"""
    patch_s_composed = ".syntax unified\n.thumb\n"
    for op in pending_operations:
        if type(op) is PatchAppendAsm:
            patch_h_composed += "void* %s ();\n" % op.symbol
            patch_s_composed += ".thumb_func\n.global %s\n%s:\n\t" % (op.symbol, op.symbol)
            patch_s_composed += op.content.replace("\n", "\n\t") + "\n"

    cflags = ["-std=c99", "-mcpu=cortex-m3", "-mthumb", "-g", "-fPIC", "-fPIE", "-nostdlib", "-Wl,-Tpatch.comp.ld", "-Wl,-Map,patch.comp.map,--emit-relocs", "-D_TIME_H_", "-I.", "-Iruntime", "-Os"]

    # Define new symbols explicitly.
    for op in pending_operations:
        if type(op) is PatchDefineSymbol:
            patch_s_composed += ".global %s\n.thumb_set %s, 0x%x\n" % (op.name, op.name, op.address)

    # Compile this C and Assembly to an object file.
    open("patch.auto.h", "w").write(patch_h_composed)
    open("patch.comp.s", "w").write(patch_s_composed)
    ldscript = open("patch.ld", "r").read()
    ldscript = ldscript.replace("@TARGET_END@", "0x%x" % (len(target_bin) + MICROCODE_OFFSET))
    open("patch.comp.ld", "w").write(ldscript)
    subprocess.check_call(["arm-none-eabi-gcc"] + cflags + ["-o", "patch.comp.o", "patch.comp.s", patch_c_path] + other_c_paths)

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
        if type(op) is PatchBranchOffset:
            # Here we're inserting a wide branch
            final_addr = symtab[op.symbol]
            offset = (final_addr - op.address - 4) >> 1
            instr = 0b11110000000000001001000000000000
            s = 0 if offset > 0 else 1
            i1 = (offset >> 23) & 1
            j1 = 0 if s ^ i1 else 1
            i2 = (offset >> 22) & 1
            j2 = 0 if s ^ i2 else 1
            imm10 = (offset >> 11) & 0b1111111111
            imm11 = (offset) & 0b11111111111
            instr |= (s << 26) | (imm10 << 16) | (j1 << 13) | (j2 << 11) | imm11
            if op.link:
                instr |= 1 << 14
            target_bin = target_bin[:op.address] + struct.pack("<HH", instr >> 16, instr & 0xFFFF) + target_bin[op.address + 4:]

    # Finally, append the patch code to the target binary
    subprocess.check_call(["arm-none-eabi-objcopy", "patch.comp.o", "-S", "-O", "binary", "patch.comp.bin"])
    target_bin += open("patch.comp.bin", "rb").read()
    open(target_bin_path.replace(".orig", ""), "wb").write(target_bin)
    open("final.bin", "wb").write(target_bin)


patch("/Volumes/MacintoshHD/Users/collinfair/Library/Application Support/Pebble SDK/SDKs/3.14/sdk-core/pebble/aplite/qemu/qemu_micro_flash.orig.bin", "runtime/patch.c", ["runtime/text_shaper.c", "runtime/text_shaper_lut.c", "runtime/utf8.c", "runtime/rtl.c"])
