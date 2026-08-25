"""Entity and cast-name helpers.

Deliberately NOT in qa/leakage.py: Task 14's namecheck module needs these, and a
top-level module must not import from the gate package — synthvdr/qa/__init__.py
imports every gate, so that dependency would drag the whole suite in.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set

ENTITY_SUFFIXES = (
    "Limited", "Ltd", "PLC", "LLP", "LLC", "Inc", "Incorporated",
    "GmbH", "AG", "SAS", "SARL", "SA", "BV", "NV", "AB", "Oy", "SpA", "KK",
)

# A small, closed set of English determiners — not a general stopword list.
# Excluded from EVERY repetition of the word-run below, not just the first:
# a heading followed by a blank line followed by a determiner-led sentence
# ("# Articles\n\nThe Ashfell Holdings Limited...") lets "Articles" start
# the run legitimately, and a guard that only checked the match's start
# would then let "The" slide in as the run's second word. Guarding each
# repetition instead means no word anywhere in the run can be a bare
# determiner, so the run breaks — and restarts one word later — the moment
# it reaches one, regardless of how many capitalised words preceded it.
_LEADING_STOPWORDS = ("The", "A", "An", "This", "That", "These", "Those")

_SUFFIX_ALTERNATION = "|".join(re.escape(s) for s in ENTITY_SUFFIXES)
_STOPWORD_ALTERNATION = "|".join(re.escape(w) for w in _LEADING_STOPWORDS)

_ENTITY = re.compile(
    r"\b((?:(?!(?:" + _STOPWORD_ALTERNATION + r")\b)[A-Z][\w&'’-]*\s+){1,4}"
    r"(?i:" + _SUFFIX_ALTERNATION + r"))\b"
)


def entity_tokens(text: str) -> Set[str]:
    """Capitalised phrases ending in a corporate suffix.

    The suffix is matched case-insensitively (Limited/limited, GmbH/GMBH
    all count) so recognition does not depend on a document happening to
    spell the suffix in the canonical casing — case variants are handled by
    folding the match, not by adding one more spelling to ENTITY_SUFFIXES
    each time a new one is noticed.
    """
    return {match.group(1).strip() for match in _ENTITY.finditer(text)}


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
