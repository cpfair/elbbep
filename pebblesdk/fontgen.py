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
import unicodedata

sys.path.append(os.path.join(os.path.dirname(__file__), '../'))
import generate_c_byte_array

# Font v3 -- https://pebbletechnology.atlassian.net/wiki/display/DEV/Pebble+Resource+Pack+Format
#   FontInfo
#       (uint8_t)  version                           - v1
#       (uint8_t)  max_height                        - v1
#       (uint16_t) number_of_glyphs                  - v1
#       (uint16_t) wildcard_codepoint                - v1
#       (uint8_t)  hash_table_size                   - v2
#       (uint8_t)  codepoint_bytes                   - v2
#       (uint8_t)  size                              - v3  # Save the size of FontInfo for sanity
#       (uint8_t)  features                          - v3
#
#   font_info_struct_size is the size of the FontInfo structure. This makes extending this structure
#   in the future far simpler.
#
#   'features' is a bitmap defined as follows:
#       0: offset table offsets uint32 if 0, uint16 if 1
#       1: glyphs are bitmapped if 0, RLE4 encoded if 1
#     2-7: reserved
#
#   (uint32_t) hash_table[]
#       glyph_tables are found in the resource image by converting a codepoint into an offset from
#       the start of the resource. This conversion is implemented as a hash where collisions are
#       resolved by separate chaining. Each entry in the hash table is as follows:
#                  (uint8_t) hash value
#                  (uint8_t) offset_table_size
#                  (uint16_t) offset
#       A codepoint is converted into a hash value by the hash function -- this value is a direct
#       index into the hash table array. 'offset' is the location of the correct offset_table list
#       from the start of offset_tables, and offset_table_size gives the number of glyph_tables in
#       the list (i.e., the number of codepoints that hash to the same value).
#
#   (uint32_t) offset_tables[][]
#       this list of tables contains offsets into the glyph_table for the codepoint.
#       each offset is counted in 32-bit blocks from the start of glyph_table.
#       packed:     (codepoint_bytes [uint16_t | uint32_t]) codepoint
#                   (features[0] [uint16_t | uint32_t]) offset
#
#   (uint32_t) glyph_table[]
#       [0]: the 32-bit block for offset 0 is used to indicate that a glyph is not supported
#       then for each glyph:
#       [offset + 0]  packed:   (int_8) offset_top
#                               (int_8) offset_left,
#                              (uint_8) bitmap_height,       NB: in v3, if RLE4 compressed, this
#                                                                field is contains the number of
#                                                                RLE4 units.
#                              (uint_8) bitmap_width (LSB)
#
#       [offset + 1]           (int_8) horizontal_advance
#                              (24 bits) zero padding
#       [offset + 2] bitmap data (unaligned rows of bits), padded with 0's at
#       the end to make the bitmap data as a whole use multiples of 32-bit
#       blocks

MIN_CODEPOINT = 0x20
MAX_2_BYTES_CODEPOINT = 0xffff
MAX_EXTENDED_CODEPOINT = 0x10ffff
FONT_VERSION_1 = 1
FONT_VERSION_2 = 2
FONT_VERSION_3 = 3
# Set a codepoint that the font doesn't know how to render
# The watch will use this glyph as the wildcard character
WILDCARD_CODEPOINT = 0x25AF # White vertical rectangle
ELLIPSIS_CODEPOINT = 0x2026
# Features
FEATURE_OFFSET_16 = 0x01
FEATURE_RLE4 = 0x02

ZERO_WIDTH_GLYPH_INDEX = "zwg"


HASH_TABLE_SIZE = 255
OFFSET_TABLE_MAX_SIZE = 128
MAX_GLYPHS_EXTENDED = HASH_TABLE_SIZE * OFFSET_TABLE_MAX_SIZE
MAX_GLYPHS = 256

