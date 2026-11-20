#!/bin/bash
# Refuse a publishing run that would publish the wrong thing. The Build workflow runs it in its first
# job, so a mistake stops the run in seconds, and again just before the publish job uploads anything,
# in case another run published the same tag while this one was building.
#
#   scripts/check-publish.sh <tag or empty> <replace_release: true|false>
#
# Needs GH_TOKEN, GITHUB_REPOSITORY, GITHUB_REF and GITHUB_SHA (the Actions environment).
#
#   - build-inputs (the draft with the build's inputs) is never a release.
#   - A release is published from main only: a test build of another branch is a run with publish off.
#   - A release that is out already is left alone, unless replace_release is on: then this run's files
#     replace the release's files of the same name (a rebuild of one platform, say).
#   - A tag that exists without a release must name this commit: a tag on another commit is a version
#     that was not bumped, or a release that was deleted, and both need a person to look.
#   - The notes' Updates line (release-notes.py) must not leave out an app whose own files changed
#     since the previous release: its users would not be told about an update that changes their app.
set -euo pipefail

tag="${1:-}"
replace="${2:-false}"
. scripts/app-version.sh
tag="${tag:-v$APP_VERSION}"

fail() { echo "check-publish: $*" >&2; exit 1; }

[ "$tag" != "build-inputs" ] ||
    fail "build-inputs is the draft that holds the build's inputs, and it is never published."

[ "$GITHUB_REF" = "refs/heads/main" ] ||
    fail "publish is on, but this run is on $GITHUB_REF. Releases are published from main only; run a test build with publish off."

# "Not found" and "could not ask" are told apart: a failed lookup must stop the run, not pass it as new.
if out="$(gh release view "$tag" --repo "$GITHUB_REPOSITORY" --json isDraft \
            --jq 'if .isDraft then "draft" else "published" end' 2>&1)"; then
    state="$out"
elif printf '%s' "$out" | grep -qi "release not found"; then
    state=none
else
    fail "could not look up the release $tag: $out"
fi
if out="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$tag" --jq .object.sha 2>&1)"; then
    tag_sha="$(gh api "repos/$GITHUB_REPOSITORY/commits/$out" --jq .sha)"   # an annotated tag names a tag object
elif printf '%s' "$out" | grep -q "HTTP 404"; then
    tag_sha=""
else
    fail "could not look up the tag $tag: $out"
fi

case "$state" in
    published)
        [ "$replace" = "true" ] ||
            fail "$tag is published already. This run would replace its files and update the feeds. Bump VERSION for a new release, or set replace_release to replace this one's files on purpose."
        echo "check-publish: replace_release is on: this run replaces files of the published $tag (tagged ${tag_sha:-?}, this run ${GITHUB_SHA})"
        ;;
    draft)
        echo "check-publish: $tag is a draft; this run adds its files to it"
        ;;
    none)
        if [ -n "$tag_sha" ] && [ "$tag_sha" != "$GITHUB_SHA" ]; then
            fail "the tag $tag exists on $tag_sha without a release, and this run is $GITHUB_SHA. Bump VERSION, or delete the stale tag yourself."
        fi
        echo "check-publish: $tag is new; this run publishes it from $GITHUB_SHA"
        ;;
esac

python3 scripts/release-notes.py --check-updates --version "$APP_VERSION" --tag "$tag" ||
    fail "the Updates line in release-notes/$APP_VERSION.md does not match what changed (above)."
