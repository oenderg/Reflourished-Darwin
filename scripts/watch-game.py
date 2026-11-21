#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compare the mod's live metadata with the game pin, without downloading game files."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
LIB_URL = "https://reflourished.ducnguyen.org/libcdn/liblatesttimerelease"
OBB_URL = "https://rsb.ducnguyen.org/live/latest.json"
LABEL = "game-update"


def request(url, data=None, token=None):
    headers = {"User-Agent": "Game-update-watcher/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers,
                                 data=None if data is None else json.dumps(data).encode())
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read(2 * 1024 * 1024))


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA-256 in live metadata")
    return value


def changes(game, lib, obb):
    # Validate both feeds before reporting, so partial or broken responses cannot create issues.
    live_lib = digest(lib["sha256_lib"])
    live_obb = digest(obb["full_sha256"])
    if not all(type(v) is int and v > 0 for v in
               (lib["timestamp"], obb["time"], obb["full_size"])):
        raise ValueError("invalid timestamp or size in live metadata")
    if not isinstance(obb["full"], str) or not obb["full"].startswith("https://"):
        raise ValueError("invalid full OBB link")
    candidates = [
        ("Library SHA-256", game["mod_lib"]["sha256"], live_lib),
        ("OBB SHA-256", game["obb_sha256"], live_obb),
        ("OBB size", game["obb_size"], obb["full_size"]),
        ("OBB full URL", game["obb_urls"][0], obb["full"]),
    ]
    return [(name, old, new) for name, old, new in candidates if old != new]


def marker(name, value):
    key = hashlib.sha256(json.dumps([name, value]).encode()).hexdigest()
    return f"<!-- game-update:{key} -->"


def issue(game, changed, lib, obb):
    kinds = []
    if any(name.startswith("Library") for name, _, _ in changed):
        kinds.append("mod library")
    if any(name.startswith("OBB") for name, _, _ in changed):
        kinds.append("OBB data")
    title = f"Game update: {' and '.join(kinds)} changed (pin {game['version']})"
    body = f"The live feeds differ from the Reflourished {game['version']} pin.\n\n"
    body += "| Field | Pinned | Live |\n| --- | --- | --- |\n"
    for name, old, new in changed:
        body += f"| {name} | `{old}` | `{new}` |\n"
    body += (f"\nLibrary metadata: {LIB_URL} (timestamp {lib['timestamp']}).\n"
             f"OBB metadata: {OBB_URL} (time {obb['time']}).\n\n"
             "Follow [Supporting a new Reflourished version](../blob/HEAD/docs/NEW-GAME-VERSION.md):\n\n"
             "1. Compare all four APK library hashes with `libs_sha256` to see what changed.\n"
             "2. Update the game data pin, including OBB links, size and hash. For a library update, "
             "update `mod_lib` and `libs_sha256` and keep the library in the build-inputs draft release. "
             "For a new APK/version, update the version references listed in the guide.\n"
             "3. Record and verify the hook table again if the library changed.\n"
             "4. Find and verify game addresses again if `libPVZ2.so` changed.\n"
             "5. Make a full build and test on Mac and iPhone/iPad as described in the guide.\n\n"
             "These feeds signal library and data updates, not a confirmed new APK version.\n\n")
    body += "\n".join(marker(name, new) for name, _, new in changed)
    return title, body


def publish(repo, token, game, changed, lib, obb):
    api = f"https://api.github.com/repos/{repo}"
    bodies = []
    page = 1
    while True:
        issues = request(f"{api}/issues?state=open&labels={LABEL}&per_page=100&page={page}", token=token)
        bodies.extend(item.get("body") or "" for item in issues if "pull_request" not in item)
        if len(issues) < 100:
            break
        page += 1
    pending = [(name, old, new) for name, old, new in changed
               if not any(marker(name, new) in body for body in bodies)]
    if not pending:
        return
    # Creating an existing label returns 422 and leaves it untouched.
    try:
        request(f"{api}/labels", {"name": LABEL, "color": "d4c5f9"}, token)
    except urllib.error.HTTPError as error:
        if error.code != 422:
            raise
    title, body = issue(game, pending, lib, obb)
    body = body.replace("../blob/HEAD/", f"https://github.com/{repo}/blob/HEAD/")
    created = request(f"{api}/issues", {"title": title, "body": body, "labels": [LABEL]}, token)
    print(f"watch-game: opened {created['html_url']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", type=Path, help="game JSON pin (default: the only data/game-*.json)")
    parser.add_argument("--dry-run", action="store_true", help="print the proposed issue; never contact GitHub")
    args = parser.parse_args()
    if args.pin is None:
        pins = list((ROOT / "data").glob("game-*.json"))
        if len(pins) != 1:
            parser.error("expected one game pin; choose one with --pin")
        args.pin = pins[0]
    game = json.loads(args.pin.read_text())
    try:
        lib = request(LIB_URL)
        obb = request(OBB_URL)
        changed = changes(game, lib, obb)
    except (urllib.error.URLError, OSError, http.client.HTTPException,
            ValueError, KeyError, TypeError) as error:
        print(f"watch-game: live metadata unavailable or invalid ({error}); skipped", file=sys.stderr)
        return
    if args.dry_run:
        if not changed:
            print("watch-game: nothing changed")
        else:
            title, body = issue(game, changed, lib, obb)
            print(f"watch-game: would open one issue (label {LABEL}; duplicates checked on publish)\n{title}\n\n{body}")
    elif changed:
        repo = os.environ["GITHUB_REPOSITORY"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            parser.error("invalid GITHUB_REPOSITORY")
        publish(repo, os.environ["GH_TOKEN"], game, changed, lib, obb)


if __name__ == "__main__":
    main()
