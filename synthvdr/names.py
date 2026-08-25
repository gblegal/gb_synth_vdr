"""Entity and cast-name helpers.

Deliberately NOT in qa/leakage.py: Task 14's namecheck module needs these, and a
top-level module must not import from the gate package — synthvdr/qa/__init__.py
imports every gate, so that dependency would drag the whole suite in.
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
    that plants a new one. That reconciliation is the cast list's job (see
    `covered_by_cast`), not this function's: it stays a plain, context-free
    matcher. The suffix is matched case-insensitively (Limited/limited,
    GmbH/GMBH all count).
    """
    return {match.group(1).strip() for match in _ENTITY.finditer(text)}


def trailing_subphrases(candidate: str) -> List[str]:
    """`candidate`'s trailing word sub-phrases, longest to shortest.

    For "See Kessler Werke GmbH": ["See Kessler Werke GmbH", "Kessler Werke
    GmbH", "Werke GmbH", "GmbH"]. Internal whitespace is normalised via
    `str.split()` before re-joining with a single space, since a candidate
    captured by entity_tokens may carry a tab between words where a
    cast-list entry is written with a plain space.
    """
    words = candidate.split()
    return [" ".join(words[i:]) for i in range(len(words))]


def covered_by_cast(candidate: str, cast: Set[str]) -> bool:
    """True if some trailing sub-phrase of `candidate` is on the cast list.

    This is the property that replaces trying to guess which leading words
    can precede a real name: a candidate is accounted for the moment ANY
    suffix of its word sequence — down to its last word alone — matches an
    entry the cast list already vouches for, regardless of what leading
    word(s) the regex greedily pulled in ahead of it. A candidate is only
    "unchecked" when none of its trailing sub-phrases is on the list.
    """
    return any(phrase in cast for phrase in trailing_subphrases(candidate))


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
