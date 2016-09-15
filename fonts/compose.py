from collections import namedtuple
import sys
import glob
import os
import subprocess
import tempfile
import json
import itertools
# This file drives the process of generating a single merged font.
# It takes a directory of PFO files (from find_system_fonts.py) and produces a second directory

if len(sys.argv) < 5:
    print("compose.py input_pfo_dir subset size_shift output_pfo_dir output_code_dir")
    sys.exit(0)

MergeMember = namedtuple("MergeMember", "ttf_path size with_shaper codepts threshold fix_ijam")
MergeMember.__new__.__defaults__ = (None,) * len(MergeMember._fields)
ShaperResult = namedtuple("ShaperResult", "map_tf labels_tf")

ARABIC_FONT = "/Library/Fonts/Tahoma.ttf"
ARABIC_FONT_BOLD = "/Library/Fonts/Tahoma Bold.ttf"
ARABIC_FONT_BOLD_SERIF = "/Library/Fonts/Times New Roman Bold.ttf"

HEBREW_FONT = ARABIC_FONT
HEBREW_FONT_BOLD = ARABIC_FONT_BOLD
HEBREW_FONT_BOLD_SERIF = ARABIC_FONT_BOLD_SERIF

HEBREW_CODEPT_LIST = [0x5c0, 0x5c3, 0x5c6] + list(range(0x5d0, 0x5f5))

# The RTL system doesn't support diacritics, especially crazy stacked harakat in Arabic.
# So we silently drop them from rendering by assigning them zero-width that don't break the RTL scheme
ZERO_WIDTH_CODEPOINT_RANGES = (
    (0x591, 0x5C0), # Hebrew diacritics
    (0x5C1, 0x5C3), # ...
    (0x5C4, 0x5C6), # ...
    (0x5C7, 0x5C8), # ...
    (0x610, 0x61B), # Arabic diacritics
    (0x64B, 0x660), # ...
    (0x6D6, 0x6DC), # ...
    (0x6DF, 0x6E9), # ...
    (0x6EA, 0x6EE), # ...
    (0x8B6, 0x8FF)  # Arabic Extended-A diacritics
)
ZERO_WIDTH_CODEPOINTS = list(itertools.chain.from_iterable((range(*r) for r in ZERO_WIDTH_CODEPOINT_RANGES)))

blacklist = ("NUMBERS", "SUBSET", "EMOJI")

shaper_result = None

