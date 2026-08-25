---
name: vdr-package
description: Package a finished data room — build the deterministic subset, render optional DOCX/PDF trees, write the content manifest and freeze the room for release. Runs every QA gate in --strict mode; refuses to freeze a room with any failure or any skip.
---

# Package the room

Freezing a room is the point at which someone else — a tool, a benchmark run, a colleague —
starts trusting it. Everything in this skill exists to make that trust checkable rather than
assumed: every gate has actually run (not skipped), the answer key is reproducible in a
bounded subset, and the room carries a content hash that `/vdr-score` can use to prove which
room a tool's output was actually produced against.

## 1. Check for real failures first — not yet strict

```bash
python3 -m synthvdr.qa --room .
```

**Do not run `--strict` yet — this first pass is deliberately non-strict.** Gates 11 (subset
reconciliation) and 16 (render parity) SKIP until Steps 2 and 3 below build their inputs, and
`--strict` turns every skip into a failure; running it now would only tell you what you
already know you have not built yet, not something new, and there is no way to satisfy it
before Step 3 has even run. Fix anything this pass genuinely FAILs on before going further — a
real gate failure is never improved by building the subset or the renders on top of it. Once
this shows `0 failed` (whatever the skip count), move on to Step 2; the strict, no-skips check
that actually gates the freeze is Step 5, once every input exists for it to check.

## 2. Build the subset

```bash
python3 -c "
from pathlib import Path
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import load_findings
from synthvdr.subset import build_subset

conf = load_room_conf(Path('room.conf'))
blind_total = conf.get_int('BLIND_TOTAL')
# The subset is a BOUNDED sample for a cheaper eval run, not a second full copy of the
# room — derived from the room's own size, never a fixed number that quietly stops being
# 'bounded' once a room grows past (or starts below) whatever was hardcoded here. Half the
# room, floored at 50, is a proper subset of anything bigger than ~100 documents; for a
# room already at or below that floor, `report.total == blind_total` below and the room IS
# the subset — check it, rather than assuming 'gate 11 passed' means 'this run was bounded'.
subset_total = min(blind_total, max(50, blind_total // 2))
report = build_subset(Path('.'), conf, load_findings(Path('_key/findings.yaml')), subset_total, Path('subset'))
print(report)
assert report.complete, report.errors
if report.total == blind_total:
    print(f'NOTE: subset/ is a full copy of the room ({blind_total} documents) — the room '
          'is at or below the floor for a genuinely bounded subset.')
"
```

The subset reproduces **every** finding with its full evidence chain, plus deterministic
filler up to `subset_total` above. The run is deterministic — re-running it over an unchanged
room produces a byte-identical subset. This is what turns gate 11's SKIP into a real check —
confirm it with Step 1's non-strict command again if you want to see it before Step 5's final
strict run.

## 3. Render (optional, but strict mode holds you to whichever you choose)

```bash
python3 -c "
from pathlib import Path
from synthvdr.render.docx import render_tree_docx
print(render_tree_docx(Path('data-room'), Path('data-room-docx')))
"
node "${CLAUDE_PLUGIN_ROOT}/synthvdr/render/pdf.mjs" --src data-room --out data-room-pdf
```

(Use the room's actual `BLIND_TREE` name from `room.conf` in place of `data-room` above if it
differs.)

Both are optional in the sense that you choose which toolchain(s) to use — DOCX needs only the
`docx` extra (`pip install -e ".[docx]"`), PDF needs Node, puppeteer and a local Chrome. But
**skipping both is not a real option at release**: gate 16 (render parity) prints `SKIP` when
no render tree exists at all, and Step 5's `--strict` run treats that skip as a failure, same
as any other. If a toolchain is genuinely unavailable in your environment, say so to the user
and stop here rather than force one — do not weaken `--strict` to work around it; a room
frozen without a passing render-parity check has not actually had that check run.

