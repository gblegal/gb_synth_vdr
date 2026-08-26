#!/usr/bin/env bash
# Package synth-vdr's read-only skills for upload to claude.ai
# (Settings > Capabilities > Skills). claude.ai expects a ZIP whose root holds
# the skill FOLDER — vdr-qa/SKILL.md — not a bare SKILL.md.
#
# WHY ONLY TWO OF THE SIX SKILLS. /vdr-scope, /vdr-findings and /vdr-build build
# a room, and /vdr-build fans out to vdr-author subagents in parallel waves —
# twenty-one references to that mechanism in its own SKILL.md. claude.ai skills
# have no equivalent, so those three would upload and then not work. The two
# packaged here consume a room that already exists and shell out to a CLI, which
# ports cleanly. Porting the building half means redesigning wave fan-out as
# sequential authoring in one context; that is a design change, not a packaging
# one, and is deliberately not attempted here.
#
# WHAT THIS ADDS TO EACH BUNDLED SKILL.md, neither of which is in the repo copy:
#
#   1. The version, appended to the description. claude.ai numbers its own
#      UPLOADS v1, v2, ... and reads no version from the bundle — there is
#      nothing in a skill folder for it to read one from — so without this
#      stamp nothing on the page says which build you are looking at. Derived
#      from synthvdr.__version__ rather than typed, so it cannot disagree with
#      the code it ships beside.
#
#   2. A preamble putting the skill folder on PYTHONPATH. The synthvdr package
#      and its domain pack ship INSIDE the folder rather than installed, and
#      DEFAULT_DOMAIN_ROOT resolves relative to the package (`__file__/../..`),
#      so the two must stay siblings and the folder must be importable. Without
#      the preamble the first command a reader runs is a ModuleNotFoundError.
#
# The repo's own SKILL.md files are NOT edited. Their descriptions are what
# Claude Code matches on when deciding to invoke a skill; the stamp is a
# packaging concern and belongs at package time.
#
# Usage: tools/build-claude-skills.sh [out-dir]     (default: dist/)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out_dir="${1:-$repo_root/dist}"

# The skills that work without subagents. See the note above before adding one.
SKILLS=(vdr-qa vdr-score)

version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$repo_root/synthvdr/__init__.py")
if [ -z "$version" ]; then
    echo "error: no __version__ in $repo_root/synthvdr/__init__.py" >&2
    exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$out_dir"

for skill in "${SKILLS[@]}"; do
    src="$repo_root/skills/$skill/SKILL.md"
    if [ ! -f "$src" ]; then
        echo "error: $src not found" >&2
        exit 1
    fi

    dst="$work/$skill"
    mkdir -p "$dst/domain"
    cp -R "$repo_root/synthvdr" "$dst/synthvdr"
    cp -R "$repo_root/domain/ma" "$dst/domain/ma"
    find "$dst" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

    VERSION="$version" python3 - "$src" "$dst/SKILL.md" <<'PY'
import os
import pathlib
import sys

src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
version = os.environ["VERSION"]
lines = src.read_text(encoding="utf-8").splitlines()

try:
    end = lines.index("---", 1)
except ValueError:
    sys.exit(f"error: {src} has no closing frontmatter delimiter")

for i in range(1, end):
    if not lines[i].startswith("description:"):
        continue
    text = lines[i][len("description:"):].strip()
    # Idempotent: strip a stamp left by an earlier build before adding this
    # one, so re-running never yields "(synth-vdr 0.2.1) (synth-vdr 0.3.0)".
    if text.endswith(")") and "(synth-vdr " in text:
        text = text[: text.rindex("(synth-vdr ")].rstrip()
    lines[i] = f"description: {text} (synth-vdr {version})"
    break
else:
    sys.exit(f"error: no description in {src}")

preamble = f"""
## Running the tooling in this bundle

Every command below calls `python3 -m synthvdr...`. The `synthvdr` package and its
`domain/` pack ship **inside this skill folder**, not installed — so make the folder
importable before the first command and everything else works unchanged:

```bash
export PYTHONPATH=/path/to/this/skill/folder
```

If `import yaml` fails, `pip install PyYAML` first — it is the only third-party dependency.

This bundle is synth-vdr **{version}**, and carries its read-only half. Building a room
(`/vdr-scope`, `/vdr-findings`, `/vdr-build`) needs parallel subagents and is Claude Code
only; this skill works on a room that already exists.
"""

dst.write_text(
    "\n".join(lines[: end + 1]) + "\n" + preamble + "\n".join(lines[end + 1 :]) + "\n",
    encoding="utf-8",
)
PY

    rm -f "$out_dir/$skill-skill.zip"
    ( cd "$work" && zip -rq "$out_dir/$skill-skill.zip" "$skill" -x '*.DS_Store' )
    echo "built $out_dir/$skill-skill.zip  (synth-vdr $version)"
done