def select_template(size, variant, size_shift_key):
    NOTIFICATION_SET_SM = [
        MergeMember(ARABIC_FONT, 15, True),
        MergeMember(HEBREW_FONT, 15, False, HEBREW_CODEPT_LIST)
    ]

    NOTIFICATION_SET_SM_BOLD = [
        MergeMember(ARABIC_FONT_BOLD, 14, True, fix_ijam=True),
        MergeMember(HEBREW_FONT_BOLD, 16, False, HEBREW_CODEPT_LIST)
    ]

    NOTIFICATION_SET_MED = [
        MergeMember(ARABIC_FONT, 19, True, threshold=100, fix_ijam=True),
        MergeMember(HEBREW_FONT, 19, False, HEBREW_CODEPT_LIST, threshold=100)
    ]

    NOTIFICATION_SET_MED_BOLD = [
        MergeMember(ARABIC_FONT_BOLD, 19, True, fix_ijam=True),
        MergeMember(HEBREW_FONT_BOLD, 19, False, HEBREW_CODEPT_LIST)
    ]

    NOTIFICATION_SET_LG = [
        MergeMember(ARABIC_FONT, 24, True),
        MergeMember(HEBREW_FONT, 26, False, HEBREW_CODEPT_LIST)
    ]

    NOTIFICATION_SET_LG_BOLD = [
        MergeMember(ARABIC_FONT_BOLD, 24, True),
        MergeMember(HEBREW_FONT_BOLD, 26, False, HEBREW_CODEPT_LIST)
    ]

    notification_size_map = {
        "small": (NOTIFICATION_SET_SM, NOTIFICATION_SET_SM_BOLD),
        "medium": (NOTIFICATION_SET_MED, NOTIFICATION_SET_MED_BOLD),
        "large": (NOTIFICATION_SET_LG, NOTIFICATION_SET_LG_BOLD)
    }

    TEMPLATES = {
        (9, None): [
            MergeMember(ARABIC_FONT, 9, True), # This is about 1px too tall - but any smaller and it renders terribly.
            MergeMember(HEBREW_FONT, 9, False, HEBREW_CODEPT_LIST)
        ],
        (14, None): [
            MergeMember(ARABIC_FONT, 13, True),
            MergeMember(HEBREW_FONT, 14, False, HEBREW_CODEPT_LIST)
        ],
        (14, "BOLD"): [
            MergeMember(ARABIC_FONT_BOLD, 13, True, fix_ijam=True),
            MergeMember(HEBREW_FONT_BOLD, 14, False, HEBREW_CODEPT_LIST)
        ],
        (18, None): NOTIFICATION_SET_SM,
        (18, "BOLD"): NOTIFICATION_SET_SM_BOLD,
        # None of these have "condensed" variants.
        (21, "CONDENSED"): [
            MergeMember(ARABIC_FONT, 20, True, fix_ijam=True),
            MergeMember(HEBREW_FONT, 21, False, HEBREW_CODEPT_LIST)
        ],
        # There's no way I can render this font at the correct height without 2-wide strokes
        # Whereas the equivalent system font is 1px.
        # There's no "Light" version of Tahoma. I guess I could hand edit 100+ glyphs.
        # Oh well, readability right?
        # This is also the font used for notifications
        # We generate several variants of the language pack for the different sizes.
        (24, None): notification_size_map[size_shift_key][0],
        (24, "BOLD"): notification_size_map[size_shift_key][1],
        (28, None): NOTIFICATION_SET_LG,
        (28, "BOLD"): NOTIFICATION_SET_LG_BOLD,
        # Thankfully, the Arabic glyph indices between TNR and Tahoma appear to align.
        # Why? No idea, they certainly don't need to.
        # But it saves me from adding yet another layer of indirection in the text shaper.
        (28, "BOLD_SERIF"): [
            MergeMember(ARABIC_FONT_BOLD_SERIF, 24, True),
            MergeMember(HEBREW_FONT_BOLD_SERIF, 26, False, HEBREW_CODEPT_LIST)
        ],
        (30, "BLACK"): [
            MergeMember(ARABIC_FONT_BOLD, 28, True),
            MergeMember(HEBREW_FONT_BOLD, 30, False, HEBREW_CODEPT_LIST)
        ],
        # See above.
        (42, "LIGHT"): [
            MergeMember(ARABIC_FONT, 38, True),
            MergeMember(HEBREW_FONT, 42, False, HEBREW_CODEPT_LIST)
        ],
        (42, "BOLD"): [
            MergeMember(ARABIC_FONT_BOLD, 38, True),
            MergeMember(HEBREW_FONT_BOLD, 42, False, HEBREW_CODEPT_LIST)
        ]
    }

    return TEMPLATES[(size, variant)]

