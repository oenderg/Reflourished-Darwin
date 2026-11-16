# Supporting a new Reflourished version

The apps support exactly one build of the game's code at a time. A new Reflourished release can
change any of the four libraries; how much work it is depends on which ones changed.

## 1. See what changed

### Scheduled update watcher

`.github/workflows/watch-game.yml` checks every six hours and can be run manually. It reads
only the small `liblatesttimerelease` and `live/latest.json` feeds linked below, comparing the
library SHA-256 and the OBB hash, size and full URL with the sole `data/game-*.json` pin.
It opens one `game-update` issue for changed values not already reported in an open labeled
issue. Keep the label and hidden identity markers for deduplication. Unreachable or malformed
feeds are logged and skipped. No game files are downloaded and no pins are changed.

Run `python3 scripts/watch-game.py --dry-run` locally, or add `--pin path/to/copy.json` to test
another pin without editing the repository. Dry runs never contact GitHub. Normal unchanged
runs are silent. The workflow grants only `issues: write` and serializes runs to avoid duplicates.
There is no separate APK/version signal in these feeds: they detect new releases when their
library or OBB changes, but an APK-only release with identical live data needs a manual check.

### Compare the libraries

```sh
unzip -o -q new.apk 'lib/arm64-v8a/*.so' -d new
shasum -a 256 new/lib/arm64-v8a/*.so
```

Compare with `libs_sha256` in the current `data/game-<version>.json`.

- **Only `libRFLib.so` changed** (the mod's own code; `libPVZ2.so` was identical from 1.4.0 to
  1.5.0): the driver's game addresses still hold. Write the hook table again (step 3).
- **`libPVZ2.so` changed**: every `GUEST_*` address in `husk-tl-pvz2.c` and every offset in
  [GAME-NOTES.md](GAME-NOTES.md) must be found again (step 4), and the hooks re-recorded.
- **The OBB changed**: new name, size, SHA-256 and download links (step 2). The builds take the
  name from the data file; an updated app downloads the new OBB on its first start (a full app
  carries it) and deletes the old one, and the iOS app replaces the APK it staged in Documents.

## 2. The game data file

