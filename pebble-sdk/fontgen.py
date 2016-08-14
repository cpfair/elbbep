#!/usr/bin/env python

import argparse
import freetype
import os
import re
import struct
import sys
import itertools
import json
from math import ceil

sys.path.append(os.path.join(os.path.dirname(__file__), '../'))
import generate_c_byte_array

# Font
#   FontInfo
#       (uint8_t)  version
#       (uint8_t)  max_height
#       (uint16_t) number_of_glyphs
#       (uint16_t) wildcard_codepoint
#       (uint8_t) hash_table_size
#       (uint8_t) codepoint_bytes
#
#   (uint32_t) hash_table[]
#       this hash table contains offsets to each glyph offset table. each offset is counted in
#       32 bit blocks from the start of the offset tables block. Each entry in the hash table is
#       as follow: (uint8_t) hash value
#                  (uint8_t) offset_table_size
#                  (uint16_t) offset
#
#   (uint32_t) offset_tables[][]
#       this list of tables contains offsets into the glyph_table for characters 0x20 to 0xff
#       each offset is counted in 32-bit blocks from the start of the glyph
#       each individual offset table contains ~10 sorted glyphs
#       table. 16-bit offsets are keyed by 16-bit codepoints.
#       packed:     (codepoint_bytes [uint16_t | uint32_t]) codepoint
#                   (uint_16) offset
#
#   (uint32_t) glyph_table[]
#       [0]: the 32-bit block for offset 0 is used to indicate that a glyph is not supported
#       then for each glyph:
#       [offset + 0]  packed:   (int_8) offset_top
#                               (int_8) offset_left,
#                              (uint_8) bitmap_height,
#                              (uint_8) bitmap_width (LSB)
#
#       [offset + 1]           (int_8) horizontal_advance
#                              (24 bits) zero padding
#         [offset + 2] bitmap data (unaligned rows of bits), padded with 0's at
#         the end to make the bitmap data as a whole use multiples of 32-bit
#         blocks

MIN_CODEPOINT = 0x20
MAX_2_BYTES_CODEPOINT = 0xffff
MAX_EXTENDED_CODEPOINT = 0x10ffff
FONT_VERSION_1 = 1
FONT_VERSION_2 = 2
# Set a codepoint that the font doesn't know how to render
# The watch will use this glyph as the wildcard character
WILDCARD_CODEPOINT = 0x25AF # White vertical rectangle
ELLIPSIS_CODEPOINT = 0x2026

HASH_TABLE_SIZE = 255
OFFSET_TABLE_MAX_SIZE = 128
MAX_GLYPHS_EXTENDED = HASH_TABLE_SIZE * OFFSET_TABLE_MAX_SIZE
MAX_GLYPHS = 256
OFFSET_SIZE_BYTES = 4

def grouper(n, iterable, fillvalue=None):
    """grouper(3, 'ABCDEFG', 'x') --> ABC DEF Gxx"""
    args = [iter(iterable)] * n
    return itertools.izip_longest(fillvalue=fillvalue, *args)

def hasher(codepoint, num_glyphs):
    return (codepoint % num_glyphs)

def bits(x):
    data = []
    for i in range(8):
        data.insert(0, int((x & 1) == 1))
        x = x >> 1
    return data

