import glob
import os
import requests
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import json
from pebblesdk.stm32_crc import crc32

# This is the script that
# - automatically fetches all the resources required
# - patches the firmware
# - builds new fonts
# - packs a new firmware image

MAX_RESOURCES = 512

if len(sys.argv) < 4:
    print("generator.py hw_rev out.pbz langpack_out.pbl")
    sys.exit(0)

cache_root = "cache"
firmware_series = "v3.8" # IDK.
hw_rev = sys.argv[1]
out_pbz_path = sys.argv[2]
out_pbl_path = sys.argv[3]

hw_rev_platform_map = {
    "ev2_4": "aplite",
    "v1_5": "aplite",
    "v2_0": "aplite",
    "snowy_dvt": "basalt",
    "snowy_s3": "basalt",
    "spalding": "chalk"
}

def cache_path(ns, k):
    if not os.path.exists(cache_root):
        os.mkdir(cache_root)
    if not os.path.exists(os.path.join(cache_root, ns)):
        os.mkdir(os.path.join(cache_root, ns))
    return os.path.join(cache_root, ns, k)

def download_firmware(series, hw_rev):
    fw_manifest = requests.get("http://pebblefw.s3.amazonaws.com/pebble/%s/release-%s/latest.json" % (hw_rev, series)).json()
    ver = fw_manifest["normal"]["friendlyVersion"]
    url = fw_manifest["normal"]["url"]
    pbz_path = cache_path("stock-firmware", "%s-%s.pbz" % (hw_rev, ver))
    if not os.path.exists(pbz_path):
        open(pbz_path, "wb").write(requests.get(url).content)
    return ver, pbz_path

def download_sdk(fw_ver):
    fw_ver = fw_ver.strip("v")
    sdk_list = requests.get("http://sdk.getpebble.com/v1/files/sdk-core?channel=release").json()
    versions = set([x["version"] for x in sdk_list["files"]])
    if fw_ver not in versions:
        fw_ver, _, _ = fw_ver.rpartition(".")

    zip_path = cache_path("sdk-zip", "%s.tar.bz2" % fw_ver)
    if not os.path.exists(zip_path):
        sdk_manifest = requests.get("http://sdk.getpebble.com/v1/files/sdk-core/%s?channel=release" % fw_ver).json()
        print(sdk_manifest)
        url = sdk_manifest["url"]
        open(zip_path, "wb").write(requests.get(url).content)

    unpacked_path = cache_path("sdk", "%s" % fw_ver)
    if not os.path.exists(unpacked_path):
        os.mkdir(unpacked_path)
        tf = tarfile.open(zip_path)
        tf.extractall(unpacked_path)
    return unpacked_path

def unpack_fw(fw_ver, hw_rev, pbz_path):
    unpacked_path = cache_path("unpacked-firmware", "%s-%s" % (fw_ver, hw_rev))
    if not os.path.exists(unpacked_path):
        zf = zipfile.ZipFile(pbz_path)
        zf.extractall(unpacked_path)
    return unpacked_path

def unpack_resources(pbpack_path):
    unpacked_path, _, _ = pbpack_path.rpartition(".")
    if not os.path.exists(unpacked_path) or True:
        # os.mkdir(unpacked_path)
        pbpack_fd = open(pbpack_path, "rb")
        OFFSET_TABLE_OFFSET = 0xC
        n_resources = struct.unpack("<I", pbpack_fd.read(4))[0]
        pbpack_fd.seek(OFFSET_TABLE_OFFSET)
        resources = []
        for i in range(n_resources):
            resid, offset, size, crc = struct.unpack("<IIII", pbpack_fd.read(16))
            resources.append((resid, offset, size))
        res_base = OFFSET_TABLE_OFFSET + MAX_RESOURCES * 16
        for resid, offset, size in resources:
            pbpack_fd.seek(res_base + offset)
            open(os.path.join(unpacked_path, "%03d" % resid), "wb").write(pbpack_fd.read(size))
    return unpacked_path

def pack_resources(resmap, out_pbpack_path, max_resources=MAX_RESOURCES):
    resources = sorted(list(resmap.items()), key=lambda x: x[0])

    data_offset_map = {}
    resource_table = b""
    resource_data = b""
    for resid, path in resources:
        if path:
            data = open(path, "rb").read()
        else:
            data = b''
        # Deduplicate resources.
        try:
            offset = data_offset_map[data]
        except KeyError:
            offset = len(resource_data)
            resource_data += data
            data_offset_map[data] = offset

        resource_table += struct.pack("<IIII", resid, offset, len(data), crc32(data))

    for x in range(max_resources - len(resources)):
        resource_table += b'\0' * 16
    assert len(resource_table) == max_resources * 16

    pack_header = struct.pack("<III", len(resources), crc32(resource_data), 0)

    repacked_fd = open(out_pbpack_path, "wb")
    repacked_fd.write(pack_header)
    repacked_fd.write(resource_table)
    repacked_fd.write(resource_data)

def extract_fonts(fw_dir):
    bin_path = os.path.join(fw_dir, "tintin_fw.bin")
    res_path = unpack_resources(os.path.join(fw_dir, "system_resources.pbpack"))
    unpacked_path = os.path.join(fw_dir, "system_fonts")
    if not os.path.exists(unpacked_path):
        os.mkdir(unpacked_path)
        subprocess.check_call([
            "python",
            "fonts/find_system_fonts.py",
            bin_path,
            res_path,
            unpacked_path])
    return unpacked_path