GLYPH_BUFFER_SIZE_BYTES = 256

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
        self.version = FONT_VERSION_3
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
        self.offset_size_bytes = 4
        self.features = 0
        self.codepoints_map = {}
        self.zero_width_codepts = []
        self.shift = (0, 0)
        self.threshold = 127
        self.dump_dir = None
        self.collect_dir = None
        self.codept_labels = {}

        self.glyph_header = ''.join((
            '<',  # little_endian
            'B',  # bitmap_width
            'B',  # bitmap_height
            'b',  # offset_left
            'b',  # offset_top
            'b'   # horizontal_advance
            ))

    def set_compression(self, engine):
        if self.version != FONT_VERSION_3:
            raise Exception("Compression being set but version != 3 ({})". format(self.version))
        if engine == 'RLE4':
            self.features |= FEATURE_RLE4
        else:
            raise Exception("Unsupported compression engine: '{}'. Font {}".format(engine,
                            self.ttf_path))

    def set_version(self, version):
        self.version = version

    def set_tracking_adjust(self, adjust):
        self.tracking_adjust = adjust

    def set_regex_filter(self, regex_string):
        if regex_string != ".*":
            try:
                self.regex = re.compile(unicode(regex_string, 'utf8'), re.UNICODE)
            except Exception, e:
                raise Exception("Supplied filter argument was not a valid regular expression."
                                "Font: {}".format(self.ttf_path))
        else:
            self.regex = None

    def set_codepoint_map(self, map_path):
        codepoints_file = open(map_path)
        codepoints_map = json.load(codepoints_file)
        self.codepoints_map = {int(x): y for x, y in codepoints_map.items()}

    def set_codepoint_list(self, list_path):
        codepoints_file = open(list_path)
        codepoints_json = json.load(codepoints_file)
        self.codepoints = [int(cp) for cp in codepoints_json["codepoints"]]

    def set_zero_width_codept_list(self, list_path):
        codepoints_file = open(list_path)
        codepoints_json = json.load(codepoints_file)
        self.zero_width_codepts = [int(cp) for cp in codepoints_json["codepoints"]]

    def set_shift(self, shift):
        self.shift = shift

    def set_threshold(self, threshold):
        self.threshold = threshold

    def set_dump_dir(self, dump_dir):
        self.dump_dir = dump_dir

    def set_collect_dir(self, collect_dir):
        self.collect_dir = collect_dir

    def set_codept_labels(self, labels_path):
        self.codept_labels = {int(k): v for k, v in json.load(open(labels_path, "r")).items()}

    def is_supported_glyph(self, codepoint):
        return (self.face.get_char_index(codepoint) > 0 or
                (codepoint == unichr(self.wildcard_codepoint)))

    def compress_glyph_RLE4(self, bitmap):
        # This Run Length Compression scheme works by converting runs of identical symbols to the
        # symbol and the length of the run. The length of each run of symbols is limited to
        # [1..2**(RLElen-1)]. For RLE4, the length is 3 bits (0-7), or 1-8 consecutive symbols.
        # For example: 11110111 is compressed to 1*4, 0*1, 1*3. or [(1, 4), (0, 1), (1, 3)]

        RLE_LEN = 2**(4-1)  # TODO possibly make this a parameter.
        # It would likely be a good idea to look into the 'bitstream' package for lengths that won't
        # easily fit into a byte/short/int.

        # First, generate a list of tuples (bit, count).
        unit_list = [(name, len(list(group))) for name, group in itertools.groupby(bitmap)]

        # Second, generate a list of RLE tuples where count <= RLE_LEN. This intermediate step will
        # make it much easier to implement the binary stream packer below.
        rle_unit_list = []
        for name, length in unit_list:
            while length > 0:
                unit_len = min(length, RLE_LEN)
                rle_unit_list.append((name, unit_len))
                length -= unit_len

        # Note that num_units does not include the padding added below.
        num_units = len(rle_unit_list)

        # If the list is odd, add a padding unit
        if (num_units % 2) == 1:
            rle_unit_list.append((0, 1))

        # Now pack the tuples into a binary stream. We can't pack nibbles, so join two
        glyph_packed = []
        it = iter(rle_unit_list)
        for name, length in it:
            name2, length2 = next(it)
            packed_byte = name << 3 | (length - 1) | name2 << 7 | (length2 - 1) << 4
            glyph_packed.append(struct.pack('<B', packed_byte))

        # Pad out to the nearest 4 bytes
        while (len(glyph_packed) % 4) > 0:
            glyph_packed.append(struct.pack('<B', 0))

        return (glyph_packed, num_units)

    # Make sure that we will be able to decompress the glyph in-place
    def check_decompress_glyph_RLE4(self, glyph_packed, width, rle_units):
        # The glyph buffer before decoding is arranged as follows:
        #  [ <header> | <free space> | <encoded glyph> ]
        # Make sure that we can decode the encoded glyph to end up with the following arrangement:
        #  [ <header> |       <decoded glyph>          ]
        # without overwriting the unprocessed encoded glyph in the process

        header_size = struct.calcsize(self.glyph_header)
        dst_ptr = header_size
        src_ptr = GLYPH_BUFFER_SIZE_BYTES - len(glyph_packed)

        def glyph_packed_iterator(tbl, num):
            for i in xrange(0, num):
                yield struct.unpack('<B', tbl[i])[0]

        # Generate glyph buffer. Ignore the header
        bitmap = [0] * GLYPH_BUFFER_SIZE_BYTES
        bitmap[-len(glyph_packed):] = glyph_packed_iterator(glyph_packed, len(glyph_packed))

        out_num_bits = 0
        out = 0
        total_length = 0
        while rle_units > 0:
            if src_ptr >= GLYPH_BUFFER_SIZE_BYTES:
                raise Exception("Error: input stream too large for buffer. Font {}".
                                format(self.ttf_path))

            unit_pair = bitmap[src_ptr]
            src_ptr += 1
            for i in range(min(rle_units, 2)):
                colour = (unit_pair >> 3) & 1
                length = (unit_pair & 0x07) + 1
                total_length += length

                if colour:
                    # Generate the bitpattern 111...
                    add = (1 << length) - 1
                    out |= (add << out_num_bits)
                out_num_bits += length

                if out_num_bits >= 8:
                    if dst_ptr >= src_ptr:
                        raise Exception("Error: unable to RLE4 decode in place! Overrun. Font {}".
                                        format(self.ttf_path))
                    if dst_ptr >= GLYPH_BUFFER_SIZE_BYTES:
                        raise Exception("Error: output bitmap too large for buffer. Font {}".
                                        format(self.ttf_path))
                    bitmap[dst_ptr] = (out & 0xFF)
                    dst_ptr += 1
                    out >>= 8
                    out_num_bits -= 8

                unit_pair >>= 4
                rle_units -= 1

        while out_num_bits > 0:
            bitmap[dst_ptr] = (out & 0xFF)
            dst_ptr += 1
            out >>= 8
            out_num_bits -= 8

        # Success! We can in-place decode this glyph
        return True


    def glyph_bits(self, codepoint, gindex):
        if gindex ==  ZERO_WIDTH_GLYPH_INDEX:
            return struct.pack(self.glyph_header, 0, 0, 0, 0, 0)
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

        glyph_packed = []
        if height and width:
            glyph_bitmap = []
            if pixel_mode == 1:  # monochrome font, 1 bit per pixel
                for i in range(bitmap.rows):
                    row = []
                    for j in range(bitmap.pitch):
                        row.extend(bits(bitmap.buffer[i*bitmap.pitch+j]))
                    glyph_bitmap.extend(row[:bitmap.width])
            elif pixel_mode == 2:  # grey font, 255 bits per pixel
                for val in bitmap.buffer:
                    glyph_bitmap.extend([1 if val > self.threshold else 0])
            else:
                # freetype-py should never give us a value not in (1,2)
                raise Exception("Unsupported pixel mode: {}. Font {}".
                                format(pixel_mode, self.ttf_path))

            if self.dump_dir:
                fn = str(gindex)
                try:
                    fn += "_" + self.codept_labels[codepoint]
                except KeyError:
                    name = unicodedata.name(unichr(codepoint), None)
                    if name:
                        fn += "_" + name
                fn += ".txt"
                fd = open(os.path.join(self.dump_dir, fn), "w")
                idx = 0
                fd.write("%s\n" % json.dumps({
                    "width": width,
                    "height": height,
                    "left": left,
                    "bottom": bottom,
                    "advance": advance
                }))
                for i in range(bitmap.rows):
                    for j in range(bitmap.width):
                        fd.write("#" if glyph_bitmap[idx] else " ")
                        idx += 1
                    fd.write("\n")
                fd.close()

                if self.collect_dir:
                    collect_path = os.path.join(self.collect_dir, fn)
                    if os.path.exists(collect_path):
                        fd = open(collect_path, "r")
                        meta = json.loads(fd.readline())
                        width = meta["width"]
                        height = meta["height"]
                        left = meta["left"]
                        bottom = meta["bottom"]
                        advance = meta["advance"]
                        bitmap_str = fd.read().replace("\n", "")
                        assert len(bitmap_str) == width * height
                        glyph_bitmap = [1 if c == "#" else 0 for c in bitmap_str]


            if (self.features & FEATURE_RLE4):
                # HACK WARNING: override the height with the number of RLE4 units.
                glyph_packed, height = self.compress_glyph_RLE4(glyph_bitmap)
                if height > 255:
                    raise Exception("Unable to RLE4 compress -- more than 255 units required"
                                    "({}). Font {}".format(height, self.ttf_path))
                # Check that we can in-place decompress. Will raise an exception if not.
                self.check_decompress_glyph_RLE4(glyph_packed, width, height)
            else:
                for word in grouper(32, glyph_bitmap, 0):
                    w = 0
                    for index, bit in enumerate(word):
                        w |= bit << index
                    glyph_packed.append(struct.pack('<I', w))

        left += self.shift[0]
        bottom += self.shift[1]
        glyph_header = struct.pack(self.glyph_header, width, height, left, bottom, advance)

        return glyph_header + ''.join(glyph_packed)

    def fontinfo_bits(self):
        if self.version == FONT_VERSION_2:
            s = struct.Struct('<BBHHBB')
            return s.pack(self.version,
                          self.max_height,
                          self.number_of_glyphs,
                          self.wildcard_codepoint,
                          self.table_size,
                          self.codepoint_bytes)
        else:
            s = struct.Struct('<BBHHBBBB')
            return s.pack(self.version,
                          self.max_height,
                          self.number_of_glyphs,
                          self.wildcard_codepoint,
                          self.table_size,
                          self.codepoint_bytes,
                          s.size,
                          self.features)


    def build_tables(self):
        def build_hash_table(bucket_sizes):
            acc = 0
            for i in range(self.table_size):
                bucket_size = bucket_sizes[i]
                self.hash_table[i] = (struct.pack('<BBH', i, bucket_size, acc))
                acc += bucket_size * (self.offset_size_bytes + self.codepoint_bytes)

        def build_offset_tables(glyph_entries):
            offset_table_format = '<'
            offset_table_format += 'L' if self.codepoint_bytes == 4 else 'H'
            offset_table_format += 'L' if self.offset_size_bytes == 4 else 'H'

            bucket_sizes = [0] * self.table_size
            for entry in glyph_entries:
                codepoint, offset = entry
                glyph_hash = hasher(codepoint, self.table_size)
                self.offset_tables[glyph_hash].append(
                        struct.pack(offset_table_format, codepoint, offset))
                bucket_sizes[glyph_hash] = bucket_sizes[glyph_hash] + 1
                if bucket_sizes[glyph_hash] > OFFSET_TABLE_MAX_SIZE:
                    print "error: %d > 127" % bucket_sizes[glyph_hash]
            return bucket_sizes

        def add_glyph(codepoint, next_offset, gindex, glyph_indices_lookup):
            offset = next_offset
            if gindex not in glyph_indices_lookup:
                glyph_bits = self.glyph_bits(codepoint, gindex)
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
        offset, next_offset, glyph_indices_lookup = add_glyph(WILDCARD_CODEPOINT, next_offset, 0,
                                                              glyph_indices_lookup)
        glyph_entries.append((WILDCARD_CODEPOINT, offset))

        # add zero-width codept(s), if desired
        for codept in self.zero_width_codepts:
            offset, next_offset, glyph_indices_lookup = add_glyph(codept, next_offset, ZERO_WIDTH_GLYPH_INDEX,
                                                                  glyph_indices_lookup)
            glyph_entries.append((codept, offset))

        if not self.codepoints_map:
            while gindex:
                # Hard limit on the number of glyphs in a font
                if (self.number_of_glyphs > self.max_glyphs):
                    break

                if (codepoint is WILDCARD_CODEPOINT):
                    raise Exception('Wildcard codepoint is used for something else in this font.'
                                    'Font {}'.format(self.ttf_path))

                if (gindex is 0):
                    raise Exception('0 index is reused by a non wildcard glyph. Font {}'.
                                    format(self.ttf_path))

                if (codepoint_is_in_subset(codepoint)):
                    offset, next_offset, glyph_indices_lookup = add_glyph(codepoint, next_offset,
                                                                          gindex, glyph_indices_lookup)
                    glyph_entries.append((codepoint, offset))

                codepoint, gindex = self.face.get_next_char(codepoint, gindex)
        else:
            for codepoint, gindex in sorted(self.codepoints_map.items(), key=lambda x: x[0]):
                offset, next_offset, glyph_indices_lookup = add_glyph(codepoint, next_offset, gindex, glyph_indices_lookup)
                glyph_entries.append((codepoint, offset))

        # Decide if we need 2 byte or 4 byte offsets
        glyph_data_bytes = sum(len(glyph) for glyph in self.glyph_table)
        if self.version == FONT_VERSION_3 and glyph_data_bytes < 65536:
            self.features |= FEATURE_OFFSET_16
            self.offset_size_bytes = 2

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
    if (args.compress):
        f.set_compression(args.compress)
    if (args.zero_width_codept_list):
        f.set_zero_width_codept_list(args.zero_width_codept_list)
    if (args.shift):
        f.set_shift(tuple((int(x) for x in args.shift.split(","))))
    if (args.threshold):
        f.set_threshold(int(args.threshold))
    if (args.dump_bitmaps):
        f.set_dump_dir(args.dump_bitmaps)
    if (args.collect_bitmaps):
        assert args.dump_bitmaps, "--collect-bitmaps requires --dump-bitmaps" # Because I'm lazy
        f.set_collect_dir(args.collect_bitmaps)
    if (args.codept_labels):
        f.set_codept_labels(args.codept_labels)
    f.set_version(int(args.version))
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

    pbi_parser = subparsers.add_parser('pfo', help="Make a .pfo (pebble font) file")
    pbi_parser.add_argument('-v', '--version', type=int, choices=xrange(2, 4),
                            help="Force output of Version V .pfo (DEFAULT is 2)")
    pbi_parser.add_argument('--compress', help="Valid compression types are: RLE4. Version 3 only.")
    pbi_parser.add_argument('--extended', action='store_true',
                            help="Whether or not to store > 256 glyphs")
    pbi_parser.add_argument('height', metavar='HEIGHT',
                            help="Height at which to render the font")
    pbi_parser.add_argument('--tracking', type=int,
                            help="Optional tracking adjustment of the font's horizontal advance")
    pbi_parser.add_argument('--filter',
                            help="Regex to match the characters that "
                                 "should be included in the output")
    pbi_parser.add_argument('--list',
                            help="json list of characters to include")
    pbi_parser.add_argument('--map', help="json map of codept->glyphs to embed")
    pbi_parser.add_argument('--dump-bitmaps', help="directory to write editable bitmaps into")
    pbi_parser.add_argument('--collect-bitmaps', help="directory to read bitmaps from, overriding TTF input")
    pbi_parser.add_argument('--codept-labels', help="JSON map of codept->name for dumping bitmaps")
    pbi_parser.add_argument('--zero-width-codept-list', help="json list of codepoints to assign a zero-width glyph")
    pbi_parser.add_argument('--shift', help="dx,dy to shift glyphs by")
    pbi_parser.add_argument('--threshold', help="black/white cutoff value (0-255)", type=int)
    pbi_parser.add_argument('--legacy', action='store_true',
                            help="use legacy rasterizer (non-mono) to preserve font dimensions")
    pbi_parser.add_argument('input_ttf', metavar='INPUT_TTF', help="The ttf to process")
    pbi_parser.add_argument('output_pfo', metavar='OUTPUT_PFO', help="The pfo output file")
    pbi_parser.set_defaults(func=cmd_pfo, version=3)

    pbh_parser = subparsers.add_parser('header', help="make a .h (pebble fallback font) file")
    pbh_parser.add_argument('height', metavar='HEIGHT', help="Height at which to render the font")
    pbh_parser.add_argument('input_ttf', metavar='INPUT_TTF', help="The ttf to process")
    pbh_parser.add_argument('output_header', metavar='OUTPUT_HEADER', help="The pfo output file")
    pbh_parser.add_argument('--filter',
                            help="Regex to match the characters that "
                                 "should be included in the output")

    pbi_parser.set_defaults(func=cmd_pfo)
    pbh_parser.set_defaults(func=cmd_header)

    args = parser.parse_args()

    # Fix up mutual exclusions
    if args.compress and args.version < 3:
        raise Exception("Error: --compress requires Version 3")

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
