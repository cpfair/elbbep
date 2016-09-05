from itertools import chain
import subprocess
import json
import struct
import sys
import os
import unicodedata

# This file generates the code that drives the Arabic text shaper SM.
# It also generates the glyph-codepoint mapping used to produce the Arabic fonts.
# It requires the hb-shape CLI tool.
# NB when generating multiple fonts for the same firmware image, they must have the same glyph mapping.
#  It would be possible to force this - but at time of writing I'm planning on using the same family everywhere.
#  So it's not a problem.

if len(sys.argv) < 6:
    print("text_shaper.py font.ttf subset map_out.json labels_out.json code_out_dir/")
    sys.exit(0)

font_path = sys.argv[1]
subset_key = sys.argv[2]
map_path = sys.argv[3]
labels_path = sys.argv[4]
codegen_path = sys.argv[5]

scratch_codepoint_ranges = ((0x700, 0x750), (0x780, 0x7FF + 1))

kashida = "ـ"

subsets = {
    "full": "ابپتٹثجچحخدڈذرڑزژسشصضطظعغفقكکگلمنوهھءیےټڅځډړږښګڼيېۍئڕێۆەڵڤأإةىؤ" + kashida,
    "arabic": "غظضذخثتشرقصفعسنملكيطحزوهدجباء" + kashida
}
shaped_alphabet = subsets[subset_key]
for ch in shaped_alphabet:
    assert len(ch.encode("utf-8")) == 2, "Alphabet member %s (%x) not encoded in 2 bytes" % (ch, ord(ch))

supplemental_alphabet = "١٢٣٤٥٦٧٨٩٠؟؛،"
ligatures = ["لا"]

missing_glyph = None
def shape_text(txt):
    process = subprocess.Popen(['hb-shape', font_path, '--output-format=json', '--no-glyph-names'], stdout=subprocess.PIPE, stdin=subprocess.PIPE)
    out, err = process.communicate(txt.encode("utf-8"))
    glyphs = list(json.loads(out.decode("utf-8")))
    # Check for missing glyphs
    missing_chars = set()
    for glyph in glyphs:
        if glyph["g"] == missing_glyph:
            missing_char = txt[glyph["cl"]:glyph["cl"]+1]
            missing_chars.add(missing_char)

    if missing_chars:
        raise Exception("The following characters are missing from the font: %s (%s)" % (missing_chars, [hex(ord(x)) for x in missing_chars]))
    return glyphs

missing_glyph = shape_text("ᓄ")[0]["g"]
kashida_glyph = shape_text(kashida)[0]["g"]

def generate_forms(alphabet, ligatures):
    forms = {}
    for ch in [ch for ch in alphabet] + ligatures:
        ch_comps = [ch, ch + kashida, kashida + ch + kashida, kashida + ch]
        ch_forms = []
        for ch_comp in ch_comps:
            if ch == kashida:
                target_glyph = kashida_glyph
            else:
                shaped = shape_text(ch_comp)
                target_glyphs = [x for x in shaped if x["g"] != kashida_glyph]
                assert len(target_glyphs) == 1
                target_glyph = target_glyphs[0]["g"]
            ch_forms.append(target_glyph)
        forms[ch] = ch_forms
    return forms