Copy the current `data/game-<version>.json` to `data/game-<version>.json` and fill in: `version`, `obb_name`,
`cdn_url` (the mod's CDN archive), `libs_sha256` (all four), `apk_sha256`, `obb_sha256`, `obb_size`,
`obb_urls`.

Since 1.5.0 the mod publishes its OBB as a live package: `https://rsb.ducnguyen.org/live/latest.json`
names the newest one (`time`, the link to the `full` file, its size and SHA-256, and a `patch` from
the one before). Pin one `time`: `obb_urls` is `https://rsb.ducnguyen.org/download/live/<time>/full`.
The Android app follows `latest.json` by itself (its Java side downloads the patch and `libRFLib`
applies it); our apps do not, they stay on the pinned OBB (see [GAME-NOTES.md](GAME-NOTES.md),
"The mod's own updaters"). Older OBBs are on github.com/lantern-fans/obbybackup, one tag per version.

Since 1.5.0 the mod also replaces its own library after the APK is installed, so the APK's
`libRFLib.so` is not what Android players run. A build uses the library `mod_lib` pins instead
(`scripts/mod-lib.py` puts it into a copy of the APK): `version` (what the title screen shows),
`timestamp` and `sha256` from `https://reflourished.ducnguyen.org/libcdn/liblatesttimerelease`,
`apk_sha256` (the APK's own `libRFLib.so`), `url` and `member` (the file's name in the tar).
`libs_sha256` names the pinned library, not the APK's. **When the mod publishes a newer library**
(the link then serves that one, and a build without a kept copy stops with a message): change
`mod_lib` and `libs_sha256`, write the hook table again (step 3), and attach the new file to the
build-inputs draft as `libRFLib-<mod_lib version>.so` ([RELEASING.md](RELEASING.md)).

The version is also written in these places (search for the old version number):

- `build.sh`: the data and hook file names, and the CDN archive's name;
- `runtime/src/translation-layer-next/husk-tl-pvz2.c`, `runtime/tools/pvz2-macos.m`,
  `runtime/tools/pvz2-ios.m`, `runtime/tools/regress/build-*-app.sh`: the CDN archive's file name
  (`<version>.main.tar`) and the default app version;
- `.github/workflows/build.yml`: the input descriptions and the release text.
- `README.md`: the game badge, its link to the mod's changelog (`Changelogs/Version_<major>.<minor>.x`)
  and the APK's name in the build steps.
- `runtime/win/smoke/first-<version>.png`: the Windows smoke test's picture of the first start
  ([TESTING.md](TESTING.md), "Windows smoke test"). Without it, `check-repo.sh` warns and the smoke
  test fails but keeps the new picture: take `first.png` from the run's `smoke-x64` artifact.

`scripts/check-repo.sh` (run on every push and before a release builds) says which of these still
name another version.

## 3. The hook table

`libRFLib` installs its inline hooks (453 in 1.5.0-R00) into `libPVZ2` at start-up by writing branches
over function prologues. A converted library cannot be written at run time, so the converter bakes a
dispatch stub into every hooked function ahead of time, and it has to know which functions those
are: `data/hooks-<version>.json` lists them (each `site` is the function's address in `libPVZ2.so`).

**When `libPVZ2.so` did not change** (1.4.2 to 1.5.0), the table is written from a run of the
converted build itself. The mod asks for every hook through `A64HookFunction`, which the shim
answers, and with `HUSK_HOOK_TRACE=1` the shim logs every request:

```sh
git mv data/hooks-<old>.json data/hooks-<new>.json        # start from the old table
./build.sh --mac --apk new.apk --out out/new
HUSK_HOOK_TRACE=1 scripts/test-run.sh --app "out/new/PvZ2 Reflourished.app" --seconds 20 --out out/runs/hooks1
converter/aot-hook-sites.py out/runs/hooks1/run.log --game <new> \
    --lib out/new/.work/libs/lib/arm64-v8a/libPVZ2.so -o data/hooks-<new>.json
```

With the old table the game may not get past its first frames (1.5.0 stopped after four: 169 of its
hooks were not in 1.4.2's table); the hooks are all asked for before that, so the log is complete.
Then build again (a changed table converts the libraries again) and run again the same way: the log
must have no `is not baked` line, and `aot-hook-sites.py <log> --check data/hooks-<new>.json` must
say the table holds exactly the sites asked for. A hook the game asks for later (none so far) would
show as an `is not baked` line in `pvz2.log`: search for it after playing.

**When `libPVZ2.so` changed**, the old table's sites are wrong and the converted build cannot start
from them. 1.4.2's table was recorded through Husk's translating loader (the unconverted `.so`
files, `TL_AOT_DIR` unset) on a Mac, with `TL_CODEWRITE_LOG=<file>`, which logs every code store the
mod makes; `converter/aot-hooks.py <log> --json` reduces the log to the final value per address.
That route is still part of `runtime/` but nothing here builds or tests it today. An empty table
and the trace above may be enough (the hooks are asked for at start-up, before the game needs
them); try that first, and please document what it took.

## 4. Game addresses

If `libPVZ2.so` changed, find each address again in the new build (Ghidra, or a disassembler and the
old build side by side: the functions are usually the same code at new offsets). The table in
[GAME-NOTES.md](GAME-NOTES.md) says what each one is for; the driver's comments say how each was
found. Check every vtable and offset by reading live objects in a test run before trusting it.
The driver guards every call it makes into the game, so a wrong address shows up as a refused action
and a log line, not as a crash. Look for those in the log.

## 5. Test

A full build (`./build.sh --apk new.apk`, no `--quick`), then on the Mac ([TESTING.md](TESTING.md)):
the first launch (Welcome screen), the title, the map, a level played for a minute with each keyboard
action (1-4, Q W E R, 5, T, S, Space, Tab, Esc), going to the background and back, a resize and full screen after
start-up. Then the same on an iPhone or iPad by hand. Note what you could not test in the pull request.