def generate_fonts(original_fonts_path):
    new_fonts_path = os.path.join(os.path.dirname(original_fonts_path), "generated_fonts")
    if not os.path.exists(new_fonts_path):
        os.mkdir(new_fonts_path)
        subprocess.check_call([
            "python",
            "fonts/compose.py",
            original_fonts_path,
            new_fonts_path,
            "runtime/"])
    return new_fonts_path

def generate_langpack(fonts_dir, out_pbl_path):
    # A language pack is just a pbpack with pre-determined resource IDs
    # 0 is the translation MO, which we don't have.
    # The balance are font PFOs.
    # See https://forums.pebble.com/t/something-about-language-pack-file/14052
    font_seq = ["GOTHIC_14", "GOTHIC_14_BOLD", "GOTHIC_18", "GOTHIC_18_BOLD", "GOTHIC_24", "GOTHIC_24_BOLD", "GOTHIC_28", "GOTHIC_28_BOLD", "BITHAM_30_BLACK", "BITHAM_42_BOLD", "BITHAM_42_LIGHT", "BITHAM_42_MEDIUM_NUMBERS", "BITHAM_34_MEDIUM_NUMBERS", "BITHAM_34_LIGHT_SUBSET", "BITHAM_18_LIGHT_SUBSET", "ROBOTO_CONDENSED_21", "ROBOTO_BOLD_SUBSET_49", "DROID_SERIF_28_BOLD"]
    # First, generate a stub PO file.
    # It still works without I think - but this lets me customize the display text on the watch.
    po_file = r"""msgid ""
msgstr ""
"MIME-Version: 1.0\n"
"Content-Type: text/plain; charset=UTF-8\n"
"Content-Transfer-Encoding: 8bit\n"
"X-Generator: POEditor.com\n"
"Project-Id-Version: 1.0\n"
"Language: en_US+\n"
"Name: Hebrew + Arabic\n"
"""
    mo_tf = tempfile.NamedTemporaryFile()
    with tempfile.NamedTemporaryFile(mode="w") as po_tf:
        po_tf.write(po_file)
        po_tf.flush()
        subprocess.check_call(["msgfmt", po_tf.name, "-o", mo_tf.name])

    resmap = {
        1: mo_tf.name
    }
    for resid_off, font in enumerate(font_seq):
        new_pfo_match = glob.glob(os.path.join(fonts_dir, "*%s*" % font))
        if new_pfo_match:
            path = new_pfo_match[0]
        else:
            path = None
        resmap[resid_off + 2] = path
    pack_resources(resmap, out_pbl_path, max_resources=256)

def patch_firmware(target_bin, sdk_dir, hw_rev):
    platform = hw_rev_platform_map[hw_rev]
    out_bin = target_bin.replace(".bin", ".patched.bin")
    if not os.path.exists(out_bin):
        libpebble_a_path = os.path.join(sdk_dir, "sdk-core", "pebble", platform, "lib", "libpebble.a")
        qemu_bin_path = os.path.join(sdk_dir, "sdk-core", "pebble", platform, "qemu", "qemu_micro_flash.bin")
        subprocess.check_call([
            "python",
            "patch.py",
            platform,
            target_bin,
            libpebble_a_path,
            out_bin])
    return out_bin

def tag_version(fw_ver, fw_bin):
    ver_string_loc = fw_bin.index(fw_ver.encode("ascii"))
    new_ver_string = fw_ver.encode("ascii") + b"-RTL"
    res = fw_bin[:ver_string_loc] + new_ver_string + fw_bin[ver_string_loc + len(new_ver_string):]
    return res

def pack_firmware(fw_ver, fw_dir, new_bin_path, out_pbz_path):
    misc_fw_files = [
        "LICENSE.txt",
        "layouts.json.auto",
        "system_resources.pbpack"
    ]

    if os.path.exists(out_pbz_path):
        os.remove(out_pbz_path)
    manifest = json.load(open(os.path.join(fw_dir, "manifest.json")))
    fw_bin = open(new_bin_path, "r").read()
    fw_bin = tag_version(fw_ver, fw_bin)
    manifest["firmware"]["size"] = len(fw_bin)
    manifest["firmware"]["crc"] = crc32(fw_bin)
    pbz_zf = zipfile.ZipFile(out_pbz_path, "w")
    pbz_zf.writestr("tintin_fw.bin", fw_bin)
    pbz_zf.writestr("manifest.json", json.dumps(manifest))
    for file in misc_fw_files:
        pbz_zf.write(os.path.join(fw_dir, file), file)


fw_ver, orig_pbz_path = download_firmware(firmware_series, hw_rev)
fw_dir = unpack_fw(fw_ver, hw_rev, orig_pbz_path)
sdk_dir = download_sdk(fw_ver)
new_resources_dir = generate_fonts(extract_fonts(fw_dir))
generate_langpack(new_resources_dir, out_pbl_path)
patched_bin = patch_firmware(os.path.join(fw_dir, "tintin_fw.bin"), sdk_dir, hw_rev)
pack_firmware(fw_ver, fw_dir, patched_bin, out_pbz_path)
