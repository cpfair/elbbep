from __future__ import division
import sys
import os
import glob
import json
# This file automatically fixes the consonant pointing marks to be more visible
# (out of the box they're single pixels - even when the rest of the font is thicker)
if len(sys.argv) < 3:
    print("fix_ijam.py in_bitmap_dump out_bitmap_dump")
    sys.exit(0)

in_dir = sys.argv[1]
out_dir = sys.argv[2]

def process_glyph(in_path, out_path):
    bmp_data = {}
    width = height = bottom = left = None
    with open(in_path, "r") as fd:
        meta = json.loads(fd.readline())
        width = meta["width"]
        height = meta["height"]
        bottom = meta["bottom"]
        left = meta["left"]
        advance = meta["advance"]
        bmp_str = fd.read().replace("\n", "")
        assert len(bmp_str) == width * height
        for y in range(height):
            for x in range(width):
                bmp_data[(x, y)] = bmp_str[y * width + x] == "#"

    # Find isolated points - the i'jam.
    def check_isolated(x, y, excepting=None):
        adj_cells = (
            (x - 1, y),
            (x + 1, y),
            (x - 1, y - 1),
            (x, y - 1),
            (x + 1, y - 1),
            (x - 1, y + 1),
            (x, y + 1),
            (x + 1, y + 1)
        )
        for adj in adj_cells:
            if excepting and adj in excepting:
                continue
            try:
                if bmp_data[adj]:
                    return False
            except KeyError:
                pass
        return True
    other_points = []
    isolated_points = []
    for x in range(width):
        for y in range(height):
            if not bmp_data[(x, y)]:
                continue
            if check_isolated(x, y):
                isolated_points.append((x, y))
            else:
                other_points.append((x, y))
    if not isolated_points:
        return

    # Turn them from 1x1 squares to 2x2
    # And do so in a direction least likely to produce collisions.
    isolated_centroid = (sum(p[0] for p in isolated_points) / len(isolated_points), sum(p[1] for p in isolated_points) / len(isolated_points))
    other_centroid = (sum(p[0] for p in other_points) / len(other_points), sum(p[1] for p in other_points) / len(other_points))

    hit_left = False
    hit_right = False
    ijam_bmp_data = {}
    for x, y in isolated_points:
        if x == isolated_centroid[0]:
            sx = -1 if x < other_centroid[0] else 1
        else:
            sx = -1 if x < isolated_centroid[0] else 1
        if y == isolated_centroid[1]:
            sy = -1 if y < other_centroid[1] else 1
        else:
            sy = -1 if y < isolated_centroid[1] else 1

        ijam_bmp_data[x, y] = True
        ijam_bmp_data[x, y + sy] = True
        ijam_bmp_data[x + sx, y + sy] = True
        ijam_bmp_data[x + sx, y] = True
        if x <= 0 or x + sx <= 0:
            hit_left = True
        if x >= width - 1 or x + sx >= width - 1:
            hit_right = True


    if hit_left or hit_right:
        hit_left = hit_right = False
        # Centre horizontally within the greater glyph
        min_iso_x = min((k[0] for k in ijam_bmp_data))
        max_iso_x = max((k[0] for k in ijam_bmp_data))
        min_oth_x = min((k[0] for k in other_points))
        max_oth_x = max((k[0] for k in other_points))
        off_x = int(round((max_oth_x - min_oth_x) / 2 - (max_iso_x - min_iso_x) / 2))
        new_ijam_bmp_data = {}
        for k, v in ijam_bmp_data.items():
            new_ijam_bmp_data[(k[0] - off_x, k[1])] = v
            if k[0] - off_x <= 0:
                hit_left = True
            if k[0] - off_x >= width:
                hit_right = True
        ijam_bmp_data = new_ijam_bmp_data

    # Expand bitmap as appropriate
    sx = sy = 0
    hdx = hdy = 0
    dl = db = 0
    for x, y in ijam_bmp_data.keys():
        if x < 0:
            sx = max(-x, sx)
        if y < 0:
            sy = max(-y, sy)
        if x >= width:
            hdx = max(x - width + 1, hdx)
        if y >= height:
            hdy = max(y - height + 1, hdy)
    if hit_left:
        sx += 1
        dl -= 1
    advance += sx + hdx
    left += sx + dl
    width += sx + hdx
    bottom += sy + db
    height += sy + hdy

    # Rebuild bitmap
    # We shift over the main body of the glyph to better centre the thickened marks.
    bmp_data = ijam_bmp_data
    for x, y in other_points:
        bmp_data[(x + hdx, y)] = True
        if x == 0:
            for xoff in range(hdx + sx):
                bmp_data[(x + xoff - sx, y)] = True

    with open(out_path, "w") as fd:
        meta["width"] = width
        meta["height"] = height
        meta["left"] = left
        meta["bottom"] = bottom
        meta["advance"] = advance
        fd.write("%s\n" % json.dumps(meta))
        for y in range(height):
            for x in range(width):
                try:
                    fd.write("#" if bmp_data[(x - sx, y - sy)] else " ")
                except KeyError:
                    fd.write(" ")
            fd.write("\n")

for file in glob.glob(os.path.join(in_dir, "*ARABIC*")):
    process_glyph(file, os.path.join(out_dir, os.path.basename(file)))

