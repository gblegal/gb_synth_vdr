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

A SECOND RESIDUAL, and the one rule that reaches it. Masking also cannot see a
name the document never spells in full: an org chart's box holds "Imaging Ltd"
where the cast says "Helmswick Imaging Limited". `abbreviates_a_cast_name`
drops a candidate that is a TRAILING sub-phrase of a cast entry — the opposite
containment to the rejected walk above, and the reason it is safe where that
was not; see its docstring for what it gives up in exchange.

The rest is not chased. Guarding the mask's left edge (refuse to mask when a
capitalised word precedes the occurrence) reopens the exact false-positive class
above, since "See" and "Registered" are capitalised too. And gate 14 is a safety
net, not a proof: the primary control is /vdr-scope checking every fact-sheet
name at Gate A — which now also runs this same mask-then-scan pass over the
fact sheet against the record it just wrote, so a name present in the prose but
missing from or miscategorised in the record surfaces before any document
inherits it — and invented fact-sheet casts do not produce nested names. The
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

# Separator allowed between the words of a name: any run of spaces and tabs,
# optionally spanning ONE line break. Never empty, and never two breaks — one
# break is a wrap, two is a paragraph or table-row boundary. Shared by
# `entity_tokens` and `mask_cast_names`, which must agree about what a name
# looks like: they are two halves of one mechanism (mask, then scan the
# residue), and while `mask_cast_names` alone spanned a wrap the scan could
# report a name the mask had been handed.
_WITHIN_NAME = r"(?:[ \t]*\n[ \t]*|[ \t]+)"

# The words a corporate suffix is followed by when it is NOT a corporate
# suffix at all. "Limited" is the one entry in ENTITY_SUFFIXES that is also an
# ordinary English adjective, and read as an adjective it qualifies the noun
# after it: "a Private Limited Company", "Independent Limited Assurance
# Report", "Private Company Limited by Shares" (Companies House's own
# boilerplate on a certificate of incorporation). Each of those puts a
# capitalised word in front of "Limited" and so matched as a company name that
# was never named; one XS-scale build spent several remediation edits keeping
# ordinary prose out of gate 14 for exactly this reason.
#
# WHY A CLOSED LIST OF FOLLOWING WORDS AND NOT "ANY CAPITALISED WORD AFTER THE
# SUFFIX". The tempting rule — a legal name ends at its suffix, so a capital
# after it means the phrase continues — is broader, and broader in the unsafe
# direction: it would also swallow "<Unchecked Name> Limited Retirement
# Benefits Scheme", which is how a pension scheme, a share plan or a document
# title routinely names the company it belongs to, and a counterparty named
# only in that shape would pass the gate in silence. This list can only ever
# be one word behind the next ordinary phrase, and being one word behind
# leaves a FALSE POSITIVE, which is the direction this gate is deliberately
# biased in: a real uncoined counterparty slipping through is much worse.
# Extend it only with words that follow the ADJECTIVE "limited" in ordinary
# legal or financial English — never with a word that could follow a company
# name.
_ADJECTIVAL_CONTINUATIONS = (
    "by", "company", "companies", "liability", "partnership", "partnerships",
    "assurance", "recourse", "warranty", "warranties", "edition",
)
_NOT_A_SUFFIX_AFTER_ALL = (
    "(?!" + _WITHIN_NAME
    + "(?i:" + "|".join(_ADJECTIVAL_CONTINUATIONS) + r")\b)"
)

