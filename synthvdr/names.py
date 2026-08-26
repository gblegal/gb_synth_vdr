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

ACCEPTED RESIDUAL, in its full form. Masking hides an unchecked entity whenever,
after masking, no capitalised word remains immediately before its suffix. That
condition is reachable two ways: a cast entry covering the suffix itself (cast
"Holdings Limited" against the different, unchecked "Ashfell Trading Holdings
Limited"), or cast entries covering ALL the capitalised words in front of the
suffix ("Daniel Oyelaran" plus the bare "Ltd" that follows it). Scoping
`cast_list` to entity rows removes the routine generator of the second shape,
since the name check emits person names for exactly those front words. Where
both nested names are genuine cast entries, hiding one is harmless — both are
checked, and longest-first masking removes the container before the part.

The rest is not chased. Guarding the mask's left edge (refuse to mask when a
capitalised word precedes the occurrence) reopens the exact false-positive class
above, since "See" and "Registered" are capitalised too. And gate 14 is a safety
net, not a proof: the primary control is /vdr-scope checking every fact-sheet
name at Gate A, and invented fact-sheet casts do not produce nested names. The
degenerate end of the same shape — a one-word or bare-suffix ENTITY row, which
would blanket-mask a whole suffix family — is malformed input rather than a
matching problem, and `malformed_cast_entries` makes gate 14 reject it loudly
instead of silently degrading into a gate that cannot fail. A one-word PERSON
row is an ordinary mononym and reaches neither check, because the cast the gate
masks with and vets is entity-scoped.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Set

ENTITY_SUFFIXES = (
    "Limited", "Ltd", "PLC", "LLP", "LLC", "Inc", "Incorporated",
    "GmbH", "AG", "SAS", "SARL", "SA", "BV", "NV", "AB", "Oy", "SpA", "KK",
)

# "SpA" is matched in its exact canonical case ONLY — never case-insensitively
# like every other entry above. Every other suffix's all-lower and all-upper
# renderings ("gmbh"/"GMBH", "ltd"/"LTD", "sa"/"SA", "oy"/"OY", ...) are not
# ordinary English words or business jargon, so reading them as a suffix
# whatever their case is safe and is what Task 9 explicitly fixed this
# function to do ("Kessler Werke GMBH" and "Ashfell Trading limited" must
# both be detected). "SpA" is different: its lowercase form "spa" is an
# ordinary English word (a leisure spa) and its upper-case form "SPA" is the
# standard M&A abbreviation for a Share Purchase Agreement — this project's
# own domain pack has a subsection literally named "draft-spa" for authoring
# one, so a case-insensitive match here false-flags completely ordinary
# document headings and prose ("Draft spa", "the SPA", "New SPA draft") on
# every fresh room. Restricting to the one distinctive, cap-lower-cap
# rendering "SpA" — how the Italian "società per azioni" suffix is actually
# written — keeps real detections ("Kessler Werke SpA") while dropping the
# false ones, without touching case-insensitivity for any other suffix.
_CASE_SENSITIVE_SUFFIXES = ("SpA",)
_CASE_INSENSITIVE_SUFFIXES = tuple(s for s in ENTITY_SUFFIXES if s not in _CASE_SENSITIVE_SUFFIXES)

_SUFFIX_ALTERNATION = (
    "(?:(?i:" + "|".join(re.escape(s) for s in _CASE_INSENSITIVE_SUFFIXES) + ")"
    "|" + "|".join(re.escape(s) for s in _CASE_SENSITIVE_SUFFIXES) + ")"
)
_SUFFIXES_LOWER = frozenset(s.lower() for s in ENTITY_SUFFIXES)

# Inter-word separation is spaces and tabs only, not \s — an entity name
# does not span a paragraph, and \s would let the run cross a blank line
# (or any line break), joining a heading to the sentence below it into one
# false candidate ("Supply\n\nSee Kessler Werke GmbH").
_ENTITY = re.compile(
    r"\b((?:[A-Z][\w&'’-]*[ \t]+){1,4}" + _SUFFIX_ALTERNATION + r")\b"
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
    (Limited/limited, GmbH/GMBH all count) EXCEPT "SpA", which is matched
    only in its exact canonical case — see the module-level comment above
    `_CASE_SENSITIVE_SUFFIXES` for why: unlike every other suffix here, its
    lower- and upper-case forms ("spa", "SPA") are ordinary English/business
    words in their own right.
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

    Matching is also CASE-INSENSITIVE, for the same reason and to the same
    end as `entity_tokens` matching its suffixes that way. The two are one
    mechanism — mask, then scan the residue — and while they disagreed
    about case the scan could report a name the mask had been handed. An
    execution block ("SIGNED for and on behalf of ASHFELL HOLDINGS
    LIMITED", which is how a deed conventionally signs) failed to mask
    against the cast's "Ashfell Holdings Limited", then matched
    `entity_tokens`' own case-insensitive suffix and came back as an
    unchecked name. So did any half-way rendering: "Ashfell Holdings
    LIMITED", "Kessler Werke GMBH".

    This widens what a cast entry deletes, which is worth stating against
    the accepted residual in the module docstring — but it cannot widen it
    into a new blind spot, because every extra occurrence it now removes is
    a rendering of a name that was already checked. What the residue loses
    is text `entity_tokens` would have attributed to a registered entity
    anyway; a genuinely unknown name in any case still survives to be
    reported, which the two all-caps controls in
    `test_gate_14_masks_the_cast_before_scanning_the_residue` pin.
    """
    for name in sorted(cast, key=lambda entry: (-len(entry), entry)):
        words = name.split()
        if not words:
            continue
        text = re.sub(
            r"[ \t]+".join(re.escape(word) for word in words),
            " ",
            text,
            flags=re.IGNORECASE,
        )
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


def cast_list(path: Path, kind: str = "entity") -> Set[str]:
    """Names from the first column of a pipe table (the name-check record),
    restricted by default to the rows whose Kind cell reads "entity".

    The Kind filter is not cosmetic. The name check emits person rows by
    design, and under masking a cast row is an active deletion from the
    document text rather than an inert thing to compare against: person
    rows named "Marta Vinceau" and "Daniel Oyelaran" would delete every
    capitalised word in front of the suffix in "A deed with Daniel Oyelaran
    Ltd", and the gate would go blind to an unchecked entity. Person rows
    were never needed here in the first place — `entity_tokens` cannot emit
    a name without a corporate suffix, so a person's name was never going
    to become a candidate. The residual risk runs the safe way for this
    gate: an entity row miscategorised as a person is simply not masked,
    which produces a false positive rather than a silent miss.

    Pass kind=None (or "") to take every row regardless of Kind.

    Separator rows are skipped by testing the first cell's character set,
    the same guard namecheck.load_name_check uses, because the tighter
    startswith("|---") test this replaced missed the spaced form
    ("| --- | --- |") that a markdown formatter produces — read as a name
    it is a one-word row, which then surfaced as a hygiene failure naming
    '---'.
    """
    names: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or not cells[0] or cells[0].lower() == "name":
            continue
        if set(cells[0]) <= {"-", ":"}:
            continue
        if kind and (len(cells) < 2 or cells[1].lower() != kind.lower()):
            continue
        names.add(cells[0])
    return names
