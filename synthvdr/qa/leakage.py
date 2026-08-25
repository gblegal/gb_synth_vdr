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
from typing import List, Set, Tuple

from ..names import cast_list, covered_by_cast, entity_tokens
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

_HIT_LIMIT = 5


def finding_id_pattern(conf) -> re.Pattern:
    """A finding ID must not continue into another alphanumeric character.

    The trailing edge used to be a plain `\\b`, which `_` defeats — `_` is a
    word character, so there is no boundary between the "1" in "ENV-1" and
    an underscore straight after it, and this project's own slug
    convention ("1.1.1_articles.md") puts an underscore exactly there. An
    explicit negative lookahead for a following letter or digit rejects
    only a real alphanumeric continuation ("ENV-1a" must not match) while
    treating '_', '.', '-', and end-of-string as legitimate boundaries.
    """
    prefixes = conf.get("FINDING_PREFIXES")
    boundary = r"(?![A-Za-z0-9])"
    return re.compile(rf"\b(?:{prefixes})-\d+{boundary}|\bDX-\d+{boundary}")


def _truncated(items: List[str], sep: str = "; ", limit: int = _HIT_LIMIT) -> str:
    """Join up to `limit` items, naming how many more were cut.

    A silently truncated list reads identically whether six things went
    wrong or sixty — the missing count is itself information a human
    triaging a FAIL needs, not decoration.
    """
    shown = sep.join(items[:limit])
    remaining = len(items) - limit
    if remaining > 0:
        shown += f" (+{remaining} more)"
    return shown


def _fallback_note(replaced: int) -> str:
    """A one-clause note naming how many files needed lossy decoding.

    Empty when none did, so every caller can append it unconditionally.
    """
    if not replaced:
        return ""
    plural = "" if replaced == 1 else "s"
    return f"; {replaced} file{plural} read with lossy decoding (invalid UTF-8 replaced)"


def _read_lossy(path: Path) -> Tuple[str, bool]:
    """Read `path` as UTF-8, falling back to errors="replace" rather than
    ever raising UnicodeDecodeError. A file that cannot be strictly decoded
    is still a file a leak can hide in — skipping it and then reporting the
    room "clean" would assert that a file was read when it never was.
    Returns (text, needed_fallback). OSError (the file cannot be opened at
    all) is left to the caller, which already needs a per-path try/except
    for it around whatever else it does with `text`.
    """
    try:
        return path.read_text(encoding="utf-8"), False
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace"), True


def _hits(paths: List[Path], needles, pattern=None) -> Tuple[List[str], int]:
    """Sweep each path's content AND filename for `needles` and `pattern`.

    The filename is swept alongside the content: a path is part of what the
    tool under test actually receives, so answer-key material sitting in a
    name is exactly as much of a leak as material sitting in a body. Returns
    (hits, replaced) — the count of files that needed `_read_lossy`'s
    decoding fallback, so callers can say so in `detail` rather than
    reporting a clean sweep of files that were never fully read.
    """
    out: List[str] = []
    replaced = 0
    for path in paths:
        try:
            text, needed_fallback = _read_lossy(path)
        except OSError:
            continue
        if needed_fallback:
            replaced += 1
        haystacks = (text.lower(), path.name.lower())
        for needle in needles:
            needle_lower = needle.lower()
            if any(needle_lower in haystack for haystack in haystacks):
                out.append(f"{path.name}: {needle!r}")
        if pattern:
            match = pattern.search(text) or pattern.search(path.name)
            if match:
                out.append(f"{path.name}: {match.group(0)!r}")
    return out, replaced


def gate_03_flag_leakage(ctx):
    files = ctx.blind_files()
    if not files:
        return skip("3", "annotation-string leakage", f"{ctx.blind_root} absent or empty")
    strings = (ctx.conf.get("FLAG_STRING_1"), ctx.conf.get("FLAG_STRING_2"))
    hits, replaced = _hits(files, strings)
    if hits:
        return fail("3", "annotation-string leakage", _truncated(hits) + _fallback_note(replaced))
    return ok("3", "annotation-string leakage", f"{len(files)} files clean" + _fallback_note(replaced))


def gate_04_vocabulary(ctx):
    files = ctx.blind_files()
    if not files:
        return skip("4", "blind-tree vocabulary sweep", f"{ctx.blind_root} absent or empty")
    hits, replaced = _hits(files, ANSWER_KEY_NOUNS, finding_id_pattern(ctx.conf))
    if hits:
        return fail("4", "blind-tree vocabulary sweep", _truncated(hits) + _fallback_note(replaced))
    return ok("4", "blind-tree vocabulary sweep", f"{len(files)} files clean" + _fallback_note(replaced))


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
    hits, replaced = _hits([index_path], needles, finding_id_pattern(ctx.conf))
    if hits:
        return fail(
            "5",
            "index.md vocabulary sweep",
            _truncated(hits)
            + " — fix _key/index-src/ and regenerate, never hand-edit index.md"
            + _fallback_note(replaced),
        )
    return ok("5", "index.md vocabulary sweep", "clean" + _fallback_note(replaced))


def gate_12_key_containment(ctx):
    files = ctx.blind_files()
    if not files:
        return skip("12", "answer-key containment", f"{ctx.blind_root} absent or empty")
    key_root = ctx.conf.get("KEY_ROOT")
    hits, replaced = _hits(files, (key_root + "/",))
    if hits:
        return fail("12", "answer-key containment", _truncated(hits) + _fallback_note(replaced))
    return ok(
        "12",
        "answer-key containment",
        f"no reference to {key_root}/ in {len(files)} files" + _fallback_note(replaced),
    )


def gate_14_unchecked_names(ctx):
    name_check = ctx.key_root / "name-check.md"
    if not name_check.is_file():
        return skip("14", "unchecked names", "_key/name-check.md absent — run /vdr-scope name check")
    files = ctx.blind_files()
    if not files:
        return skip("14", "unchecked names", f"{ctx.blind_root} absent or empty")
    cast = cast_list(name_check)
    unchecked: Set[str] = set()
    replaced = 0
    for path in files:
        try:
            text, needed_fallback = _read_lossy(path)
        except OSError:
            continue
        if needed_fallback:
            replaced += 1
        candidates = entity_tokens(text) | entity_tokens(path.name)
        unchecked |= {c for c in candidates if not covered_by_cast(c, cast)}
    if unchecked:
        detail = _truncated(sorted(unchecked), sep=", ") + " — not on the cast list; check or remove"
        return fail("14", "unchecked names", detail + _fallback_note(replaced))
    return ok("14", "unchecked names", f"{len(cast)} cast names, none unchecked" + _fallback_note(replaced))
