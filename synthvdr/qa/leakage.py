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

from ..namecheck import load_name_check, unresolved
from ..names import (
    abbreviates_a_cast_name,
    cast_list,
    entity_tokens,
    malformed_cast_entries,
    mask_cast_names,
)
from .runner import fail, ok, skip, truncated, warn

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
        return fail("3", "annotation-string leakage", truncated(hits) + _fallback_note(replaced))
    return ok("3", "annotation-string leakage", f"{len(files)} files clean" + _fallback_note(replaced))


def gate_04_vocabulary(ctx):
    files = ctx.blind_files()
    if not files:
        return skip("4", "blind-tree vocabulary sweep", f"{ctx.blind_root} absent or empty")
    hits, replaced = _hits(files, ANSWER_KEY_NOUNS, finding_id_pattern(ctx.conf))
    if hits:
        return fail("4", "blind-tree vocabulary sweep", truncated(hits) + _fallback_note(replaced))
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
            truncated(hits)
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
        return fail("12", "answer-key containment", truncated(hits) + _fallback_note(replaced))
    return ok(
        "12",
        "answer-key containment",
        f"no reference to {key_root}/ in {len(files)} files" + _fallback_note(replaced),
    )


def gate_14_unchecked_names(ctx):
    """Entity-shaped tokens in the blind tree that no name check cleared.

    A safety net for company names that entered the room during authoring
    without going through the name-collision check — not a proof; the
    primary control is /vdr-scope checking every fact-sheet name at Gate A.

    Every known cast name is masked out of each file's text and name FIRST,
    and the residue is then scanned. Whatever the regex finds in the
    residue is genuinely unknown, so no rule is needed to tell an ordinary
    capitalised word preceding a registered name from part of that name —
    the registered name is already gone. See synthvdr/names.py's module
    docstring for the three extract-then-filter versions this replaced and
    the residual it accepts. The filename is masked on the same terms as
    the content, since the sweep is the same regex over the same kind of
    text.

    Only the cast's entity rows are masked, and only they are vetted for
    hygiene — see `cast_list` for why a person row is an active hazard here
    and a mononym is not a defect.

    A malformed cast list is rejected before any file is read: a single-word
    or bare-suffix entity row would blanket-mask a whole suffix family, and
    a gate that silently cannot fail is worse than one that reports the row.

    TWO SHAPES MASKING CANNOT REACH, both handled in the safe direction. A
    document that ABBREVIATES a checked name — an org chart's box too narrow
    for "Helmswick Imaging Limited" holding "Imaging Ltd" — never contains
    the full name to be masked, so `abbreviates_a_cast_name` filters the
    residue for candidates that merely shorten a cast entry; only a
    candidate no longer than that entry is dropped, so the different, longer
    name the names module's docstring warns about still fails. And "Limited"
    is the one corporate suffix that is also an ordinary English adjective,
    which had this gate reporting "a Private Limited Company" and
    "Independent Limited Assurance Report" as unchecked companies; that is
    handled one register down, in `entity_tokens`, against a closed list of
    the words the adjective qualifies.

    THE RECORDED VERDICTS ARE READ, NOT JUST THE NAMES. Until this gate did
    that, nothing in `synthvdr/` ever looked at name-check.md's Verdict
    column: `cast_list` takes columns 1 and 2 (Name, Kind) and stops, so a
    name the check had positively found to COLLIDE with a real company was
    masked out exactly like a cleared one and the room shipped. /vdr-scope
    calls a collision "a hard block ... there is no sign-off that waives a
    collision", and `namecheck.unresolved()` was written to find them — but
    it had no caller anywhere outside the tests, which left Gate A's hardest
    rule enforced by prose alone, through `/vdr-qa --strict` and
    `/vdr-package --strict` alike. Same shape as gate 17's own history: a
    real check, written and tested, wired to nothing.

    The two outcomes are deliberately different, and they track what
    /vdr-scope actually says rather than flattening both into one verdict:

      * `collision` FAILS. It is the hard block, and there is no
        acknowledgement that waives it — the name must be regenerated.
      * any OTHER non-clear verdict (`ambiguous`, the `unchecked` that
        /vdr-scope writes when WebSearch was unavailable, or a value that is
        simply a typo) WARNS. Gate A does not block automatically on these,
        but requires the user's explicit acknowledgement, so the gate's job
        is to make sure the room can never quietly forget one — WARN is
        counted in the runner's summary without failing a build over a risk
        the user is entitled to accept. Anything not spelled `clear` lands
        here, which is the safe direction for a typo.

    The verdict read runs BEFORE the blind-tree guard below, deliberately: a
    recorded collision is a defect in the answer key's own record, true
    whether or not a single document has been authored yet, and gating it on
    the corpus existing would hide it for the whole of /vdr-findings.
    """
    name_check = ctx.key_root / "name-check.md"
    if not name_check.is_file():
        return skip("14", "unchecked names", "_key/name-check.md absent — run /vdr-scope name check")

    outstanding = unresolved(load_name_check(name_check))
    collisions = [v for v in outstanding if v.verdict == "collision"]
    if collisions:
        return fail(
            "14",
            "unchecked names",
            "_key/name-check.md records a collision for "
            + truncated([repr(v.text) for v in collisions], sep=", ")
            + " — /vdr-scope Gate A treats a collision as a hard block with no sign-off that"
            " waives it: invent a replacement name, re-check it, and rebuild",
        )
    # Held rather than returned: an unchecked name in the corpus is the more
    # fundamental problem and gets to speak first if both are true. Appended
    # to whatever this gate concludes below so it can never be dropped.
    unresolved_note = ""
    if outstanding:
        named = ", ".join(f"{v.text!r} ({v.verdict})" for v in outstanding)
        unresolved_note = (
            f"; {len(outstanding)} name(s) not cleared: {named} — not an automatic block,"
            " but Gate A requires the user's explicit acknowledgement of each"
        )

    # Entity rows only, stated explicitly at the call site because it is
    # load-bearing rather than a default worth inheriting silently: a person
    # row would delete its own words out of the document text and blind the
    # sweep to an unchecked entity standing behind them.
    cast = cast_list(name_check, kind="entity")
    malformed = malformed_cast_entries(cast)
    if malformed:
        return fail(
            "14",
            "unchecked names",
            "malformed cast row(s) in _key/name-check.md: "
            + truncated([repr(name) for name in malformed], sep=", ")
            + " — a single-word or bare-suffix entity row masks every name ending in it;"
            " regenerate the name check from the fact sheet"
            + unresolved_note,
        )
    files = ctx.blind_files()
    if not files:
        return skip("14", "unchecked names", f"{ctx.blind_root} absent or empty" + unresolved_note)
    unchecked: Set[str] = set()
    replaced = 0
    for path in files:
        try:
            text, needed_fallback = _read_lossy(path)
        except OSError:
            continue
        if needed_fallback:
            replaced += 1
        unchecked |= entity_tokens(mask_cast_names(text, cast))
        unchecked |= entity_tokens(mask_cast_names(path.name, cast))
    # An org chart's box, or a schedule whose heading has already said the
    # group name, holds the name shortened: "Imaging Ltd" for a cast
    # "Helmswick Imaging Limited". Masking cannot reach that — the full name
    # is not in the text to remove — so the abbreviation is filtered here,
    # after the sweep. Only a candidate no LONGER than the cast entry it
    # tails is dropped, which is the opposite of the rejected walk the
    # names module documents; see `abbreviates_a_cast_name`.
    unchecked = {name for name in unchecked if not abbreviates_a_cast_name(name, cast)}
    if unchecked:
        detail = truncated(sorted(unchecked), sep=", ") + " — not on the cast list; check or remove"
        return fail("14", "unchecked names", detail + _fallback_note(replaced) + unresolved_note)

    cleared = (
        f"{len(cast)} entity cast name{'' if len(cast) == 1 else 's'}, none unchecked"
        + _fallback_note(replaced)
    )
    # WARN rather than PASS, so a room carrying an ambiguous or never-searched
    # name cannot read as a clean sweep in the one line most people skim.
    if outstanding:
        return warn("14", "unchecked names", cleared + unresolved_note)
    return ok("14", "unchecked names", cleared)
