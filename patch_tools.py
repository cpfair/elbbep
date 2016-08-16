import re
import struct
import subprocess
from collections import namedtuple

MICROCODE_OFFSET = 0x8000000

PatchOverwrite = namedtuple("PatchOverwrite", "address content")
PatchBranchOffset = namedtuple("PatchBranchOffset", "address symbol link")
PatchAppendAsm = namedtuple("PatchAppendAsm", "symbol content")
PatchDefineSymbol = namedtuple("PatchDefineSymbol", "name address")
PatchDefineMacro = namedtuple("PatchDefineMacro", "name value")
MatchResult = namedtuple("MatchResult", "start end markers groups")

class Patcher:
    def __init__(self, target_bin_path, patch_c_path, other_c_paths):
        self.target_bin_path = target_bin_path
        self.patch_c_path = patch_c_path
        self.patch_c = open(patch_c_path, "r").read()
        self.other_c_paths = other_c_paths

        self.target_bin = open(target_bin_path, "rb").read()
        self.target_deasm = subprocess.check_output(["arm-none-eabi-objdump", "-b", "binary", "-marm", "-Mforce-thumb", "-D", target_bin_path]).replace("\t", " ")

        self.op_queue = []

    def _q(self, op):
        self.op_queue.append(op)

    def match(self, pattern):
        pattern_lines = pattern.split("\n")
        # Filter out MARKERS
        marker_indices = {}
        filtered_pattern_lines = []
        for line in pattern_lines:
            if re.match("[A-Z]+", line.strip()):
                marker_indices[line.strip()] = len(filtered_pattern_lines)
            elif line.strip():
                filtered_pattern_lines.append(line.strip())

        pattern_composed = "\n".join((r"^\s*(?P<addr_%d>[a-f0-9]+):\s*[a-f0-9]+(?: [a-f0-9]+)?\s*%s$" % (idx, pattern.strip()) for idx, pattern in enumerate(filtered_pattern_lines)))
        print(pattern_composed)
        # Find it in the deasm
        match_exp = re.compile(pattern_composed, re.MULTILINE)
        matches = match_exp.finditer(self.target_deasm)
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

        return MatchResult(
            start=int(match.group("addr_0"), 16),
            end=int(match.group("addr_%d" % (len(filtered_pattern_lines)-1)), 16),
            markers={k: int(match.group("addr_%d" % v), 16) for k, v in marker_indices.items()},
            groups={k: v for k, v in match.groupdict().items() if not k.startswith("addr_")}
        )

    def _regmatch_asm(self, dest_signature):
        dirtied_registers = set()
        # Fish any register parameters they requested out of the stack.
        # We assume the function doesn't have any other parameters...
        requested_register_matches = list(re.finditer(r"(?P<type>REGISTER_MATCH|CALLSITE_SP)(?:\((?P<arg>[^)]+)\))?", dest_signature))
        proxy_asm = ""
        trailing_proxy_asm = ""
        stacked_args = max(0, len(requested_register_matches) - 4)
        stacked_args_offset = stacked_args * 4
        for arg_reg_no, requested_match in enumerate(requested_register_matches):
            if requested_match.group("type") == "REGISTER_MATCH":
                arg = requested_match.group("arg")
                reg_no = int(arg.strip("r"))
                if arg_reg_no < 4 and arg_reg_no == reg_no and arg_reg_no not in dirtied_registers:
                    continue
                dirtied_registers.add(arg_reg_no)
                stack_off = reg_no * 4
                if arg_reg_no < 4:
                    proxy_asm += "LDR r%d, [sp, #%d]\n" % (arg_reg_no, stack_off)
                else:
                    stack_dest_off = -(arg_reg_no - 4) * 4 - stacked_args_offset
                    proxy_asm += "LDR ip, [sp, #%d]\n" % stack_off
                    proxy_asm += "STR ip, [sp, #%d]\n" % stack_dest_off

            elif requested_match.group("type") == "CALLSITE_SP":
                if arg_reg_no < 4:
                    proxy_asm += "ADD r%d, sp, #%d\n" % (arg_reg_no, 56)
                else:
                    stack_dest_off = -(arg_reg_no - 4) * 4 - stacked_args_offset
                    proxy_asm += "ADD ip, sp, #%d\n" % 56
                    proxy_asm += "STR ip, [sp, #%d]\n" % stack_dest_off
            else:
                raise RuntimeError("Unknown register parameter request type %s" % requested_match.group("type"))
        if stacked_args:
            proxy_asm += "SUB sp, #%d\n" % stacked_args_offset
            trailing_proxy_asm += "ADD sp, #%d\n" % stacked_args_offset
        return proxy_asm, trailing_proxy_asm

    def _find_signature(self, name):
        # Probabilistically correct.
        matched_line = None
        for line in self.patch_c.split("\n"):
            if name in line and not line.startswith("//"):
                if not matched_line or len(matched_line) < len(line):
                    matched_line = line
        assert matched_line
        return matched_line

    def inject(self, dest_symbol, dest_match, supplant=False):
        # Patch insertion points must
        # - not have any instruction in the next 2 half-words that is PC-relative
        #   (as these are copied into the proxy stub)
        # - not include any PC-relative instructions within two half-words of the jump point
        # - no include any 32-bit instructions within two half-words of the jump point

        # The jump point that gets pasted into the located signature
        jmp_mcode = [0, 0, 0, 0] # Filled in later by the relocator.
        jmp_insert_addr = dest_match.markers["JUMP"]
        end_patch_addr = jmp_insert_addr + len(jmp_mcode)
        print("0x%x" % (jmp_insert_addr + MICROCODE_OFFSET))
        self._q(PatchBranchOffset(jmp_insert_addr, "%s__proxy" % dest_symbol, False))

        # Grab the stuff we're going to overwrite
        overwrote_mcode = self.target_bin[jmp_insert_addr:jmp_insert_addr + len(jmp_mcode)]

        # Assemble the proxy function to be assembled and linked
        # Preserve all registers of the caller
        proxy_asm = "PUSH.W {r0-r12, lr}\n"
        arg_setup, arg_teardown = self._regmatch_asm(self._find_signature(dest_symbol))
        proxy_asm += arg_setup
        # Jump to injected function
        proxy_asm += "BLX %s\n" % dest_symbol
        proxy_asm += arg_teardown
        # Restore caller variables
        proxy_asm += "POP.W {r0-r12, lr}\n"
        if not supplant:
            # Perform whatever actions we overwrote.
            for byte in overwrote_mcode:
                proxy_asm += ".byte 0x%x\n" % ord(byte)
        # Return to original site
        proxy_asm += "B %s__return\n" % dest_symbol

        self._q(PatchDefineSymbol("%s__return" % dest_symbol, MICROCODE_OFFSET + end_patch_addr))
        self._q(PatchAppendAsm("%s__proxy" % dest_symbol, proxy_asm))

    def wrap(self, dest_symbol, dest_match):
        # Patch insertion points must
        # - occur at the beginning of a procedure, before the stack has been modified.
        #   (though maybe not if one doesn't need args from the stack, i.e. r0-r3 are fine).
        # - not include any PC-relative instructions within two half-words of the jump point
        # - explicitly encompass any 32-bit instructions that fall within two half-words of the jump point

        # The jump point that gets pasted into the located signature
        jmp_insert_addr = dest_match.markers["JUMP"]
        end_patch_addr = dest_match.markers.get("END", jmp_insert_addr + 4)

        print("0x%x" % jmp_insert_addr)
        self._q(PatchBranchOffset(jmp_insert_addr, dest_symbol, False))

        # Grab the stuff we're going to overwrite
        overwrote_mcode = self.target_bin[jmp_insert_addr:jmp_insert_addr + (end_patch_addr - jmp_insert_addr)]

        # Make the pass-through function for the wrapper to call, should it elect to do so.
        passthru_asm = ""
        # Prep to return to the original function
        # passthru_asm += "LDR ip, =0x%x\n" % (MICROCODE_OFFSET + jmp_insert_addr + len(jmp_mcode) + 1)
        # Perform whatever actions we overwrote.
        for byte in overwrote_mcode:
            passthru_asm += ".byte 0x%x\n" % ord(byte)

        # Return to original site
        passthru_asm += "B %s__return\n" % dest_symbol
        self._q(PatchDefineSymbol("%s__return" % dest_symbol, MICROCODE_OFFSET + end_patch_addr))
        self._q(PatchAppendAsm("%s__passthru" % dest_symbol, passthru_asm))

    def define_function(self, dest_symbol, target_addr):
        self._q(PatchDefineSymbol(dest_symbol, target_addr))

    def define_macro(self, name, value):
        self._q(PatchDefineMacro(name.upper(), value))

    def finalize(self, destination_bin_path):
        # Produce the final files to compile.
        patch_h_composed = """// THIS FILE IS AUTOMATICALLY GENERATED
#define CALLSITE_SP
#define PASSTHRU(name, ...) name ## __passthru(__VA_ARGS__)
#define REGISTER_MATCH(reg)\n"""
        patch_s_composed = ".syntax unified\n.thumb\n"
        for op in self.op_queue:
            if type(op) is PatchAppendAsm:
                patch_h_composed += "void* %s ();\n" % op.symbol
                patch_s_composed += ".thumb_func\n.global %s\n%s:\n\t" % (op.symbol, op.symbol)
                patch_s_composed += op.content.replace("\n", "\n\t") + "\n"

        cflags = ["-std=c99", "-mcpu=cortex-m3", "-mthumb", "-g", "-fPIC", "-fPIE", "-nostdlib", "-Wl,-Tpatch.comp.ld", "-Wl,-Map,patch.comp.map,--emit-relocs", "-D_TIME_H_", "-I.", "-Iruntime", "-O0"]

        # Define new symbols explicitly.
        for op in self.op_queue:
            if type(op) is PatchDefineSymbol:
                patch_s_composed += ".global %s\n.thumb_set %s, 0x%x\n" % (op.name, op.name, op.address)

        # Generate #defines
        for op in self.op_queue:
            if type(op) is PatchDefineMacro:
                patch_h_composed += "#define %s %s\n" % (op.name, op.value)

        # Compile this C and Assembly to an object file.
        open("patch.auto.h", "w").write(patch_h_composed)
        open("patch.comp.s", "w").write(patch_s_composed)
        ldscript = open("patch.ld", "r").read()
        ldscript = ldscript.replace("@TARGET_END@", "0x%x" % (len(self.target_bin) + MICROCODE_OFFSET))
        open("patch.comp.ld", "w").write(ldscript)
        subprocess.check_call(["arm-none-eabi-gcc"] + cflags + ["-o", "patch.comp.o", "patch.comp.s", self.patch_c_path] + self.other_c_paths)

        # Perform requested overwrites on input binary.
        for op in self.op_queue:
            if type(op) is PatchOverwrite:
                self.target_bin = self.target_bin[:op.address] + "".join((chr(x) for x in op.content)) + self.target_bin[op.address + len(op.content):]

        # And relocations.
        # First, we need the symbols from the compiled patch.
        symtab_txt = subprocess.check_output(["arm-none-eabi-nm", "patch.comp.o"])
        symtab = {
            m.group("name"): int(m.group("addr"), 16) for m in re.finditer(r"(?P<addr>[a-f0-9]+)\s+\w+\s+(?P<name>\w+)$", symtab_txt, re.MULTILINE)
        }
        for op in self.op_queue:
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
                self.target_bin = self.target_bin[:op.address] + struct.pack("<HH", instr >> 16, instr & 0xFFFF) + self.target_bin[op.address + 4:]

        # Finally, append the patch code to the target binary
        subprocess.check_call(["arm-none-eabi-objcopy", "patch.comp.o", "-S", "-O", "binary", "patch.comp.bin"])
        self.target_bin += open("patch.comp.bin", "rb").read()
        open(destination_bin_path, "wb").write(self.target_bin)
        open("final.bin", "wb").write(self.target_bin)
