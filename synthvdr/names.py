"""Entity and cast-name helpers.

Deliberately NOT in qa/leakage.py: Task 14's namecheck module needs these, and a
top-level module must not import from the gate package — synthvdr/qa/__init__.py
imports every gate, so that dependency would drag the whole suite in.

HOW AN UNCHECKED NAME IS FOUND, AND WHY IT IS DONE BACKWARDS. Gate 14 does not
extract candidate names from prose and then ask whether each one is on the cast
list. Three earlier versions did, and each carried both error classes at once:
the regex absorbs whatever capitalised words happen to precede a name ("See
Kessler Werke GmbH"), so a rule is needed to tell an ordinary leading word from
part of a name — and that is named-entity recognition, which no word-count rule
or stoplist performs. A determiner stoplist handled "The" and missed "See",
"Under", "Per" and "Registered". An unbounded trailing-sub-phrase walk killed
those false positives but let a cast entry of "Holdings Limited" cover the
different, unchecked "Ashfell Trading Holdings Limited". Bounding that walk to
one leading word only shrank both windows to two words.

So the operation is inverted: `mask_cast_names` removes every known cast name
from the text FIRST, and `entity_tokens` then scans the residue. A registered
name is gone before the regex ever runs, so no number of ordinary leading words
can manufacture a candidate — the false-positive class goes structurally, with
no stoplist and no bound. Whatever the regex still finds carries a corporate
suffix that no cast entry accounts for, which is the definition of unchecked.

ACCEPTED RESIDUAL. If a genuine cast entry is a strict trailing sub-phrase of a
different entity's full name — cast "Holdings Limited", room text "Ashfell
Trading Holdings Limited" — masking removes the shorter name and the longer,
unchecked one no longer carries a suffix, so it is not flagged. This is not
chased, for two reasons. Guarding the mask's left edge (refuse to mask when a
capitalised word precedes the occurrence) reopens the exact false-positive class
above, since "See" and "Registered" are capitalised too. And gate 14 is a safety
net, not a proof: the primary control is /vdr-scope checking every fact-sheet
name at Gate A, and invented fact-sheet casts do not produce nested names. The
degenerate end of the same shape — a one-word or bare-suffix cast row, which
would blanket-mask a whole suffix family — is malformed input rather than a
matching problem, and `malformed_cast_entries` makes gate 14 reject it loudly
instead of silently degrading into a gate that cannot fail.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Set

ENTITY_SUFFIXES = (
    "Limited", "Ltd", "PLC", "LLP", "LLC", "Inc", "Incorporated",
    "GmbH", "AG", "SAS", "SARL", "SA", "BV", "NV", "AB", "Oy", "SpA", "KK",
)

_SUFFIX_ALTERNATION = "|".join(re.escape(s) for s in ENTITY_SUFFIXES)
_SUFFIXES_LOWER = frozenset(s.lower() for s in ENTITY_SUFFIXES)

# Inter-word separation is spaces and tabs only, not \s — an entity name
# does not span a paragraph, and \s would let the run cross a blank line
# (or any line break), joining a heading to the sentence below it into one
# false candidate ("Supply\n\nSee Kessler Werke GmbH").
_ENTITY = re.compile(
    r"\b((?:[A-Z][\w&'’-]*[ \t]+){1,4}(?i:" + _SUFFIX_ALTERNATION + r"))\b"
)


def entity_tokens(text: str) -> Set[str]:
    """Capitalised phrases ending in a corporate suffix.

    Matches greedily and makes no attempt to tell a genuine leading word of
    a name from an ordinary word that merely precedes one ("See Kessler
    Werke GmbH", "Under Kessler Werke GmbH", "Per Ashfell Holdings
    Limited") — there is no closed list of words that might come before a
    name, so a version of this function that tried to exclude them one
    spelling at a time would always be one word behind the next document
    that plants a new one. Callers that care about the difference remove
    the names they already know with `mask_cast_names` BEFORE calling this,
    rather than asking this function to guess; it stays a plain,
    context-free matcher. The suffix is matched case-insensitively
    (Limited/limited, GmbH/GMBH all count).
    """
    return {match.group(1).strip() for match in _ENTITY.finditer(text)}


def mask_cast_names(text: str, cast: Set[str]) -> str:
    """Remove every known cast name from `text`, longest entry first.

    Run this before `entity_tokens` and what comes back carries only names
    the cast list does not account for — see the module docstring for why
    the operation is inverted rather than filtered after the fact.

    Two details are load-bearing. Entries are masked LONGEST FIRST so a
    shorter one cannot pre-empt a longer one that contains it: masking
    "Holdings Limited" out of "Ashfell Holdings Limited" would strand
    "Ashfell" in the residue, where it can fuse with the capitalised words
    beside it and be reported as part of a name the document never
    contained. And each name is replaced by a SPACE rather than the empty
    string, so the words either side of a removed name cannot fuse into a
    candidate of their own.

    Matching allows any run of spaces or tabs between a name's words, since
    a name wrapped across a table cell or padded in a document is the same
    name as the one the cast list writes with single spaces.
    """
    for name in sorted(cast, key=lambda entry: (-len(entry), entry)):
        words = name.split()
        if not words:
            continue
        text = re.sub(r"[ \t]+".join(re.escape(word) for word in words), " ", text)
    return text


def malformed_cast_entries(cast: Set[str]) -> List[str]:
    """Cast rows no name check could legitimately have produced, sorted.

    A row that is a bare corporate suffix ("GmbH") or any single word would
    blanket-mask every entity ending in it, turning gate 14 into a gate
    that cannot fail — "Ashfell Trading GmbH" comes back clean against a
    cast of {"GmbH"}. The cast list is generated from the fact sheet by the
    name check, so a one-word row means that process went wrong; the right
    response is to reject it by name, not to compensate for it in the
    matcher. Both conditions are stated even though every current entry in
    ENTITY_SUFFIXES is a single token, so adding a two-word suffix later
    cannot quietly open the hole back up.
    """
    return sorted(
        name
        for name in cast
        if len(name.split()) < 2 or name.strip().lower() in _SUFFIXES_LOWER
    )


def cast_list(path: Path) -> Set[str]:
    """Names from the first column of a pipe table (the name-check record)."""
    names: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and cells[0] and cells[0].lower() != "name":
            names.add(cells[0])
    return names
