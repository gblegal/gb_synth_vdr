"""Leakage gates: answer-key material must never reach the eval input.

THE THREE TOKEN LISTS BELOW ARE DELIBERATELY DIFFERENT. Gate 4 sweeps the blind
tree for finding IDs and answer-key nouns. Gate 5 sweeps index.md with a wider
list, because index.md is tool-facing but sits OUTSIDE the blind tree, and the
leak this gate exists to catch — a build instruction in the contents list —
contained none of gate 4's tokens. Widening gate 4 alone would not have caught
it. Do not reconcile the lists.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Set

from ..names import cast_list, entity_tokens
from .runner import fail, ok, skip

# Gate 4. "registry" is deliberately absent: Land Registry is legitimate in-room.
ANSWER_KEY_NOUNS = (
    "planted finding",
    "distractor",
    "answer key",
    "red herring",
    "diligence flag",
    "ground truth",
)

# Gate 5 only, on top of gate 4's list.
BUILD_VOCABULARY = (
    "blind room",
    "blind tree",
    "flagged room",
    "flagged tree",
    "renumber",
    "data-room",
    "_key",
    "tier a",
    "tier f",
)

def finding_id_pattern(conf) -> re.Pattern:
    prefixes = conf.get("FINDING_PREFIXES")
    return re.compile(rf"\b(?:{prefixes})-\d+\b|\bDX-\d+\b")


def _hits(paths: List[Path], needles, pattern=None) -> List[str]:
    out: List[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lowered = text.lower()
        for needle in needles:
            if needle.lower() in lowered:
                out.append(f"{path.name}: {needle!r}")
        if pattern:
            match = pattern.search(text)
            if match:
                out.append(f"{path.name}: {match.group(0)!r}")
    return out


def gate_03_flag_leakage(ctx):
    files = ctx.blind_files()
    if not files:
        return skip("3", "annotation-string leakage", f"{ctx.blind_root} absent or empty")
    strings = (ctx.conf.get("FLAG_STRING_1"), ctx.conf.get("FLAG_STRING_2"))
    hits = _hits(files, strings)
    if hits:
        return fail("3", "annotation-string leakage", "; ".join(hits[:5]))
    return ok("3", "annotation-string leakage", f"{len(files)} files clean")


def gate_04_vocabulary(ctx):
    files = ctx.blind_files()
    if not files:
        return skip("4", "blind-tree vocabulary sweep", f"{ctx.blind_root} absent or empty")
    hits = _hits(files, ANSWER_KEY_NOUNS, finding_id_pattern(ctx.conf))
    if hits:
        return fail("4", "blind-tree vocabulary sweep", "; ".join(hits[:5]))
    return ok("4", "blind-tree vocabulary sweep", f"{len(files)} files clean")


def gate_05_index_vocabulary(ctx):
    index_path = ctx.room / "index.md"
    if not index_path.is_file():
        return skip("5", "index.md vocabulary sweep", "index.md absent")
    needles = (
        *ANSWER_KEY_NOUNS,
        *BUILD_VOCABULARY,
        ctx.conf.get("FLAG_STRING_1"),
        ctx.conf.get("FLAG_STRING_2"),
    )
    hits = _hits([index_path], needles, finding_id_pattern(ctx.conf))
    if hits:
        return fail(
            "5",
            "index.md vocabulary sweep",
            "; ".join(hits[:5]) + " — fix _key/index-src/ and regenerate, never hand-edit index.md",
        )
    return ok("5", "index.md vocabulary sweep", "clean")


def gate_12_key_containment(ctx):
    files = ctx.blind_files()
    if not files:
        return skip("12", "answer-key containment", f"{ctx.blind_root} absent or empty")
    key_root = ctx.conf.get("KEY_ROOT")
    hits = _hits(files, (key_root + "/",))
    if hits:
        return fail("12", "answer-key containment", "; ".join(hits[:5]))
    return ok("12", "answer-key containment", f"no reference to {key_root}/ in {len(files)} files")


def gate_14_unchecked_names(ctx):
    name_check = ctx.key_root / "name-check.md"
    if not name_check.is_file():
        return skip("14", "unchecked names", "_key/name-check.md absent — run /vdr-scope name check")
    files = ctx.blind_files()
    if not files:
        return skip("14", "unchecked names", f"{ctx.blind_root} absent or empty")
    cast = cast_list(name_check)
    unchecked: Set[str] = set()
    for path in files:
        try:
            unchecked |= entity_tokens(path.read_text(encoding="utf-8")) - cast
        except (UnicodeDecodeError, OSError):
            continue
    if unchecked:
        return fail(
            "14",
            "unchecked names",
            ", ".join(sorted(unchecked)[:5]) + " — not on the cast list; check or remove",
        )
    return ok("14", "unchecked names", f"{len(cast)} cast names, none unchecked")