class Font:
    def __init__(self, ttf_path, height, max_glyphs, legacy):
        self.version = FONT_VERSION_2
        self.ttf_path = ttf_path
        self.max_height = int(height)
        self.legacy = legacy
        self.face = freetype.Face(self.ttf_path)
        self.face.set_pixel_sizes(0, self.max_height)
        self.name = self.face.family_name + "_" + self.face.style_name
        self.wildcard_codepoint = WILDCARD_CODEPOINT
        self.number_of_glyphs = 0
        self.table_size = HASH_TABLE_SIZE
        self.tracking_adjust = 0
        self.regex = None
        self.codepoints = range(MIN_CODEPOINT, MAX_EXTENDED_CODEPOINT)
        self.codepoint_bytes = 2
        self.max_glyphs = max_glyphs
        self.glyph_table = []
        self.hash_table = [0] * self.table_size
        self.offset_tables = [[] for i in range(self.table_size)]
        self.codepoints_map = {}
        return

    def set_tracking_adjust(self, adjust):
        self.tracking_adjust = adjust

    def set_regex_filter(self, regex_string):
        if regex_string != ".*":
            try:
                self.regex = re.compile(unicode(regex_string, 'utf8'), re.UNICODE)
            except Exception, e:
                raise Exception("Supplied filter argument was not a valid regular expression.")
        else:
            self.regex = None

    def set_codepoint_list(self, list_path):
        codepoints_file = open(list_path)
        codepoints_json = json.load(codepoints_file)
        self.codepoints = [int(cp) for cp in codepoints_json["codepoints"]]

    def set_codepoint_map(self, map_path):
        codepoints_file = open(map_path)
        codepoints_map = json.load(codepoints_file)
        self.codepoints_map = {int(x): y for x, y in codepoints_map.items()}

    def is_supported_glyph(self, codepoint):
        return (self.face.get_char_index(codepoint) > 0 or (codepoint == unichr(self.wildcard_codepoint)))

    def glyph_bits(self, gindex):
        flags = (freetype.FT_LOAD_RENDER if self.legacy else
            freetype.FT_LOAD_RENDER | freetype.FT_LOAD_MONOCHROME | freetype.FT_LOAD_TARGET_MONO)
        self.face.load_glyph(gindex, flags)
        # Font metrics
        bitmap = self.face.glyph.bitmap
        advance = self.face.glyph.advance.x / 64     # Convert 26.6 fixed float format to px
        advance += self.tracking_adjust
        width = bitmap.width
        height = bitmap.rows
        left = self.face.glyph.bitmap_left
        bottom = self.max_height - self.face.glyph.bitmap_top
        pixel_mode = self.face.glyph.bitmap.pixel_mode

        glyph_structure = ''.join((
            '<',  #little_endian
            'B',  #bitmap_width
            'B',  #bitmap_height
            'b',  #offset_left
            'b',  #offset_top
            'b'   #horizontal_advance
            ))
        glyph_header = struct.pack(glyph_structure, width, height, left, bottom, advance)

        glyph_bitmap = []
        if pixel_mode == 1: # monochrome font, 1 bit per pixel
            for i in range(bitmap.rows):
                row = []
                for j in range(bitmap.pitch):
                    row.extend(bits(bitmap.buffer[i*bitmap.pitch+j]))
                glyph_bitmap.extend(row[:bitmap.width])
        elif pixel_mode == 2: # grey font, 255 bits per pixel
            for val in bitmap.buffer:
                glyph_bitmap.extend([1 if val > 127 else 0])
        else:
            # freetype-py should never give us a value not in (1,2)
            raise Exception("Unsupported pixel mode: {}".format(pixel_mode))

        glyph_packed = []
        for word in grouper(32, glyph_bitmap, 0):
            w = 0
            for index, bit in enumerate(word):
                w |= bit << index
            glyph_packed.append(struct.pack('<I', w))

        return glyph_header + ''.join(glyph_packed)

    def fontinfo_bits(self):
        return struct.pack('<BBHHBB',
                           self.version,
                           self.max_height,
                           self.number_of_glyphs,
                           self.wildcard_codepoint,
                           self.table_size,
                           self.codepoint_bytes)

    def build_tables(self):
        def build_hash_table(bucket_sizes):
            acc = 0
            for i in range(self.table_size):
                bucket_size = bucket_sizes[i]
                self.hash_table[i] = (struct.pack('<BBH', i, bucket_size, acc))
                acc += bucket_size * (OFFSET_SIZE_BYTES + self.codepoint_bytes)

        def build_offset_tables(glyph_entries):
            offset_table_format = '<LL' if self.codepoint_bytes == 4 else '<HL'
            bucket_sizes = [0] * self.table_size
            for entry in glyph_entries:
                codepoint, offset = entry
                glyph_hash = hasher(codepoint, self.table_size)
                self.offset_tables[glyph_hash].append(struct.pack(offset_table_format, codepoint, offset))
                bucket_sizes[glyph_hash] = bucket_sizes[glyph_hash] + 1
                if bucket_sizes[glyph_hash] > OFFSET_TABLE_MAX_SIZE:
                  print "error: %d > 127" % bucket_sizes[glyph_hash]
            return bucket_sizes

        def add_glyph(codepoint, next_offset, gindex, glyph_indices_lookup):
            offset = next_offset
            if gindex not in glyph_indices_lookup:
                glyph_bits = self.glyph_bits(gindex)
                glyph_indices_lookup[gindex] = offset
                self.glyph_table.append(glyph_bits)
                next_offset += len(glyph_bits)
            else:
                offset = glyph_indices_lookup[gindex]

            if (codepoint > MAX_2_BYTES_CODEPOINT):
                self.codepoint_bytes = 4

            self.number_of_glyphs += 1
            return offset, next_offset, glyph_indices_lookup

        def codepoint_is_in_subset(codepoint):
           if (codepoint not in (WILDCARD_CODEPOINT, ELLIPSIS_CODEPOINT)):
              if self.regex is not None:
                  if self.regex.match(unichr(codepoint)) is None:
                      return False
              if codepoint not in self.codepoints:
                 return False
           return True

        glyph_entries = []
        # MJZ: The 0th offset of the glyph table is 32-bits of
        # padding, no idea why.
        self.glyph_table.append(struct.pack('<I', 0))
        self.number_of_glyphs = 0
        glyph_indices_lookup = dict()
        next_offset = 4
        codepoint, gindex = self.face.get_first_char()

        # add wildcard_glyph
        offset, next_offset, glyph_indices_lookup = add_glyph(WILDCARD_CODEPOINT, next_offset, 0, glyph_indices_lookup)
        glyph_entries.append((WILDCARD_CODEPOINT, offset))

        if not self.codepoints_map:
            while gindex:
                # Hard limit on the number of glyphs in a font
                if (self.number_of_glyphs > self.max_glyphs):
                    break

                if (codepoint is WILDCARD_CODEPOINT):
                    raise Exception('Wildcard codepoint is used for something else in this font')

                if (gindex is 0):
                    raise Exception('0 index is reused by a non wildcard glyph')

                if (codepoint_is_in_subset(codepoint)):
                    offset, next_offset, glyph_indices_lookup = add_glyph(codepoint, next_offset, gindex, glyph_indices_lookup)
                    glyph_entries.append((codepoint, offset))

                codepoint, gindex = self.face.get_next_char(codepoint, gindex)
        else:
            for codepoint, gindex in sorted(self.codepoints_map.items(), key=lambda x: x[0]):
                offset, next_offset, glyph_indices_lookup = add_glyph(codepoint, next_offset, gindex, glyph_indices_lookup)
                glyph_entries.append((codepoint, offset))

        # Make sure the entries are sorted by codepoint
        sorted_entries = sorted(glyph_entries, key=lambda entry: entry[0])
        hash_bucket_sizes = build_offset_tables(sorted_entries)
        build_hash_table(hash_bucket_sizes)

    def bitstring(self):
        btstr = self.fontinfo_bits()
        btstr += ''.join(self.hash_table)
        for table in self.offset_tables:
            btstr += ''.join(table)
        btstr += ''.join(self.glyph_table)

        return btstr

    def convert_to_h(self):
        to_file = os.path.splitext(self.ttf_path)[0] + '.h'
        f = open(to_file, 'wb')
        f.write("#pragma once\n\n")
        f.write("#include <stdint.h>\n\n")
        f.write("// TODO: Load font from flash...\n\n")
        self.build_tables()
        bytes = self.bitstring()
        generate_c_byte_array.write(f, bytes, self.name)
        f.close()
        return to_file

    def convert_to_pfo(self, pfo_path=None):
        to_file = pfo_path if pfo_path else (os.path.splitext(self.ttf_path)[0] + '.pfo')
        with open(to_file, 'wb') as f:
            self.build_tables()
            f.write(self.bitstring())
        return to_file

