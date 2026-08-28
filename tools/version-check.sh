#!/usr/bin/env bash
# Fail if the plugin's surface changed without the version moving with it.
#
# This repo IS its own marketplace: `.claude-plugin/marketplace.json` declares
# `"source": "./"`, and Claude Code has it registered as a git source pointing at
# this remote. So master is the published artefact and there is no separate
# publish step — which also means there is no such thing as "published is behind
# master" to detect. The drift that actually bites is the other one: master moves
# while the version does not, and every existing install keeps serving what it
# already cached, because the cache is keyed on the version.
#
# That is the 26 August review's R1 in its true form. The published build was not
# stale because a publish failed; it was stale because eight modules, two skills
# and pyproject.toml changed under an unchanged 0.1.0.
#
# Usage: tools/version-check.sh <base-ref>
set -euo pipefail

BASE="${1:?usage: tools/version-check.sh <base-ref>}"

# Everything Claude Code serves, plus the packaging that decides what pip builds.
SURFACE=(
    .claude-plugin
    agents
    domain
    schemas
    skills
    synthvdr
    tools
    pyproject.toml
)

# `if ! CHANGED=$(...)` rather than a bare assignment. Under `set -e` a bare
# assignment whose command substitution fails takes the whole script with it —
# the same shape that made version_at() below exit 128 for a base ref with no
# manifest. It is NOT the same severity here and the comment should not claim
# it is: git's own "fatal: bad revision" is not suppressed on this line, so the
# failure did reach the log. What it did not do is say which script failed,
# which argument was bad, or what the run concluded — the raw git message alone,
# in a check whose every other line speaks as `version-check:`.
#
# FAILING IS CORRECT AND MUST STAY THAT WAY. A base ref this cannot diff
# against is a comparison that never ran, and a check that could not run must
# never fall through to the "nothing to bump" pass below. That is the
# silence-is-never-a-pass rule this repo is built on, so the error is turned
# into a louder failure, never a warning.
if ! CHANGED=$(git diff --name-only "$BASE"...HEAD -- "${SURFACE[@]}" 2>&1); then
    echo "version-check: cannot compare against '$BASE'. git said:" >&2
    printf '%s\n' "$CHANGED" | sed 's/^/  /' >&2
    echo >&2
    # Quoted delimiter: the text names $GITHUB_BASE_REF as a literal, and an
    # unquoted heredoc would expand it — unbound under `set -u`, so the
    # diagnostic would itself die halfway through printing.
    cat >&2 <<'MSG'
The surface comparison never ran, so this is a hard failure rather than a pass:
a check that cannot see what changed must not report that nothing did.

Check the ref exists and that history is deep enough to reach it. CI passes
origin/$GITHUB_BASE_REF and needs actions/checkout with fetch-depth: 0 — a
shallow clone is the usual way this ref becomes unreachable.
MSG
    exit 1
fi

if [ -z "$CHANGED" ]; then
    echo "version-check: no plugin surface changed against $BASE — nothing to bump."
    exit 0
fi

version_at() {
    # `|| true` is not defensive noise. `git show REF:missing-path` exits 128,
    # and under `set -euo pipefail` a failing pipeline in a bare `V=$(...)`
    # assignment kills the script — exit 128, no output, an empty CI log and
    # no clue which line did it. That is what happened for any base ref with
    # no plugin.json, which is exactly the case the empty-version branch below
    # was written to handle and could never reach.
    git show "$1:.claude-plugin/plugin.json" 2>/dev/null \
        | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' || true
}

# Plain X.Y.Z(.W...) only — no pre-release suffixes, no leading/trailing/double
# dots, and nothing that would break the arithmetic below (a multi-line value
# from a manifest carrying two "version" lines included).
is_dotted_numeric() {
    case "$1" in "" | *[!0-9.]* | .* | *. | *..*) return 1 ;; esac
    return 0
}

# 0 if $1 is strictly newer than $2. Pure bash rather than `sort -V`: this is a
# release gate, CI runs GNU coreutils and the development machine runs BSD, and
# the two disagree about -V. Three details are deliberate:
#   * `case`, not `[[ =~ ]]` — no bash 3.2 RHS-quoting trap on macOS.
#   * `10#` — $((08)) is an octal error, 10#08 is 8.
#   * explicit `if ... then return; fi`, not `[ ... ] && return 0` — the && form
#     is a complete list whose status is 1 when the test fails, which `set -e`
#     would act on in some callers.
version_gt() {
    local i x y
    local -a A B
    IFS=. read -r -a A <<<"$1"
    IFS=. read -r -a B <<<"$2"
    i=0
    while [ "$i" -lt "${#A[@]}" ] || [ "$i" -lt "${#B[@]}" ]; do
        x=$((10#${A[i]:-0}))
        y=$((10#${B[i]:-0}))
        if [ "$x" -gt "$y" ]; then return 0; fi
        if [ "$x" -lt "$y" ]; then return 1; fi
        i=$((i + 1))
    done
    return 1
}

BASE_VERSION=$(version_at "$BASE")
HEAD_VERSION=$(version_at HEAD)

if [ -z "$HEAD_VERSION" ]; then
    echo "version-check: no version in .claude-plugin/plugin.json at HEAD." >&2
    exit 1
fi

if [ "$BASE_VERSION" = "$HEAD_VERSION" ]; then
    echo "version-check: the plugin surface changed but the version is still $HEAD_VERSION." >&2
    echo >&2
    echo "$CHANGED" | sed 's/^/  /' >&2
    echo >&2
    cat >&2 <<MSG
Every install caches by version, so shipping this under an unchanged version
means nobody is offered it. Bump all four declarations together:

  .claude-plugin/plugin.json      "version"
  .claude-plugin/marketplace.json plugins[0].version
  pyproject.toml                  version
  synthvdr/__init__.py            __version__

test_plugin_manifest_version_agrees_with_package_version checks they agree; this
checks the number actually moved.
MSG
    exit 1
fi

# The version moved. It must have moved FORWARDS: installs cache by version,
# so a number going backwards re-serves a build that is already out there
# under a name that says it is newer, which is the same staleness this whole
# script exists to prevent, arrived at from the other side.
if [ -z "$BASE_VERSION" ]; then
    echo "version-check: surface changed; no version at $BASE (no manifest there) -> $HEAD_VERSION. OK."
    exit 0
fi

if is_dotted_numeric "$BASE_VERSION" && is_dotted_numeric "$HEAD_VERSION"; then
    if ! version_gt "$HEAD_VERSION" "$BASE_VERSION"; then
        echo "version-check: the version does not move forwards: $BASE_VERSION -> $HEAD_VERSION." >&2
        echo >&2
        cat >&2 <<MSG
Every install caches by version. A version that goes backwards, or sideways to
something not greater than the base, means an install that already holds the
higher number is never offered this build — and one that does not gets served
it as though it were newer. Bump forwards from $BASE_VERSION in all four
declarations.
MSG
        exit 1
    fi
else
    # Said out loud rather than passed over. A permissive fallback that stays
    # silent is the silence-equals-pass shape this project refuses; printing
    # makes the gap visible without hard-failing a numbering scheme this
    # comparison has no competence over.
    echo "version-check: $BASE_VERSION -> $HEAD_VERSION is not a plain dotted-numeric pair; ordering not checked."
fi

echo "version-check: surface changed, version $BASE_VERSION -> $HEAD_VERSION. OK."
