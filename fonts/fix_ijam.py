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

    for x, y in isolated_points:
        if x == isolated_centroid[0]:
            sx = -1 if x < other_centroid[0] else 1
        else:
            sx = -1 if x < isolated_centroid[0] else 1
        if y == isolated_centroid[1]:
            sy = -1 if y < other_centroid[1] else 1
        else:
            sy = -1 if y < isolated_centroid[1] else 1

        bmp_data[x, y + sy] = True
        bmp_data[x + sx, y + sy] = True
        bmp_data[x + sx, y] = True

    # Expand bitmap as appropriate
    sx = sy = 0
    hdx = hdy = 0
    for x, y in bmp_data.keys():
        if x < 0:
            sx = max(-x, sx)
        if y < 0:
            sy = max(-y, sy)
        if x >= width:
            hdx = max(x - width + 1, hdx)
        if y >= height:
            hdy = max(y - height + 1, hdy)
    left -= sx
    width += sx + hdx
    bottom -= sy
    height += sy + hdy

    with open(out_path, "w") as fd:
        meta["width"] = width
        meta["height"] = height
        meta["left"] = left
        meta["bottom"] = bottom
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