# Inter-word separation spans at most ONE line break, never a blank line — an
# entity name does not span a paragraph, and \s would let the run cross any
# amount of whitespace, joining a heading to the sentence below it into one
# false candidate ("Supply\n\nSee Kessler Werke GmbH"). One break it must
# span: markdown prose wraps, so a long name arrives split mid-phrase as a
# matter of course, and while this pattern was spaces-and-tabs only such a
# name was not extracted at all — the fact sheet's belt-and-braces net went
# blind to it, and the workaround was a rule that authors keep entity names on
# one line, which is a constraint the tool should absorb.
_ENTITY = re.compile(
    r"\b((?:[A-Z][\w&'’-]*" + _WITHIN_NAME + r"){1,4}"
    + _SUFFIX_ALTERNATION + r")\b" + _NOT_A_SUFFIX_AFTER_ALL
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

    A match may span a line break, because markdown prose wraps; the token
    that comes back never does. Whitespace inside it is normalised to
    single spaces, and a leading "The" is dropped, because the token is a
    KEY — it is compared against the cast list, reported by gate 14, and
    written into the name check, all of which spell a name one way. Left
    alone, "The Helmswick Group Limited" was checked, recorded and searched
    as a different name from "Helmswick Group Limited", and a wrapped name
    could not be written into the name-check table at all (a line break in
    the Name column does not survive the round trip — see
    `namecheck._validate_name_for_render`). "The" is dropped only where a
    capitalised word still remains in front of the suffix: "The Limited" is
    not a determiner and a name, and stripping it would leave a bare
    suffix, which is not a candidate at all.
    """
    return {_as_key(match.group(1)) for match in _ENTITY.finditer(text)}


def _as_key(raw: str) -> str:
    words = raw.split()
    if len(words) > 2 and words[0].lower() == "the":
        words = words[1:]
    return " ".join(words)


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

    Matching allows any run of spaces or tabs between a name's words, and a
    single line break, since a name wrapped across a table cell or padded
    in a document is the same name as the one the cast list writes with
    single spaces. The line break is not a nicety: markdown prose wraps, so
    a long entity name arrives split mid-phrase as a matter of course, and
    while the separator was spaces-and-tabs only the half after the break
    kept its corporate suffix and came back as an unchecked name. One XS
    build produced nine such false positives across three rounds, and hit
    its own fact sheet at /vdr-scope before a single document existed.

    `_WITHIN_NAME`'s exact shape carries both bounds. It cannot match the
    empty string, so a cast entry never masks a run-together word it has no
    business touching, and it admits ONE newline rather than an unbounded run
    of whitespace — one break is a wrap, two is a paragraph or table-row
    boundary, and a mask that jumps a boundary deletes text on both sides of
    a break the cast entry never spanned.

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
            _WITHIN_NAME.join(re.escape(word) for word in words),
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


# Suffix spellings that name the same thing. Only these two pairs: every
# other entry in ENTITY_SUFFIXES is a distinct legal form, not a shorthand
# for another one.
_SUFFIX_SYNONYMS = {"ltd": "limited", "inc": "incorporated"}


def _canonical_words(name: str) -> List[str]:
    words = [word.lower() for word in name.split()]
    if words:
        words[-1] = _SUFFIX_SYNONYMS.get(words[-1], words[-1])
    return words


def abbreviates_a_cast_name(candidate: str, cast: Set[str]) -> bool:
    """True if `candidate` is a checked name, shortened.

    An org chart abbreviates: a box too narrow for "Helmswick Imaging
    Limited" holds "Imaging Ltd", and a schedule that has already said
    "Helmswick" in its heading says "Clinics Ltd" in the rows below. Both
    are entity-shaped, neither is on the cast list verbatim, and masking
    cannot help — the text does not contain the full name to remove. Gate
    14 reported them as unchecked names, and an author fixed the room
    rather than the gate, which is the wrong way round.

    A candidate counts as an abbreviation when it is a TRAILING sub-phrase
    of a cast entry, reading "Ltd" and "Limited" (and "Inc" and
    "Incorporated") as the same suffix so a shortened suffix still matches.

    THE DIRECTION IS LOAD-BEARING, and it is the opposite of the trailing
    sub-phrase walk the module docstring records as rejected. There, a
    SHORT cast entry ("Holdings Limited") excused a LONGER unchecked name
    ("Ashfell Trading Holdings Limited") — a different company, waved
    through. Here only a candidate no longer than the cast entry is
    excused, so that name still fails the gate. What is accepted instead is
    the residual that a genuinely unchecked entity whose whole name is a
    tail of a checked one goes unreported ("Imaging Limited" behind cast
    "Helmswick Imaging Limited"). That tail is by construction the generic
    end of an invented name — this project's rooms build entity names on a
    distinctive leading token, which is exactly the part an abbreviation
    drops — and `malformed_cast_entries` already refuses the degenerate
    case that would make the rule dangerous, a one-word or bare-suffix cast
    row, which would otherwise excuse every name ending in that suffix.
    """
    words = _canonical_words(candidate)
    if len(words) < 2:
        return False
    for entry in cast:
        entry_words = _canonical_words(entry)
        if len(words) <= len(entry_words) and entry_words[-len(words):] == words:
            return True
    return False


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
