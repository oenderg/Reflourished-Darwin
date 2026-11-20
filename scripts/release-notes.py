#!/usr/bin/env python3
"""Put a release's notes together (release-notes/README.md says what goes in them).

  scripts/release-notes.py <release-dir> --version <v> --tag <tag> [--since <ref>] [--repo <owner/name>]
  scripts/release-notes.py --assets <json> --version <v> --tag <tag> [...]
  scripts/release-notes.py --whats-new --version <v> [--format md|text|html]
  scripts/release-notes.py --updates --version <v>
  scripts/release-notes.py --check-updates --version <v> --tag <tag> [--since <ref>]

The first form prints the release page's Markdown: release-notes/<version>.md as
What's new, the feat/fix/perf commits since the previous tag (or --since) folded
under All changes, and release-notes/footer.md with a table of the files in the
release directory. With --assets the table lists a published release's files
instead, from `gh release view <tag> --json assets` saved to a file: the release
workflow uses it once the Windows files join the Apple ones. The last form prints
only What's new: as plain text for the AltStore / SideStore changelog, or as HTML
for the Mac update window.

An "Updates: Windows" line in the notes names the apps the release changes (iPhone and
iPad, Mac, Windows, comma-separated; no line means all of them). Only those apps' update
feeds move to the release; the others stay on the release before, so nobody is told about
an update that changes nothing for them. The line is never shown. --updates prints the
apps as ios, mac and windows; --check-updates refuses notes that leave out an app whose
own files changed since the previous release.
Python 3 standard library only.
"""

import argparse
import html
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTES = os.path.join(ROOT, "release-notes")
GROUPS = [("feat", "Features"), ("fix", "Fixes"), ("perf", "Performance")]
TITLE = re.compile(r"^(\w+)(?:\(([^)]+)\))?!?: (.+)$")
# What each release file is, by the end of its name
KINDS = [("-iOS.ipa", "iPhone / iPad"), ("-iOS-Full.ipa", "iPhone / iPad, game data included"),
         ("-Mac.dmg", "Mac (Apple silicon)"), ("-Mac-Full.dmg", "Mac, game data included"),
         ("-Windows-Setup.exe", "Windows (Intel/AMD and ARM)"),
         ("-Windows-x64.zip", "Windows Intel/AMD, portable"),
         ("-Windows-ARM64.zip", "Windows on ARM, portable")]
# The one-click line at the top: the file most people want on each platform
QUICK = [("-iOS.ipa", "iPhone / iPad"), ("-Mac.dmg", "Mac"), ("-Windows-Setup.exe", "Windows")]


# The apps a release can update, the names the Updates line may use for each, and the files
# only that app is built from (a change anywhere else may change every app).
APPS = [("ios", "iPhone and iPad", ("iphone and ipad", "iphone/ipad", "iphone", "ipad", "ios"),
         ("runtime/tools/pvz2-ios", "runtime/scripts/build_angle_ios.sh", "prebuilt/ios/",
          "converter/build-ios-dylibs.sh", "runtime/tools/regress/build-ios-")),
        ("mac", "Mac", ("mac", "macos"),
         ("runtime/tools/pvz2-macos", "runtime/tools/macos-icon.swift", "runtime/tools/pvz2-discord-macos.c",
          "converter/build-macos-dylibs.sh", "runtime/tools/regress/build-macos-app.sh")),
        ("windows", "Windows", ("windows", "pc"),
         ("runtime/win/", "runtime/tools/pvz2-windows", "runtime/tools/pvz2-discord-windows.c",
          "prebuilt/win-", "prebuilt/winsparkle/"))]
UPDATES = re.compile(r"^updates:(.*)$", re.I | re.M)


def notes_file(version):
    path = os.path.join(NOTES, f"{version}.md")
    if not os.path.isfile(path):
        sys.exit(f"no release notes for {version}: write {os.path.relpath(path, ROOT)} first "
                 "(release-notes/README.md)")
    return open(path).read()


def whats_new(version):
    return UPDATES.sub("", notes_file(version)).strip()


def updates(version):
    """The apps the release updates, as keys of APPS: the Updates line's, or all of them."""
    lines = UPDATES.findall(notes_file(version))
    if not lines:
        return [key for key, *_ in APPS]
    if len(lines) > 1:
        sys.exit(f"release-notes/{version}.md has {len(lines)} Updates lines; keep one")
    named = set()
    for word in (w.strip().lower() for w in lines[0].split(",")):
        if word == "all":
            named.update(key for key, *_ in APPS)
            continue
        key = next((key for key, _, names, _ in APPS if word in names), None)
        if not key:
            sys.exit(f"release-notes/{version}.md: Updates names \"{word}\"; use iPhone and iPad, Mac, "
                     "Windows or All, comma-separated")
        named.add(key)
    if not named:
        sys.exit(f"release-notes/{version}.md: the Updates line names no app")
    return [key for key, *_ in APPS if key in named]