The render is non-destructive by design: it creates directories and writes/overwrites the
files it renders, but never deletes a stale render left behind by a since-renamed or
since-deleted source. If you rename or remove a source document after rendering, re-render
before Step 5's final gate run — gate 16 will catch the mismatch either way, in both
directions (a source missing its render, and a render missing its source), but it is your job
to fix it, not the renderer's to guess which side is right.

## 4. Write the manifest

`_key/manifest.json` is what makes the room's provenance checkable. `content_hash` is
`sha256` over the sorted `rel_path + "\0" + sha256(bytes)` of every file in the blind tree —
**this exact form**, because `/vdr-score`'s `check_provenance` reads `content_hash` out of
this file and compares it, as a plain string, against a tool output's `room_hash`. Writing a
different key, a different shape, or nothing at all makes that check permanently inert: a
user could then score a tool's output from one room against a completely different room's
answer key and get a confident, precise, meaningless number, with nothing in the pipeline
able to catch it.

Run this exactly — copy and adapt only the total/paths, never the hash construction itself:

```python
import hashlib
import json
from datetime import date
from pathlib import Path

from synthvdr.roomconf import load_room_conf
from synthvdr.schema import load_findings


def compute_content_hash(blind_root: Path):
    """sha256 over the sorted `rel_path + "\\0" + sha256(bytes)` of every
    file in the blind tree. Sorting the per-file entries themselves (not
    the Path objects) before the final hash means the result depends only
    on file content and relative path, never on directory-walk order or
    PYTHONHASHSEED.
    """
    entries = []
    for path in blind_root.rglob("*"):
        if not path.is_file():
            continue
        rel_path = path.relative_to(blind_root).as_posix()
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{rel_path}\0{file_digest}")
    entries.sort()
    digest = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    return digest, len(entries)


conf = load_room_conf(Path("room.conf"))
blind_root = Path(".") / conf.get("BLIND_TREE")
findings = load_findings(Path("_key/findings.yaml"))

content_hash, document_count = compute_content_hash(blind_root)
manifest = {
    "room": conf.get("ROOM_CODENAME"),
    "content_hash": content_hash,
    "documents": document_count,
    "findings": len(findings.findings),
    "built": date.today().isoformat(),
}
Path("_key/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(manifest)
```

The literal shape this writes — copy and adapt, do not reconstruct it from memory, since
`content_hash` and `documents`/`findings`/`built` are exactly the fields `/vdr-score`'s
`check_provenance` and the tool-output schema (`schemas/tool-output.schema.json`) expect:

```json
{
  "room": "Project Ashfell",
  "content_hash": "3f2504e0a1234567890abcdef0123456789abcdef0123456789abcdef012345",
  "documents": 812,
  "findings": 42,
  "built": "2026-06-01"
}
```

**Hand `content_hash` to whoever is going to produce the tool output that gets scored
against this room.** It belongs in that output's own `room_hash` field
(`schemas/tool-output.schema.json`). Without it, `/vdr-score` still scores the run, but marks
the whole scorecard `UNVERIFIED` rather than silently assuming the output came from this
room.

## 5. Verify in strict mode — the actual release gate

```bash
python3 -m synthvdr.qa --room . --strict
```

**This is the one point in this skill where `--strict` must show a clean result, and the only
check that actually licenses Step 6.** Every input the seventeen gates check now exists —
the subset (Step 2), a render tree or an explicit decision not to build one (Step 3), and the
manifest (Step 4, itself a file under `_key/`, so gate 12 must confirm it has not leaked into
the blind tree same as every other answer-key artefact) — so `--strict` is now answerable, not
circular the way running it in Step 1 would have been. **Do not package a room with any
failure or any skip.** If anything fails or skips here, fix it or build the missing input (see
`/vdr-qa`'s "Common failures" section) and re-run this step until it is clean before Step 6.

## 6. Freeze

Commit and tag. Tell the user, in one line each: what to hand a tool under test
(`data-room/` — or `subset/` for a bounded run — plus `data-room-docx/`/`data-room-pdf/` if
built), and what must never leave the room (`_key/`, in full — the manifest included, since
`content_hash` alone reveals nothing but is still answer-key infrastructure).