def pack_lut(forms):
    # LUT is simply repeated <true codept, isolated codept, initialDelta, medialDelta, finalDelta>
    # The runtime automatically detects characters like alef that restart the SM.
    # Ligatures are assigned their own "true" codepoints.
    # The ligature table is of form <prefixn>,...,<prefix0>,<replacement> (where replacement has its MSB set)
    # We recycle some of the dustier blocks in the 2-byte UTF8 range.
    available_codepts = chain(*(range(*p) for p in scratch_codepoint_ranges))
    selected_glyphs = {}
    dirtied_codepts = []
    labels = {}
    lut_data = bytes()
    lig_data = bytes()
    for ch in sorted(list(forms.keys())):
        ch_forms = forms[ch]
        if len(ch) == 1:
            true_codept = ord(ch)
            label_base = unicodedata.name(ch)
        else:
            assert len(ch) <= 2, "Ligature handling supports 2-char patterns only"
            # A ligature - also update the ligature table.
            true_codept = next(available_codepts)
            dirtied_codepts.append(true_codept)
            for c in ch:
                lig_data += struct.pack("<H", ord(c))
            lig_data += struct.pack("<H", true_codept | (1 << 15))
            label_base = "LIG-%s" % ch
        line_parts = [true_codept]
        base_transformed_codept = None
        for idx, glyph in enumerate(ch_forms):
            if glyph not in selected_glyphs:
                selected_glyphs[glyph] = next(available_codepts)
                dirtied_codepts.append(selected_glyphs[glyph])
                labels[selected_glyphs[glyph]] = label_base + "-" + ["ISO", "INI", "MED", "FIN"][idx]
            if not base_transformed_codept:
                base_transformed_codept = selected_glyphs[glyph]
                line_parts.append(selected_glyphs[glyph])
            else:
                line_parts.append(selected_glyphs[glyph] - base_transformed_codept)
        lut_data += struct.pack("<HHbbb", *line_parts)
    return lut_data, lig_data, selected_glyphs, labels, dirtied_codepts

def contiguous_ranges(vals):
    run_range = []
    last_val = None
    for val in sorted(vals):
        if last_val is not None and last_val + 1 != val:
            yield (run_range[0], run_range[-1])
            run_range = []
        run_range.append(val)
        last_val = val
    yield (run_range[0], run_range[-1])

def supplement_selected_glyphs(selected_glyphs, alphabet):
    for ch in alphabet:
        glyph = shape_text(ch)[0]["g"]
        selected_glyphs[glyph] = ord(ch)

def write_lut(lut_data, lig_data, shapable_ranges, out_dir):
    lut_h = open(os.path.join(out_dir, "text_shaper_lut.h"), "w")
    lut_h.write("#include \"pebble.h\"\n#include \"range.h\"\n// THIS FILE IS AUTOMATICALLY GENERATED\n\n")
    lut_c = open(os.path.join(out_dir, "text_shaper_lut.c"), "w")
    lut_c.write("#include \"text_shaper_lut.h\"\n// THIS FILE IS AUTOMATICALLY GENERATED\n\n")
    def write_array(datatype, name, elements):
        lut_h.write("extern %s %s[];\n" % (datatype, name))
        lut_h.write("#define %s_SIZE %d\n" % (name, len(elements)))
        lut_c.write("%s %s[] = {%s};\n" % (datatype, name, ", ".join("0x%x" % x for x in elements)))
    def write_define(name, value):
        lut_h.write("#define %s %s\n" % (name, value))
    # This isn't a real lookup table, since you can't index directly into it.
    # For shame - but doing so would double the memory footprint in exchange for saving a relatively small loop?
    write_array("const uint8_t", "ARABIC_SHAPER_LUT", lut_data)
    write_array("const uint8_t", "ARABIC_LIGATURE_LUT", lig_data)
    write_define("ARABIC_SHAPER_RANGE(cp)", "(%s)" % " || ".join("RANGE(cp, %d, %d)" % (r[0], r[1] + 1) for r in shapable_ranges))

# Get the glyph indices corresponding to the forms of the various letters.
character_forms = generate_forms(shaped_alphabet, ligatures)
# Build the LUT
# This also assigns codepoints to the glyph within the defined ranges
lut_data, lig_data, selected_glyphs, labels, dirtied_codepts = pack_lut(character_forms)
shapable_ranges = contiguous_ranges(dirtied_codepts)
# Add un-shaped codepoints to the font.
supplement_selected_glyphs(selected_glyphs, supplemental_alphabet)
# Write the LUTs.
write_lut(lut_data, lig_data, shapable_ranges, codegen_path)

# Write misc data files used as input to fontgen.
selected_codepts = {v: k for k, v in selected_glyphs.items()}
json.dump(selected_codepts, open(map_path, "w"))
json.dump(labels, open(labels_path, "w"))