def check_updates(version, tag, since):
    """Refuse notes that leave out an app whose own files changed since the previous release."""
    apps = updates(version)
    if not since:
        since = git("describe", "--tags", "--abbrev=0", "--match", "v*", "--exclude", tag, "HEAD")
    if not since:
        print("release-notes: no earlier release, every app is updated")
        return
    changed = git("diff", "--name-only", f"{since}..HEAD").splitlines()
    problems = []
    for key, name, _, own in APPS:
        if key in apps:
            continue
        mine = [f for f in changed if f.startswith(own) and not f.endswith(".md")]
        if mine:
            problems.append(f"{name} is left out, but its own files changed since {since}: "
                            + ", ".join(mine[:5]) + (" ..." if len(mine) > 5 else ""))
    if problems:
        sys.exit(f"release-notes/{version}.md: " + "; ".join(problems)
                 + ". Add the app to the Updates line, or take the line out to update every app.")
    names = [name for key, name, *_ in APPS if key in apps]
    print(f"release-notes: {version} updates {', '.join(names)} (checked against {since})")


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True).stdout.strip()


def all_changes(tag, since, repo):
    """The feat/fix/perf commits since the previous tag, grouped; None for the first release."""
    if not since:
        since = git("describe", "--tags", "--abbrev=0", "--exclude", tag, "HEAD")
    if not since:
        return None
    groups = {kind: [] for kind, _ in GROUPS}
    for line in git("log", "--no-merges", "--format=%h\t%s", f"{since}..HEAD").splitlines():
        sha, subject = line.split("\t", 1)
        m = TITLE.match(subject)
        if m and m.group(1) in groups:
            scope = f"{m.group(2)}: " if m.group(2) else ""
            groups[m.group(1)].append(f"- {scope}{m.group(3)} ({sha})")
    parts = [f"**{name}**\n" + "\n".join(groups[kind]) for kind, name in GROUPS if groups[kind]]
    parts.append(f"**Full changelog:** [{since}...{tag}](https://github.com/{repo}/compare/{since}...{tag})")
    return "<details>\n<summary>All changes</summary>\n\n" + "\n\n".join(parts) + "\n\n</details>"


def size(n):
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{round(n / 1e6)} MB"


def release_files(args):
    """The release's files as (name, bytes): a local folder's, or a published release's."""
    if args.assets:
        import json
        return [(a["name"], a["size"]) for a in json.load(open(args.assets))["assets"]]
    return [(n, os.path.getsize(os.path.join(args.release_dir, n))) for n in os.listdir(args.release_dir)]


def downloads(files):
    rows = []
    for suffix, what in KINDS:
        for name, n in sorted(files):
            if name.endswith(suffix):
                rows.append(f"| {what} | `{name}` | {size(n)} |")
    if not rows:
        sys.exit("no release files (IPA, DMG or Windows setup) to list")
    return "| Device | File | Size |\n|---|---|---|\n" + "\n".join(rows)


def quick_links(files, tag, repo):
    """One line of direct download links, one per platform the release has a file for."""
    links = []
    for suffix, what in QUICK:
        for name, _ in sorted(files):
            if name.endswith(suffix):
                links.append(f"[{what}](https://github.com/{repo}/releases/download/{tag}/{name})")
                break
    return "**Download:** " + " · ".join(links) if links else None


def page(args):
    files = release_files(args)
    footer = open(os.path.join(NOTES, "footer.md")).read().strip()
    footer = footer.replace("{downloads}", downloads(files)).replace("{repo}", args.repo)
    quick = quick_links(files, args.tag, args.repo)
    parts = ([quick] if quick else []) + ["## What's new", whats_new(args.version)]
    changes = all_changes(args.tag, args.since, args.repo)
    if changes:
        parts.append(changes)
    parts.append(footer)
    return "\n\n".join(parts) + "\n"


def inline_html(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return re.sub(r"\[(.+?)\]\((https://[^)\s]+)\)", r'<a href="\2">\1</a>', text)


def as_html(md):
    """What's new as the simple HTML the Mac update window shows: paragraphs and bullet lists."""
    out, items = [], []
    for block in re.split(r"\n\s*\n", md):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if lines and all(l.startswith("- ") for l in lines):
            out.append("<ul>" + "".join(f"<li>{inline_html(l[2:])}</li>" for l in lines) + "</ul>")
        elif lines:
            out.append(f"<p>{inline_html(' '.join(lines))}</p>")
    return "".join(out)


def as_text(md):
    """What's new as plain text (AltStore shows the changelog without Markdown)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", md)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\((\S+?)\)", r"\1 (\2)", text)
    return re.sub(r"^- ", "• ", text, flags=re.M)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("release_dir", nargs="?")
    p.add_argument("--assets", help="gh release view --json assets output, in place of a release directory")
    p.add_argument("--version", required=True)
    p.add_argument("--tag")
    p.add_argument("--since", help="the previous release's tag or commit (default: the newest tag before --tag)")
    p.add_argument("--repo", default="oenderg/Reflourished-Darwin")
    p.add_argument("--whats-new", action="store_true")
    p.add_argument("--format", choices=["md", "text", "html"], default="md")
    p.add_argument("--updates", action="store_true", help="print the apps the release updates")
    p.add_argument("--check-updates", action="store_true",
                   help="check the Updates line against the files changed since the previous release")
    args = p.parse_args()
    if args.updates:
        print(" ".join(updates(args.version)))
        return
    if args.check_updates:
        if not args.tag:
            p.error("--check-updates needs --tag")
        check_updates(args.version, args.tag, args.since)
        return
    if args.whats_new:
        md = whats_new(args.version)
        print({"md": md, "text": as_text(md), "html": as_html(md)}[args.format])
        return
    if not ((args.release_dir or args.assets) and args.tag):
        p.error("a release directory (or --assets) and --tag are needed")
    sys.stdout.write(page(args))


if __name__ == "__main__":
    main()
