from collections import namedtuple
import sys
import glob
import os
import subprocess
import tempfile
import json
# This file drives the process of generating a single merged font.
# It takes a directory of PFO files (from find_system_fonts.py) and produces a second directory

if len(sys.argv) < 3:
    print("compose.py input_pfo_dir output_pfo_dir")
    sys.exit(0)

MergeMember = namedtuple("MergeMember", "ttf_path size with_shaper codepts threshold")
MergeMember.__new__.__defaults__ = (None,) * len(MergeMember._fields)
ShaperResult = namedtuple("ShaperResult", "map_tf zero_width_codepts")

ARABIC_FONT = "/Library/Fonts/Tahoma.ttf"
ARABIC_FONT_BOLD = "/Library/Fonts/Tahoma Bold.ttf"
ARABIC_FONT_BOLD_SERIF = "/Library/Fonts/Times New Roman Bold.ttf"

HEBREW_FONT = ARABIC_FONT
HEBREW_FONT_BOLD = ARABIC_FONT_BOLD
HEBREW_FONT_BOLD_SERIF = ARABIC_FONT_BOLD_SERIF

HEBREW_CODEPT_LIST = [0x5c0, 0x5c3, 0x5c6] + list(range(0x5d0, 0x5f5))

# The RTL system doesn't support diacritics, especially crazy stacked harakat in Arabic.
# So we silently drop them from rendering by assigning them zero-width that don't break the RTL scheme
# ^ This is all TODO since I just realized the RTL system currently relies on the text shaper output to tell it these codepoints.

blacklist = ("NUMBERS", "SUBSET", "EMOJI")

shaper_result = None

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
        MergeMember(ARABIC_FONT_BOLD, 13, True),
        MergeMember(HEBREW_FONT_BOLD, 14, False, HEBREW_CODEPT_LIST)
    ],
    (18, None): [
        MergeMember(ARABIC_FONT, 15, True),
        MergeMember(HEBREW_FONT, 15, False, HEBREW_CODEPT_LIST)
    ],
    (18, "BOLD"): [
        MergeMember(ARABIC_FONT_BOLD, 14, True),
        MergeMember(HEBREW_FONT_BOLD, 16, False, HEBREW_CODEPT_LIST)
    ],
    # None of these have "condensed" variants.
    (21, "CONDENSED"): [
        MergeMember(ARABIC_FONT, 20, True),
        MergeMember(HEBREW_FONT, 21, False, HEBREW_CODEPT_LIST)
    ],
    # There's no way I can render this font at the correct height without 2-wide strokes
    # Whereas the equivalent system font is 1px.
    # There's no "Light" version of Tahoma. I guess I could hand edit 100+ glyphs.
    # Oh well, readability right?
    (24, None): [
        MergeMember(ARABIC_FONT, 19, True, threshold=100),
        MergeMember(HEBREW_FONT, 19, False, HEBREW_CODEPT_LIST, threshold=100)
    ],
    (24, "BOLD"): [
        MergeMember(ARABIC_FONT_BOLD, 19, True),
        MergeMember(HEBREW_FONT_BOLD, 19, False, HEBREW_CODEPT_LIST)
    ],
    (28, None): [
        MergeMember(ARABIC_FONT, 24, True),
        MergeMember(HEBREW_FONT, 26, False, HEBREW_CODEPT_LIST)
    ],
    (28, "BOLD"): [
        MergeMember(ARABIC_FONT_BOLD, 24, True),
        MergeMember(HEBREW_FONT_BOLD, 26, False, HEBREW_CODEPT_LIST)
    ],
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

def compose_font(input_pfo_path, output_pfo_path):
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
        template = TEMPLATES[(size, variant)]
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

        if compressed:
            fontgen_params += ["--compress", "RLE4"]

        if member.size != size:
            fontgen_params += ["--shift", "%d,%d" % (0, size - member.size)]

        if member.threshold is not None:
            fontgen_params += ["--threshold", "%d" % member.threshold]

        zero_width_codepts = []
        if member.with_shaper:
            if not shaper_result:
                map_tf = tempfile.NamedTemporaryFile()
                zwc_tf = tempfile.NamedTemporaryFile(mode="r")
                subprocess.check_call([
                    "python3",
                    os.path.join(os.path.dirname(os.path.realpath(__file__)), "text_shaper.py"),
                    member.ttf_path,
                    map_tf.name,
                    zwc_tf.name,
                    os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "runtime")
                ])

                shaper_result = ShaperResult(map_tf, json.load(zwc_tf)["codepoints"])
            fontgen_params += ["--map", shaper_result.map_tf.name]
            zero_width_codepts += shaper_result.zero_width_codepts

        zwc_tf = tempfile.NamedTemporaryFile(mode="w")
        json.dump({"codepoints": zero_width_codepts}, zwc_tf)
        zwc_tf.flush()

        cpt_list_tf = None
        if member.codepts:
            cpt_list_tf = tempfile.NamedTemporaryFile(mode="w")
            json.dump({"codepoints": member.codepts}, cpt_list_tf)
            cpt_list_tf.flush()
            fontgen_params += ["--list", cpt_list_tf.name]

        fontgen_params += ["--zero-width-codept-list", zwc_tf.name]
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
out_dir = sys.argv[2]

for in_file in glob.glob(os.path.join(in_dir, "*.pfo")):
    if any(b in in_file for b in blacklist):
        continue
    out_file = os.path.join(out_dir, os.path.basename(in_file))
    compose_font(in_file, out_file)
