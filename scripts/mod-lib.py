#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
mod-lib.py -- put the mod's current library into a copy of the game's APK.

On Android the mod replaces its own library (libRFLib.so) after the APK is installed: it
downloads a newer one from its server and loads that instead (docs/GAME-NOTES.md, "The
mod's own updaters"). Our apps do not, so a build takes the library a release pins
(data/game-<version>.json, "mod_lib") and puts it where the APK's own was. Everything
after that step sees an ordinary APK.

    scripts/mod-lib.py <the game's APK> <the APK to write> [--lib FILE]

--lib is the library file. Without it the script downloads the mod's link, which only ever
serves the newest library: once the mod has moved on, the pinned one has to come from a
kept copy (CI keeps one in the build-inputs draft release, docs/RELEASING.md).

An APK that already holds the pinned library is copied as it is.
"""
import argparse
import hashlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY = "lib/arm64-v8a/libRFLib.so"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("apk")
    ap.add_argument("out")
    ap.add_argument("--lib", help="the pinned library file (default: download the mod's link)")
    a = ap.parse_args()

    games = sorted((ROOT / "data").glob("game-*.json"))
    game = json.loads(games[0].read_text())
    pin = game.get("mod_lib")
    src = zipfile.ZipFile(a.apk)
    have = sha256(src.read(ENTRY))
    if not pin or have == pin["sha256"]:
        shutil.copyfile(a.apk, a.out)
        print(f"mod-lib: {a.apk} already holds the library this version pins")
        return
    if have != pin["apk_sha256"]:
        sys.exit(f"mod-lib: this APK's libRFLib.so is not the one Reflourished {game['version']} ships")

    if a.lib:
        lib = pathlib.Path(a.lib).read_bytes()
    else:
        print(f"mod-lib: downloading {pin['url']}")
        got = subprocess.run(["curl", "-fsSL", "--retry", "3", pin["url"]], stdout=subprocess.PIPE, check=True)
        tar = tarfile.open(fileobj=io.BytesIO(got.stdout))
        lib = tar.extractfile(pin["member"]).read()
    if sha256(lib) != pin["sha256"]:
        where = a.lib or "the mod's link"
        sys.exit(f"mod-lib: {where} is not the library this version pins ({pin['version']}).\n"
                 "The mod has most likely published a newer one: see docs/NEW-GAME-VERSION.md,\n"
                 "or pass a kept copy of the pinned one with --lib.")

    with zipfile.ZipFile(a.out, "w") as dst:
        for info in src.infolist():
            data = lib if info.filename == ENTRY else src.read(info.filename)
            dst.writestr(info, data, compress_type=info.compress_type)
    print(f"mod-lib: wrote {a.out} with libRFLib.so {pin['version']}")


if __name__ == "__main__":
    main()