def cmd_pfo(args):
    max_glyphs = MAX_GLYPHS_EXTENDED if args.extended else MAX_GLYPHS
    f = Font(args.input_ttf, args.height, max_glyphs, args.legacy)
    if (args.tracking):
        f.set_tracking_adjust(args.tracking)
    if (args.filter):
        f.set_regex_filter(args.filter)
    if (args.list):
        f.set_codepoint_list(args.list)
    if (args.map):
        f.set_codepoint_map(args.map)
    f.convert_to_pfo(args.output_pfo)

def cmd_header(args):
    f = Font(args.input_ttf, args.height, MAX_GLYPHS, args.legacy)
    if (args.filter):
        f.set_regex_filter(args.filter)
    f.convert_to_h()

def process_all_fonts():
    font_directory = "ttf"
    font_paths = []
    for _, _, filenames in os.walk(font_directory):
        for filename in filenames:
            if os.path.splitext(filename)[1] == '.ttf':
                font_paths.append(os.path.join(font_directory, filename))

    header_paths = []
    for font_path in font_paths:
        f = Font(font_path, 14)
        print "Rendering {0}...".format(f.name)
        f.convert_to_pfo()
        to_file = f.convert_to_h()
        header_paths.append(os.path.basename(to_file))

    f = open(os.path.join(font_directory, 'fonts.h'), 'w')
    print>>f, '#pragma once'
    for h in header_paths:
        print>>f, "#include \"{0}\"".format(h)
    f.close()

