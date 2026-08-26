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

CHANGED=$(git diff --name-only "$BASE"...HEAD -- "${SURFACE[@]}")
if [ -z "$CHANGED" ]; then
    echo "version-check: no plugin surface changed against $BASE — nothing to bump."
    exit 0
fi

version_at() {
    git show "$1:.claude-plugin/plugin.json" 2>/dev/null \
        | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
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

echo "version-check: surface changed, version $BASE_VERSION -> $HEAD_VERSION. OK."
