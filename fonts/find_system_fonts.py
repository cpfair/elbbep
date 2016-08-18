import struct
import sys
import shutil
import glob
import os
import json

MICROCODE_OFFSET = 0x8000000
BOOTLOADER_OFFSET = 0x4000

# The firmware image contains a lookup table of RESOURCE_ID_... keys to numeric resource IDs in the pbpack.
# The item struct is a pointer to a string followed by a resource index.
# Sometime the index has other stuff in the word - idk, I just mask it out.
def extract_system_font_resource_ids(target_bin_path):
    target_bin = open(target_bin_path, "rb").read()
    if len(target_bin) % 4 != 0:
        target_bin += '\0' * (4 - (len(target_bin) % 4))
    words = struct.unpack("<" + "L" * (len(target_bin)/4), target_bin)
    last_word = 0
    final_table = {}
    work_table = {}
    since_last = 0
    for word in words:
        since_last += 1
        if last_word > MICROCODE_OFFSET and last_word < 0x9000000:
            name_ptr = last_word - MICROCODE_OFFSET - BOOTLOADER_OFFSET
            if target_bin[name_ptr:name_ptr + 11] == "RESOURCE_ID":
                full_name = target_bin[name_ptr:target_bin[name_ptr:].index('\0') + name_ptr]
                work_table[full_name] = word & 0xffff
                since_last = 0
        if since_last > 2:
            # No longer in the table - reset it for the next run.
            work_table = {}
        elif len(work_table) > len(final_table):
            # Save the best (largest) table-shaped thing we've found so far.
            final_table = work_table
        last_word = word
    return final_table

if len(sys.argv) < 2:
    print("find_system_fonts.py tintin_fw.bin [unpacked_res_dir pfo_dest_dir]")
    sys.exit(0)

result = extract_system_font_resource_ids(sys.argv[1])
print(json.dumps(result))

if len(sys.argv) == 4:
    for name, key in result.items():
        if "FALLBACK_INTERNAL" in name:
            continue
        src = glob.glob(os.path.join(sys.argv[2], "%03d*" % key))[0]
        dest = os.path.join(sys.argv[3], "%d_%s.pfo" % (key, name.replace("RESOURCE_ID_", "")))
        shutil.copyfile(src, dest)