def compose_font(input_pfo_path, subset_key, size_shift_key, output_pfo_path):
    global shaper_result
    input_pfo_name = os.path.basename(input_pfo_path)
    input_split = input_pfo_name.split(".")[0].split("_")
    size = input_split[-1]
    if len(input_split) > 3:
        size = input_split[-2]
        variant = input_split[-1]
    else:
        variant = None

    try:
        size = int(size)
    except ValueError:
        size, variant = variant, size
        size = int(size)

    # Blegh.
    if "SERIF" in input_pfo_name:
        variant += "_SERIF"

    try:
        template = select_template(size, variant, size_shift_key)
    except KeyError:
        print("No template for %s!" % os.path.basename(input_pfo_path))
        return

    # Check the original PFO to see if we need to generate compressed PFOs.
    # The merge tool can't fix this afterwards.
    compressed = False
    with open(input_pfo_path, "rb") as input_pfo_fd:
        pfo_ver = ord(input_pfo_fd.read(1))
        if pfo_ver == 3:
            FEATURE_RLE4 = 0x02
            input_pfo_fd.seek(9)
            features = ord(input_pfo_fd.read(1))
            compressed = features & FEATURE_RLE4

    merge_params = [input_pfo_path]
    tempfiles = []
    for member in template:
        pfo_tf = tempfile.NamedTemporaryFile()
        tempfiles.append(pfo_tf)
        merge_params.append(pfo_tf.name)

        fontgen_params = [
            "python",
            os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "pebblesdk", "fontgen.py"),
            "pfo",
            str(member.size),
            member.ttf_path,
            pfo_tf.name
        ]

        if member.fix_ijam:
            collect_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "bitmaps", os.path.basename(member.ttf_path).split(".")[0], str(member.size))
            dump_dir = os.path.join(collect_dir, "dump")
            if not os.path.exists(dump_dir):
                os.makedirs(dump_dir)
            fontgen_params += [
                "--dump-bitmaps", dump_dir,
                "--collect-bitmaps", collect_dir
            ]

        if compressed:
            fontgen_params += ["--compress", "RLE4"]

        if member.size != size:
            fontgen_params += ["--shift", "%d,%d" % (0, size - member.size)]

        if member.threshold is not None:
            fontgen_params += ["--threshold", "%d" % member.threshold]

        if member.with_shaper:
            if not shaper_result:
                map_tf = tempfile.NamedTemporaryFile()
                labels_tf = tempfile.NamedTemporaryFile()
                subprocess.check_call([
                    "python3",
                    os.path.join(os.path.dirname(os.path.realpath(__file__)), "text_shaper.py"),
                    member.ttf_path,
                    subset_key,
                    map_tf.name,
                    labels_tf.name,
                    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "runtime")
                ])

                shaper_result = ShaperResult(map_tf, labels_tf)
            fontgen_params += ["--map", shaper_result.map_tf.name]
            fontgen_params += ["--codept-labels", shaper_result.labels_tf.name]

        zwc_tf = tempfile.NamedTemporaryFile(mode="w")
        json.dump({"codepoints": ZERO_WIDTH_CODEPOINTS}, zwc_tf)
        zwc_tf.flush()

        cpt_list_tf = None
        if member.codepts:
            cpt_list_tf = tempfile.NamedTemporaryFile(mode="w")
            json.dump({"codepoints": member.codepts}, cpt_list_tf)
            cpt_list_tf.flush()
            fontgen_params += ["--list", cpt_list_tf.name]

        fontgen_params += ["--zero-width-codept-list", zwc_tf.name]

        if member.fix_ijam:
            # We must dump the glyphs first.
            if not glob.glob(os.path.join(dump_dir, "*.txt")):
                try:
                    subprocess.check_call(fontgen_params)
                except subprocess.CalledProcessError:
                    pass
            subprocess.check_call([
                "python",
                os.path.join(os.path.dirname(os.path.realpath(__file__)), "fix_ijam.py"),
                dump_dir,
                collect_dir
            ])

        try:
            subprocess.check_call(fontgen_params)
        except subprocess.CalledProcessError:
            print("Failed generating member for %s - it will not be output!" % input_pfo_name)
            return

    merge_params.append(output_pfo_path)
    subprocess.check_call([
        "python",
        os.path.join(os.path.dirname(os.path.realpath(__file__)), "pfo_merge.py")
    ] + merge_params)

in_dir = sys.argv[1]
subset_key = sys.argv[2]
size_shift_key = sys.argv[3]
out_dir = sys.argv[4]
out_code_dir = sys.argv[5]

# Top quality codegen
code = """// THIS FILE IS AUTOMATICALLY GENERATED
#include "range.h"
#include "font_ranges.h"

bool is_zero_width(uint16_t codept) {
    return %s;
}
""" % " || ".join(("RANGE(codept, %d, %d)" % r for r in ZERO_WIDTH_CODEPOINT_RANGES))
open(os.path.join(out_code_dir, "font_ranges.c"), "w").write(code)

header = """// THIS FILE IS AUTOMATICALLY GENERATED
#pragma once
#include "pebble.h"
bool is_zero_width(uint16_t codept);
#define ZERO_WIDTH_CODEPT %d
""" % ZERO_WIDTH_CODEPOINTS[0]
open(os.path.join(out_code_dir, "font_ranges.h"), "w").write(header)

for in_file in glob.glob(os.path.join(in_dir, "*.pfo")):
    if any(b in in_file for b in blacklist):
        continue
    out_file = os.path.join(out_dir, os.path.basename(in_file))
    compose_font(in_file, subset_key, size_shift_key, out_file)