def process_cmd_line_args():
    parser = argparse.ArgumentParser(description="Generate pebble-usable fonts from ttf files")
    subparsers = parser.add_subparsers(help="commands", dest='which')

    pbi_parser = subparsers.add_parser('pfo', help="make a .pfo (pebble font) file")
    pbi_parser.add_argument('--extended', action='store_true', help="Whether or not to store > 256 glyphs")
    pbi_parser.add_argument('height', metavar='HEIGHT', help="Height at which to render the font")
    pbi_parser.add_argument('--tracking', type=int, help="Optional tracking adjustment of the font's horizontal advance")
    pbi_parser.add_argument('--filter', help="Regex to match the characters that should be included in the output")
    pbi_parser.add_argument('--list', help="json list of characters to include")
    pbi_parser.add_argument('--map', help="json map of codept->glyphs to embed")
    pbi_parser.add_argument('--legacy', action='store_true', help="use legacy rasterizer (non-mono) to preserve font dimensions")
    pbi_parser.add_argument('input_ttf', metavar='INPUT_TTF', help="The ttf to process")
    pbi_parser.add_argument('output_pfo', metavar='OUTPUT_PFO', help="The pfo output file")
    pbi_parser.set_defaults(func=cmd_pfo)

    pbh_parser = subparsers.add_parser('header', help="make a .h (pebble fallback font) file")
    pbh_parser.add_argument('height', metavar='HEIGHT', help="Height at which to render the font")
    pbh_parser.add_argument('input_ttf', metavar='INPUT_TTF', help="The ttf to process")
    pbh_parser.add_argument('output_header', metavar='OUTPUT_HEADER', help="The pfo output file")
    pbh_parser.add_argument('--filter', help="Regex to match the characters that should be included in the output")

    pbi_parser.set_defaults(func=cmd_pfo)
    pbh_parser.set_defaults(func=cmd_header)

    args = parser.parse_args()
    args.func(args)

def main():
    if len(sys.argv) < 2:
        # process all the fonts in the ttf folder
        process_all_fonts()
    else:
        # process an individual file
        process_cmd_line_args()


if __name__ == "__main__":
    main()
