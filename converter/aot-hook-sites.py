#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""
aot-hook-sites.py -- write the hook table from a run of a converted build.

The mod asks for its hooks through A64HookFunction, which the shim answers
(converter/bionic-shim.c). With HUSK_HOOK_TRACE=1 in the environment the shim logs every
request, baked or not, with the site's run-time address; the log also says where libPVZ2's
code was loaded. A site's address in the library is the difference of the two, and that is
all the converter needs (it reads the displaced instruction out of the library itself).

So a new mod build, on an unchanged libPVZ2, needs no translating loader: build with the
previous version's table, run the app once with the trace on, and write the new table:

    HUSK_HOOK_TRACE=1 scripts/test-run.sh --app <app> --seconds 20 --out <run>
    converter/aot-hook-sites.py <run>/run.log --game 1.5.0 --lib <libPVZ2.so> -o data/hooks-1.5.0.json

Then build again and run again: the log must have no "is not baked" line, and a second
table written from that run must equal the first (--check FILE compares instead of writing).

Every site must be the first instruction of a function. --lib checks what can be checked
without unwind tables: the site is in the library's code and the word before it ends a
function (ret, a branch, or a call that does not return).
"""
import argparse
import json
import re
import struct
import sys

BASE = re.compile(r"aot: libPVZ2\.so: .* code (0x[0-9a-f]+)")
ASKED = re.compile(r"^hook: A64HookFunction -> 0x[0-9a-f]+ \(site (0x[0-9a-f]+)", re.M)
MISSED = re.compile(r"^hook: site (0x[0-9a-f]+) is not baked", re.M)


def sites_of(log_path):
    log = open(log_path, errors="replace").read()
    base = BASE.search(log)
    if not base:
        sys.exit(f"{log_path}: no 'aot: libPVZ2.so: ... code 0x...' line; is this a converted build's log?")
    base = int(base.group(1), 16)
    asked = {int(s, 16) - base for s in ASKED.findall(log)}
    missed = {int(s, 16) - base for s in MISSED.findall(log)}
    if not asked:
        sys.exit(f"{log_path}: no hook requests; run the app with HUSK_HOOK_TRACE=1")
    return sorted(asked | missed), sorted(missed)


def check_lib(lib_path, sites):
    elf = open(lib_path, "rb").read()
    phoff = struct.unpack_from("<Q", elf, 0x20)[0]
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x36)
    code = []
    for i in range(phnum):
        kind, flags, off, va, _, fsz, _, _ = struct.unpack_from("<IIQQQQQQ", elf, phoff + i * phentsize)
        if kind == 1 and flags & 1:
            code.append((va, off, fsz))

    def word(v):
        for va, off, fsz in code:
            if va <= v < va + fsz:
                return struct.unpack_from("<I", elf, v - va + off)[0]
        return None

    bad = []
    for s in sites:
        here, before = word(s), word(s - 4)
        ends = before is not None and (
            before == 0xD65F03C0                      # ret
            or before >> 26 in (0x05, 0x25)           # b, bl (a call that does not return)
            or before & 0xFFFFFC1F == 0xD61F0000      # br
            or before >> 16 == 0)                     # padding
        if here is None or s % 4 or not ends:
            bad.append(s)
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--game", help="the game version, for the table's meta section")
    ap.add_argument("--lib", help="libPVZ2.so, to check that every site starts a function")
    ap.add_argument("-o", "--out", help="the table to write")
    ap.add_argument("--check", metavar="FILE", help="compare with this table instead of writing one")
    a = ap.parse_args()

    sites, missed = sites_of(a.log)
    print(f"{len(sites)} hook sites asked for, {len(missed)} of them not baked in this build")
    if a.lib:
        bad = check_lib(a.lib, sites)
        if bad:
            sys.exit("not the start of a function: " + " ".join(hex(s) for s in bad))
        print("every site is in the code and follows the end of a function")
    if a.check:
        have = sorted(h["site"] for h in json.load(open(a.check))["hooks"])
        if have != sites:
            extra, lacking = sorted(set(have) - set(sites)), sorted(set(sites) - set(have))
            sys.exit(f"{a.check} differs: {len(lacking)} site(s) missing from it, {len(extra)} no longer asked for")
        print(f"{a.check} holds exactly these sites")
    elif a.out:
        table = {
            "meta": {
                "game": a.game,
                "library": "libPVZ2.so",
                "site": "the hooked function's address in the library (guest virtual address)",
                "recorded": "converter/aot-hook-sites.py, from a HUSK_HOOK_TRACE=1 run of a converted build",
                "count": len(sites),
            },
            "hooks": [{"site": s} for s in sites],
        }
        with open(a.out, "w") as f:
            json.dump(table, f, indent=1)
            f.write("\n")
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
