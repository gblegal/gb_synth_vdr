"""The room's content hash, and the manifest that carries it.

`_key/manifest.json` is what makes a room's provenance checkable:
`score.check_provenance` reads `content_hash` out of it and compares it,
as a plain string, against the `room_hash` a tool stamps on its output.
Without a manifest a scorecard is UNVERIFIED; with a wrong one it is
worse than useless, because someone can score one room's output against
another room's answer key and get a confident, precise, meaningless
number.

WHY THE HASH LIVES HERE AND NOT IN THE SKILL. It used to be a code block
in `/vdr-package`'s markdown, introduced with "run this exactly — copy
and adapt only the total/paths, never the hash construction itself".
That is an algorithm kept under version control in prose: nothing tested
it, nothing stopped an agent retyping it slightly differently on a room
built next month, and the comparison downstream is a string equality
that cannot tell a differently-constructed hash from a different room —
it just says the output came from somewhere else. The skill now runs
`python3 -m synthvdr manifest`, and the construction is pinned by
`tests/test_manifest.py`. Moving it also made the corrupted twin's own
manifest possible, which needed exactly this function and could not
reach into a skill's markdown for it.

NO CLOCK IN THIS MODULE. The packaged manifest carries a `built` date,
but the caller passes it — determinism is a project-wide rule (no RNG,
no clock anywhere in `synthvdr`), and the corrupted twin's manifest,
which must stay byte-identical for a given (room, seed, profile), omits
the field altogether rather than carrying a date that would break that.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional, Tuple

MANIFEST_NAME = "manifest.json"


def compute_content_hash(root: Path) -> Tuple[str, int]:
    """sha256 over the sorted `rel_path + "\\0" + sha256(bytes)` of every
    file under `root`, and the file count.

    Sorting the per-file entries themselves (not the Path objects) before
    the final hash means the result depends only on file content and
    relative path — never on directory-walk order or PYTHONHASHSEED. The
    path is in the hash as well as the bytes, so a room whose documents
    were renamed is a different room, which is exactly what the corrupted
    twin is.

    THIS EXACT FORM IS LOAD-BEARING. Every room already released carries a
    hash built this way, and every tool output produced against one of
    them stamps it. A change here does not fail loudly: it silently turns
    every one of those into a "produced against a different room" refusal.
    """
    entries = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root).as_posix()
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{rel_path}\0{file_digest}")
    entries.sort()
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest(), len(entries)


def write_manifest(path: Path, manifest: dict) -> None:
    """Write a manifest as pretty JSON with a trailing newline.

    One writer for both manifests — the packaged room's and the corrupted
    twin's — so the two cannot drift into different spellings of the same
    file.
    """
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def read_content_hash(path: Path) -> str:
    """The `content_hash` in the manifest at `path`, or "" if there is
    none to read — absent file, unreadable JSON, or no such key.

    Returning "" rather than raising is deliberate at both call sites: to
    `check_provenance` a missing hash is UNVERIFIED, not an error, and to
    the corrupted twin a clean room that has not been packaged yet simply
    has no lineage to record.
    """
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(manifest, dict):
        return ""
    return manifest.get("content_hash") or ""


def build_room_manifest(
    room_codename: str,
    content_hash: str,
    documents: int,
    findings: int,
    built: str,
) -> dict:
    """The packaged room's manifest, in the shape `/vdr-score` expects.

    `built` is passed in, never read from the clock here — see the module
    docstring.
    """
    return {
        "room": room_codename,
        "content_hash": content_hash,
        "documents": documents,
        "findings": findings,
        "built": built,
    }


def build_twin_manifest(
    room_codename: str,
    content_hash: str,
    documents: int,
    seed: int,
    derived_from: Optional[str] = None,
) -> dict:
    """The corrupted twin's manifest.

    Deliberately not the packaged room's shape. There is no `findings`
    count (the twin rewrites the classification key, not findings.yaml)
    and no `built` date (a clock would break the twin's byte-identity for
    a given room, seed and profile). What it adds instead is `seed` and
    `derived_from` — the clean room's own content_hash where the room has
    been packaged — because a twin means nothing except against the room
    it was cut from, and without that line a scorecard can say which twin
    it scored but not which room the twin came from.
    """
    manifest = {
        "room": room_codename,
        "content_hash": content_hash,
        "documents": documents,
        "seed": seed,
    }
    if derived_from:
        manifest["derived_from"] = derived_from
    return manifest
