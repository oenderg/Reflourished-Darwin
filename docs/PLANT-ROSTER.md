# Personal plant roster

`data/plant-roster-98.json` records the requested Reflourished 1.5.0 selection:
98 regular plants stay visible, 94 are hidden, and 40 of the 48 permanent shop
plants are delisted. The 8 remaining shop plants cost 650 gems in total.
Marigold, the ten Roman Zomboid cards and the sixteen already-hidden entries
are outside the regular roster and retain their original definitions.

## Build

Use Python 3.11 or newer and the `zstd` command. Supply the original, unmodified
1.5.0 OBB; the roster tool verifies its pinned SHA-256 before processing it.

```sh
./build.sh --mac --quick --apk <apk> --obb <original-obb> \
    --plant-roster data/plant-roster-98.json
```

Omit `--mac` to build both platforms. A roster requires a full build: slim apps
would download the unmodified data. Omit `--plant-roster` to build the original
roster again. Do not replace `data/game-1.5.0.json`'s official hashes with a
personalized file's hashes.

The tool writes a separate `.work/plant-roster.obb` and a `.roster.json` audit
report. It appends a replacement Packages subgroup to a copy of the OBB and
updates that subgroup's index and loader pool capacity, leaving all other
resource groups intact.
Within Packages, only these two files change:

- `PLANTTYPES.RTON`: set `HideInPlantViewers=true` on exactly the 94 requested IDs.
- `MARKET_SCHEDULE.RTON`: remove scheduled plant products for the 40 hidden shop
  plants. Product definitions, prices and all other offers remain unchanged.

The output is reopened and decoded to verify both modified files. Every other
package is compared byte for byte with the source. Plant definitions are compared
field by field to verify that only the requested visibility flags changed.
Game assets, extracted packages, personalized OBBs and saves must stay outside Git.

## Scope

This is a visibility policy, not a rewrite of the campaign. It preserves plant
frameworks, resources, ownership, rewards, quests, preset seed banks, conveyors
and preplanted plants. Hidden plants can still appear when a level explicitly
supplies or requires them. Some optional world mechanics may require a hidden
plant in a free-choice level; the roster follows the requested 54-item list
without postponing those plants until a world is completed.

The roster does not promise that all levels remain completable with only the
98 visible cards. Making hidden plants disappear from fixed levels, rewards,
quest artwork and every other screen would require separate campaign and UI
changes. Later game updates must be audited against their own data.

The full personalized app carries its OBB. Remote OBB download metadata is
omitted because the official download would not match the personalized hash.
Rebuilding a normal full app restores the original bundled data. Keep an
unmodified app or OBB available for rollback.

## Mac validation

The 98-plant build was launched with isolated copies of a test save. Its resource
audit reports 98 visible regular plants, 94 hidden plants, 40 delisted products,
and 8 remaining shop plants costing 650 gems. All 38 requested nostalgic plants
remain visible.

The Mac shop screenshot shows seven purchasable plants because the test profile
already owns Imitater. The other seven are Cold Snapdragon, Shrinking Violet,
Escape Root, Pokra, Pumpkin, Power Lily and Teleportato Mine. The free-choice
screen shows restored Bloomerang, Coconut Cannon and Spring Bean. A conveyor
regression with the same visibility mechanism also supplies Bloomerang while
its hidden flag is set, confirming that hiding a viewer entry does not delete
its conveyor resources. Test-only level overrides are confined to the test data
folders; they are not included in the delivered app.

Evidence is under `out/roster-tests/`: `final-98`, `chooser-98`, `almanac-98`,
and the earlier `conveyor` regression. No phone or tablet run was performed.
