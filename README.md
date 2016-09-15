elbbeP
======
Arabic and Hebrew for the Pebble Smartwatch.

Out of the box, Pebble can't display any right-to-left language properly, for three reasons:

* The system fonts have no glyphs for RTL languages like Hebrew and Arabic.
* The text renderer has no concept of RTL - so even if you install custom fonts, the text comes out backwards.
* In the case of Arabic script, the renderer does not attempt any contextual text-shaping, so you get س ل ا م instead of سلام.

This project fixes all three issues through a custom firmware image and language pack, allowing Hebrew, Arabic, and other languages written with the Arabic script to be displayed.


Requirements
------------

* Python 2.x *and* 3.x
* The GCC toolchain for ARM (`arm-none-eabi-...`)
    * On Ubuntu run `sudo apt-get install binutils-arm-none-eabi gcc-arm-none-eabi`
* `hb-shape` command-line tool
    * On Ubuntu run `sudo apt-get install libharfbuzz-bin`
* A Mac with the Tahoma and Times New Roman type families installed (or modify the shamelessly-hardcoded paths in `fonts/compose.py`)
* 1700-1900 bytes of free space in the target platform's firmware image - the generator will fail if not enough free space is available.

Usage
-----

Run `generator.py <hw_rev> <out.pbz> [<out.pbl>]` to generate a firmware package `out.pbz` for the specified Pebble `hw_rev`, and (optionally) an accompanying language pack `out.pbl`.

This tool automatically downloads parts of the Pebble Developer SDK, so its use requires agreement to the Pebble Developer [Terms of Use](https://developer.getpebble.com/legal/terms-of-use) and [SDK License Agreement](https://developer.getpebble.com/legal/sdk-license).

To perform steps of the process individually, use `patch.py`, `fonts/compose.py`, `fonts/pfo_merge.py`, `fonts/text_shaper.py`, and `fonts/fix_ijam.py`.
