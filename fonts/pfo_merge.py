from __future__ import division
import math
from collections import namedtuple, defaultdict
import struct
import sys

Font = namedtuple("Font", "max_height wildcard compressed glyphs")
Glyph = namedtuple("Glyph", "codepoints data")

HASHTABLE_DIRECTORY_SIZE = 255
HASHTABLE_DIRECTORY_ITEM_SIZE = 4
OFFSET_TABLE_MAX_SIZE = 128

def font_read(pfo_path):
    HASHTABLE_CHAIN_ITEM = struct.Struct("<HH")
    pfo = open(pfo_path, "rb").read()

    pfo_ver = struct.unpack("<B", pfo[0])[0]
    assert pfo_ver in (2, 3)
    if pfo_ver == 2:
        HEADER_SIZE = 8
        pfo_ver, max_height, glyph_ct, wildcard, hashtable_sz, codept_sz = struct.unpack('<BBHHBB', pfo[:HEADER_SIZE])
        compressed = False
    else:
        HEADER_SIZE = 10
        pfo_ver, max_height, glyph_ct, wildcard, hashtable_sz, codept_sz, s_size, features = struct.unpack('<BBHHBBBB', pfo[:HEADER_SIZE])
        if not features & 1:
            HASHTABLE_CHAIN_ITEM = struct.Struct("<HL")
        compressed = features & 2
    assert codept_sz == 2

    glyphs = {}

    glyphs_base = HEADER_SIZE + HASHTABLE_DIRECTORY_SIZE * HASHTABLE_DIRECTORY_ITEM_SIZE + glyph_ct * HASHTABLE_CHAIN_ITEM.size

    for directory_index in range(HASHTABLE_DIRECTORY_SIZE):
        dir_addr = HEADER_SIZE + directory_index * HASHTABLE_DIRECTORY_ITEM_SIZE
        _, table_size, offset = struct.unpack("<BBH", pfo[dir_addr:dir_addr + HASHTABLE_DIRECTORY_ITEM_SIZE])
        chain_base = HEADER_SIZE + HASHTABLE_DIRECTORY_SIZE * HASHTABLE_DIRECTORY_ITEM_SIZE + offset
        for item_idx in range(table_size):
            item_addr = chain_base + item_idx * HASHTABLE_CHAIN_ITEM.size
            codept, offset = HASHTABLE_CHAIN_ITEM.unpack(pfo[item_addr:item_addr + HASHTABLE_CHAIN_ITEM.size])
            if offset not in glyphs:
                data_start = glyphs_base + offset
                h, w = struct.unpack("<BB", pfo[data_start:data_start + 2])
                bitmap_data_sz = int(math.ceil((h * w) / 8))
                bitmap_data_sz = int(math.ceil(bitmap_data_sz / 4) * 4)
                data_end = data_start + 5 + bitmap_data_sz
                glyph_data = pfo[data_start:data_end]
                glyphs[offset] = Glyph([codept], glyph_data)
            else:
                glyphs[offset].codepoints.append(codept)
    return Font(max_height, wildcard, compressed, glyphs)

def font_write(font, pfo_path):
    bitmapdata_length = sum(len(g.data) for g in font.glyphs.values())
    HASHTABLE_CHAIN_ITEM = struct.Struct("<HL")
    features = 0
    if bitmapdata_length < 65535:
        HASHTABLE_CHAIN_ITEM = struct.Struct("<HH")
        features |= 1
    if font.compressed:
        features |= 2
    header = struct.pack('<BBHHBBBB', 3, font.max_height, len(font.glyphs), font.wildcard, HASHTABLE_DIRECTORY_SIZE, 2, 10, features)

    # Generate hashtable chain lists
    glyph_data = "\0\0\0\0"
    codepoint_offset_pairs = []
    for glyph in sorted(font.glyphs.values(), key=lambda x: x.codepoints[0]):
        for cpt in glyph.codepoints:
            codepoint_offset_pairs.append((cpt, len(glyph_data)))
        glyph_data += glyph.data

    chains = ["" for i in range(HASHTABLE_DIRECTORY_SIZE)]
    chain_counts = defaultdict(int)
    for cpt, offset in sorted(codepoint_offset_pairs, key=lambda x: x[0]):
        bin_no = cpt % HASHTABLE_DIRECTORY_SIZE
        chains[bin_no] += HASHTABLE_CHAIN_ITEM.pack(cpt, offset)
        chain_counts[bin_no] += 1
        assert chain_counts[bin_no] < OFFSET_TABLE_MAX_SIZE

    # Generate hashtable directory
    hashtable_data = b''
    off = 0
    for x in range(HASHTABLE_DIRECTORY_SIZE):
        hashtable_data += struct.pack("<BBH", x, chain_counts[x], off)
        off += len(chains[x])

    open(pfo_path, "wb").write(header + hashtable_data + "".join(chains) + glyph_data)

def merge_fonts(font_1, font_2):
    assert font_1.compressed == font_2.compressed
    max_height = font_1.max_height

    font_cpt_map = {}
    for glyph in font_1.glyphs.values():
        new_glyph = Glyph([], glyph.data)
        for cpt in glyph.codepoints:
            font_cpt_map[cpt] = new_glyph

    for glyph in font_2.glyphs.values():
        new_glyph = Glyph([], glyph.data)
        for cpt in glyph.codepoints:
            font_cpt_map[cpt] = new_glyph

    font_glyphs = {}
    for cpt, glyph in font_cpt_map.items():
        glyph.codepoints.append(cpt)
        if glyph not in font_glyphs.values():
            font_glyphs[cpt] = glyph
    return Font(max_height, font_1.wildcard, font_1.compressed, font_glyphs)

if len(sys.argv) < 3:
    print("pfo_merge.py (font.pfo)+ out.pfo")
    sys.exit(0)

fonts = [font_read(pfo_path) for pfo_path in sys.argv[1:-1]]
font_accum = fonts[0]
for font in fonts[1:]:
    font_accum = merge_fonts(font_accum, font)
font_write(font_accum, sys.argv[-1])
