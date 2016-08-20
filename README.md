elbbeP
======
Arabic and Hebrew for the Pebble Smartwatch.

Out of the box, Pebble can't display any right-to-left language properly, for three reasons:

* The system fonts have no glyphs for RTL languages like Hebrew and Arabic.
* The text renderer has no concept of RTL - so even if you install custom fonts, the text comes out backwards.
* In the case of Arabic script, the renderer does not attempt any contextual text-shaping.

This project fixes all three issues through a custom firmware image and language pack, allowing Hebrew, Arabic, and other languages written with the Arabic script to be displayed.


Requirements
------------

* Python 2.x *and* 3.x
* `hb-shape` command-line tool
* A Mac with the Tahoma and Times New Roman type families installed (or modify the shamelessly-hardcoded paths in `fonts/compose.py`)

Usage
-----

Run `generator.py <hw_rev> <out.pbz> [<out.pbl>]` to generate a firmware package `out.pbz` for the specified Pebble `hw_rev`, and (optionally) an accompanying language pack `out.pbl`. Language packs are not hardware-specific.

This tool automatically downloads parts of the Pebble Developer SDK, so its use requires agreement to the Pebble Developer [Terms of Use](https://developer.getpebble.com/legal/terms-of-use) and [SDK License Agreement](https://developer.getpebble.com/legal/sdk-license).

To perform individual steps of the generation process individually, use `patch.py`, `fonts/compose.py`, `fonts/pfo_merge.py`, and `fonts/text_shaper.py`.
