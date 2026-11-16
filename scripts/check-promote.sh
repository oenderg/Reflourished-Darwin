#!/bin/bash
# Check that an earlier run's files may be published by this run (the Build workflow's from_run),
# instead of building them again. The files that ship are then the ones that run built and tested.
#
#   scripts/check-promote.sh <run id> <publish: true|false>
#
# Needs GH_TOKEN, GITHUB_REPOSITORY and GITHUB_SHA (the Actions environment), and the history of
# this commit (a checkout with fetch-depth 0).
#
#   - The run is a finished Build run of this repository that passed, and its Windows files passed
#     the smoke test (a run that skipped it is no source).
#   - Its commit is this one or an ancestor of it, and every file changed since then is one that no
#     build reads (ALLOWED below): Markdown text and the feeds the publish job rewrites. Anything
#     else (code, VERSION, the workflow, the icon, the update key) needs a new run.
#   - It still has this version's release files (run files expire).
set -euo pipefail

run="${1:-}"
publish="${2:-false}"

fail() { echo "check-promote: $*" >&2; exit 1; }

[[ "$run" =~ ^[0-9]+$ ]] || fail "from_run must be a run's number (its id in the run page's address), not '$run'."
[ "$publish" = "true" ] || fail "from_run publishes another run's files: set publish too, or leave from_run empty for a test build."

# Every file changed since the run's commit must be one of these (case patterns; * also matches /).
allowed() {
    case "$1" in
        docs/*.md | release-notes/*.md) return 0 ;;
        */*) ;;
        *.md) return 0 ;;   # README.md, AGENTS.md and the rest at the top
    esac
    case "$1" in
        altstore/source.json | sparkle/appcast.xml | sparkle/windows-appcast.xml) return 0 ;;
    esac
    return 1
}

info="$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$run" \
    --jq '[.path, .event, .status, .conclusion // "", .head_sha, .head_branch] | @tsv' 2>&1)" ||
    fail "could not look up run $run: $info"
IFS=$'\t' read -r path event status conclusion sha branch <<< "$info"

[ "$path" = ".github/workflows/build.yml" ] || fail "run $run is a $path run, not a Build run."
[ "$event" = "workflow_dispatch" ] || fail "run $run was started by $event, not by hand."
[ "$status" = "completed" ] || fail "run $run is $status: wait for it to finish."
[ "$conclusion" = "success" ] || fail "run $run ended in $conclusion."

jobs="$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$run/jobs?per_page=100" \
    --jq '.jobs[] | "\(.conclusion)\t\(.name)"')"
bad="$(printf '%s\n' "$jobs" | awk -F'\t' '$1 != "success" && $1 != "skipped"')"
[ -z "$bad" ] || fail "run $run has jobs that did not pass: $bad"
if printf '%s\n' "$jobs" | grep -qx $'success\tWindows setup and zips'; then
    for arch in x64 ARM64; do
        printf '%s\n' "$jobs" | grep -qx $'success\tWindows smoke test ('"$arch"')' ||
            fail "run $run built Windows files but its $arch smoke test did not pass (skipped?): they were never started."
    done
fi

git cat-file -e "$sha^{commit}" 2>/dev/null ||
    fail "run $run is on $sha, which is not in this history (a branch that was rebased or deleted?)."
git merge-base --is-ancestor "$sha" "$GITHUB_SHA" ||
    fail "run $run is on $sha (${branch}), which is not part of this commit's history ($GITHUB_SHA)."

# --no-renames: a file moved from the code into docs/ counts as the code file it was.
changed="$(git diff --name-only --no-renames "$sha" "$GITHUB_SHA")"
blocked=""
while IFS= read -r f; do
    [ -n "$f" ] || continue
    allowed "$f" || blocked+="  $f"$'\n'
done <<< "$changed"
[ -z "$blocked" ] ||
    fail "these files changed since run $run ($sha) and may change what is built, so its files can't be published as this commit's. Run a new test build."$'\n'"$blocked"

. scripts/app-version.sh
prefix="PvZ2-Reflourished-$GAME_VERSION-Darwin-$APP_VERSION"
files="$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$run/artifacts?per_page=100" \
    --jq '.artifacts[] | select(.name | test("^PvZ2-Reflourished-.*-(iOS(-Full)?[.]ipa|Mac(-Full)?[.]dmg|Windows-(Setup[.]exe|x64[.]zip|ARM64[.]zip))$")) | "\(.expired)\t\(.name)"')"
[ -n "$files" ] || fail "run $run has no release files."
while IFS=$'\t' read -r expired name; do
    case "$name" in "$prefix"-*) ;; *) fail "run $run's $name is not version $APP_VERSION's ($prefix)." ;; esac
    [ "$expired" = false ] || fail "run $run's $name has expired: run a new test build."
done <<< "$files"

n="$(printf '%s\n' "$changed" | grep -c . || true)"
msg="check-promote: this run publishes the files of run $run (${branch} at ${sha:0:7}), from $GITHUB_SHA; $n file(s) changed since, none that a build reads:"
echo "$msg"
printf '%s\n' "$files" | cut -f2 | sed 's/^/  /'
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
        echo "Files from [run $run](https://github.com/$GITHUB_REPOSITORY/actions/runs/$run) (${branch} at \`${sha:0:7}\`), published from \`${GITHUB_SHA:0:7}\`."
        echo
        printf '%s\n' "$files" | cut -f2 | sed 's/^/- /'
    } >> "$GITHUB_STEP_SUMMARY"
fi
