"""Name-collision checking.

This module extracts candidate names and keeps the durable record. The searching
itself happens in the /vdr-scope skill, because only the agent has WebSearch.

THREE SOURCES, NOT ONE. The spec requires every invented proper noun — company,
brand, product, site, domain — to be extracted, not just the ones that happen to
carry a corporate suffix. Brands, products, sites and domains have no corporate
suffix and no cast row, so a suffix-only extractor never sees them. The fix is
declaration, not inference: the fact sheet states its invented names explicitly
in an `## Invented names` table (`| Name | Kind |`), and that table is read as a
first-class source. This project already spent five review rounds learning that
an open-ended proper-noun extractor false-positives on ordinary prose (see
synthvdr/names.py's module docstring); a declared table is a statement of
authorial intent, and scanning free prose for capitalised words is a guess. Do
not add prose scanning here.

`entity_tokens` (corporate-suffix matching) stays on as the belt-and-braces net
for a name that reaches the fact sheet without being declared — it is the only
source that does not depend on the author remembering to declare something.
`## Cast` table rows are always tagged "person".

COLLISION VS NOTABILITY. Entities and people take different tests, and this is
the reason the Kind column exists at all. An invented scandal landing on a real
company is the actual risk this module protects against, so every candidate
that is NOT a person — entity, brand, product, site, domain — takes a collision
check: does a real company/brand/product/site/domain of this name already
exist? A person takes a notability check only: is this a public figure? — not a
collision check, because every plausible surname exists somewhere and treating
an unremarkable name as a hit would make the check impossible to pass. Getting
this tagging wrong is not cosmetic: `cast_list(path, kind="entity")` (see
synthvdr/names.py) filters this very record by its Kind column, and gate 14's
masking depends on it. A person mistagged as an entity is masked away and gate
14 goes blind to a genuinely unchecked name standing behind it; an entity
mistagged as a person is simply not masked, which only produces a false
positive. Tag conservatively when in doubt: as an entity, not a person.

A name can arrive from more than one source (declared AND picked up by
entity_tokens from the prose, say). It is emitted once. Precedence: the
declared `## Invented names` table wins, because it is authorial intent;
failing that, "entity" beats "person", because being masked and searched as a
company is the conservative treatment for gate 14.

TWO WAYS THE FACT SHEET CAN BE WRONG, AND WHY BOTH RAISE. `## Cast` rows are
people by definition. If `## Invented names` also declares that same text with
a non-person kind, the fact sheet is self-contradictory, and "declared table
wins" would resolve it silently — masking that name out of the room as an
entity and blinding gate 14 to genuinely unchecked names sitting next to it,
exactly the hazard synthvdr/names.py:139-163 documents. There is no correct
side to pick, so `extract_candidates` raises `NameCheckError` instead of
choosing: the author must resolve the contradiction, and declaring the row as
Kind "person" is how they say the overlap with `## Cast` is intentional
rather than an error. Separately, a Kind that is blank or not one of the six
recognised values also raises: the collision-vs-notability split below is
undefined for a kind nothing recognises, and gate 14's fail-safe (not masking
an unrecognised kind) is not a reason to let a typo through quietly here. Both
raise eagerly rather than collecting into a report, because `/vdr-scope` calls
`extract_candidates` while drafting — a self-contradictory or meaningless fact
sheet should stop the skill immediately, the same ruling already applied to
gate 13's self-contradictory fact sheet elsewhere in this project.

FIVE MORE GUARDS, ONE PRINCIPLE: KEYS ARE EXACT-OR-REJECT, COMMENTARY IS
SANITISED. (The fifth, the closed verdict vocabulary, is neither a key nor
commentary but a third category — see `VERDICTS` — and it obeys the same
principle: the renderer rejects rather than guesses.) A name declared twice in `## Invented names` with two different
kinds is the same contradiction as the declared-vs-cast case above, just
between two rows of the same table instead of two tables — "declared table
wins" cannot arbitrate it either, because both sides ARE the declared table,
so `_declared_candidates` raises `NameCheckError` rather than letting
last-wins silently pick one (an identical kind repeated is harmless and does
not raise).

Separately, `render_name_check_md` rejects a `Verdict.text` the pipe-table
format cannot carry as a key. THE CHECK IS THE ROUND TRIP ITSELF, NOT A LIST
OF FORBIDDEN SHAPES — an earlier version enumerated shapes (a literal `|`;
text made only of `-`/`:` characters, or empty) and missed the actual
boundary: `_would_vanish_as_separator` tested the raw text while the loader
tests the STRIPPED cell, and `str.splitlines()` additionally treats a bare
`\r` as a line break, so ' - ', '   ---   ', and a name containing `\n` or
`\r` all rendered and then silently vanished on load — none matched the
enumerated shapes, but all of them failed the round trip. The guard now
renders the one row, re-parses it with `_read_verdict_row` — the exact
function `load_name_check` uses, so the two can never drift apart the way
`_would_vanish_as_separator` and the loader did — and raises unless the
recovered Name equals the input. This closes every bypass of the class at
once, including ones not yet found, and it is self-maintaining: a future
change to `_read_verdict_row` automatically tightens the guard rather than
opening a new gap. The specific messages below (empty, separator-shaped,
contains a pipe, contains a line break) exist so an author learns why a name
was rejected; they are diagnosis, not the check.

A `Verdict.note`, by contrast, is free-text commentary written by the search
skill at build time, not a key, so a `|` in it is sanitised (replaced with
`/`) rather than rejected — breaking a build over a cosmetic character in a
comment is worse than substituting it, and the substitution must not
truncate the note the way emitting the raw pipe would have.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List

from .names import entity_tokens

# The four values `_key/name-check.md` may record, and the only ones
# `render_name_check_md` will write. Three of them are search OUTCOMES, which
# is how /vdr-scope introduces them ("Record each result as `clear`,
# `collision`, or `ambiguous`"); `unchecked` is not an outcome at all but the
# record of a search that could not happen, written when WebSearch is
# unavailable in the scoping session. It belongs here for the reason
# /vdr-scope gives for demanding it — "never leave the record silent about a
# name that was never actually checked" — and it was missing from this tuple
# for as long as the tuple went unreferenced by anything.
#
# ENFORCED AT WRITE, NOT AT READ. `render_name_check_md` rejects a verdict
# outside this tuple; `load_name_check` deliberately does not. Gate 14
# (`qa.leakage.gate_14_unchecked_names`) treats anything not spelled `clear`
# as a WARN precisely so a typo lands somewhere safe rather than being read
# as a pass, and a validating loader would turn that designed WARN into a
# crash on a hand-edited file. Reject what we write; warn about what we read.
VERDICTS = ("clear", "collision", "ambiguous", "unchecked")


class NameCheckError(ValueError):
    """The fact sheet declares something invalid or self-contradictory.

    Raised eagerly by `extract_candidates`, not collected, because
    `/vdr-scope` calls it while drafting the fact sheet — a contradiction or
    a meaningless Kind should stop the skill immediately, not be gathered
    into a report the author might not see until later. See the module
    docstring for the two conditions that raise it.
    """


# The six recognised kinds. `## Invented names` typically declares one of
# the first five; "person" may also be declared there, which is how an
# author states that an overlap with `## Cast` is deliberate rather than a
# contradiction (see _check_declared_person_consistency).
KINDS = ("entity", "brand", "product", "site", "domain", "person")


@dataclass(frozen=True)
class CandidateName:
    text: str
    kind: str  # "entity" | "brand" | "product" | "site" | "domain" | "person"


@dataclass(frozen=True)
class Verdict:
    text: str
    kind: str
    verdict: str
    checked: str  # ISO date, supplied by the caller — never read from the
    # system clock inside this module. Determinism is a project-wide rule:
    # no RNG, no clock, here or anywhere else in synthvdr.
    note: str = ""


def _is_header_or_separator(first_cell: str) -> bool:
    return not first_cell or first_cell.lower() == "name" or set(first_cell) <= {"-", ":"}


def _table_rows(fact_sheet_text: str, heading_prefix: str) -> Iterator[List[str]]:
    """Yield the data-row cells of EVERY pipe table under a `##` heading
    whose lowercased text starts with `heading_prefix` — not just the first.

    An unrelated `##` heading ends the current table (`in_section = False`)
    but does not end the scan, so a later matching heading is picked back
    up. Header and separator rows are dropped by the same test
    `namecheck.load_name_check` and `names.cast_list` use: the first cell is
    empty, reads "Name", or is made up only of the dash/colon characters a
    markdown table separator uses.

    This used to `break` on the first unrelated `##` heading, which silently
    stopped the scan there. `integrity.parse_canonical_figures` had the
    identical bug over the identical file and was fixed the identical way —
    its docstring records it as "Final review, F3", where a fact sheet
    grouping figures under more than one `## Canonical figures` heading had
    "every figure after the first heading's table go unchecked, with gate 13
    reporting a confident PASS on whatever fraction it happened to see". Two
    parsers over the same file disagreeing about how much of it to read is a
    defect in whichever one reads less.

    It matters more here than it did there. A brand, product, site or domain
    name carries no corporate suffix, so `entity_tokens` cannot find it and
    the `## Invented names` table is its ONLY route into the name check —
    drop the table and nothing downstream ever checks that name, which is
    exactly the hole TECHNICAL-NOTES §6 already warns an omitted ROW opens.
    An author who splits the table in two (invented companies under one
    heading, brands and sites under another) is doing something entirely
    reasonable, and used to lose the second half without a word.
    """
    in_section = False
    for line in fact_sheet_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(heading_prefix):
            in_section = True
            continue
        if in_section and stripped.startswith("##"):
            in_section = False
            continue
        if not in_section or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or _is_header_or_separator(cells[0]):
            continue
        yield cells


def _declared_candidates(fact_sheet_text: str) -> List[CandidateName]:
    """Names declared in `## Invented names` (`| Name | Kind |`).

    Raises `NameCheckError` for a blank or unrecognised Kind — see the
    module docstring for why this raises rather than passing the value
    through: gate 14 fails safe on it (an unrecognised kind is simply not
    masked), but the collision-vs-notability split downstream is undefined
    for a kind nothing recognises.

    Also raises if the same name is declared twice with two DIFFERENT
    kinds — last-wins would silently pick one, and picking "entity" over
    "person" is exactly the masking hazard the module docstring describes.
    The same name repeated with the SAME kind is harmless and does not
    raise; only a genuine conflict does.
    """
    out = []
    seen: Dict[str, str] = {}
    for cells in _table_rows(fact_sheet_text, "## invented names"):
        if len(cells) < 2:
            continue
        text = cells[0]
        raw_kind = cells[1].strip()
        kind = raw_kind.lower()
        if kind not in KINDS:
            valid = ", ".join(KINDS)
            if not kind:
                raise NameCheckError(
                    f"{text!r} has a blank Kind in ## Invented names — must be one of: {valid}"
                )
            raise NameCheckError(
                f"{text!r} declares an unrecognised Kind {raw_kind!r} in "
                f"## Invented names — must be one of: {valid}"
            )
        if text in seen and seen[text] != kind:
            raise NameCheckError(
                f"{text!r} is declared twice in ## Invented names with different "
                f"kinds ({seen[text]!r} and {kind!r}) — the fact sheet is "
                f"self-contradictory; resolve it by removing one of the rows"
            )
        seen[text] = kind
        out.append(CandidateName(text=text, kind=kind))
    return out


def _cast_candidates(fact_sheet_text: str) -> List[CandidateName]:
    """People from `## Cast` (`| Name | Role |`), always tagged "person"."""
    return [CandidateName(text=cells[0], kind="person") for cells in _table_rows(fact_sheet_text, "## cast")]


def _check_declared_person_consistency(
    declared: List[CandidateName], cast: List[CandidateName]
) -> None:
    """Raise if a name is declared with a non-person kind but is also cast.

    `## Cast` rows are people by definition. If `## Invented names` also
    declares that same text as an entity/brand/product/site/domain, the
    fact sheet contradicts itself, and there is no correct side to pick —
    silently preferring the declared kind (the usual precedence) would mask
    that name out of the room as an entity and blind gate 14 to genuinely
    unchecked names beside it. Declaring the row as Kind "person" is how an
    author states the overlap is intentional, not a contradiction, so that
    case does not raise.
    """
    cast_texts = {c.text for c in cast}
    for c in declared:
        if c.kind != "person" and c.text in cast_texts:
            raise NameCheckError(
                f"{c.text!r} is declared as {c.kind!r} in ## Invented names but "
                f"also appears as a person in ## Cast — the fact sheet is "
                f"self-contradictory; resolve it by removing one, or by "
                f"declaring it as Kind 'person' if the overlap is intentional"
            )


def extract_candidates(fact_sheet_text: str) -> List[CandidateName]:
    """Every candidate name in the fact sheet, deduplicated by text.

    Three sources, lowest precedence first so later sources overwrite:
      1. `entity_tokens` — corporate-suffix matches in the prose (kind "entity").
      2. `## Cast` table rows (kind "person"), added only where a suffix
         match did not already claim the text — this is the "entity beats
         person" rule.
      3. `## Invented names` table rows, which always win — a declared kind
         is authorial intent and overrides whatever the other two sources
         guessed.

    Raises `NameCheckError` if `## Invented names` declares a blank or
    unrecognised Kind, or declares a non-person kind for a name that also
    appears in `## Cast` — see the module docstring for why both raise
    rather than resolving silently.
    """
    cast = _cast_candidates(fact_sheet_text)
    declared = _declared_candidates(fact_sheet_text)
    _check_declared_person_consistency(declared, cast)

    by_text: Dict[str, CandidateName] = {}
    for name in sorted(entity_tokens(fact_sheet_text)):
        by_text[name] = CandidateName(text=name, kind="entity")
    for candidate in cast:
        by_text.setdefault(candidate.text, candidate)
    for candidate in declared:
        by_text[candidate.text] = candidate
    return sorted(by_text.values(), key=lambda c: c.text)


def names_needing_check(candidates: List[CandidateName], existing: List[Verdict]) -> List[CandidateName]:
    checked = {v.text for v in existing}
    return [c for c in candidates if c.text not in checked]


def unresolved(verdicts: List[Verdict]) -> List[Verdict]:
    return [v for v in verdicts if v.verdict != "clear"]


def _read_verdict_row(line: str) -> List[str] | None:
    """Recover one name-check row's cells from a single line of file
    content, exactly as `load_name_check` reads a line — this function IS
    that reader, factored out so the round-trip guard below and
    `load_name_check` can never drift apart the way `_would_vanish_as_separator`
    and the old loader once did.

    Returns None if `line` would not be read back as a data row at all:
    not pipe-prefixed after stripping, fewer than the four required cells
    (Name, Kind, Verdict, Checked), or caught by the header/separator guard
    `_is_header_or_separator` applies to the first cell.
    """
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    if len(cells) < 4 or _is_header_or_separator(cells[0]):
        return None
    return cells


def _recovered_row_names(row: str) -> List[str]:
    """Every Name column `load_name_check` would recover from `row`, were
    `row` to stand alone as file content.

    `row` is split with `str.splitlines()`, the same call `load_name_check`
    makes over the whole file — which treats a bare '\r' as a line break in
    addition to '\n' — so an embedded line break inside `row` behaves
    exactly as it would once this row is one line among many in the real
    file: the pipe-prefixed fragment before the break is read as a
    (truncated) row, and the fragment after it is not.
    """
    names = []
    for line in row.splitlines():
        cells = _read_verdict_row(line)
        if cells is not None:
            names.append(cells[0])
    return names


def _validate_name_for_render(text: str, row: str) -> None:
    """Raise unless `row` — the exact table row `text` is about to be
    written as — reads back with a Name column equal to `text`.

    THE CHECK IS THE ROUND TRIP, NOT ANY ONE CONDITION BELOW. See the module
    docstring for why: enumerating forbidden shapes left a gap between what
    this function tested and what the loader actually does. The messages
    here exist only so an author learns why their name was rejected; they
    are consulted in order after the round trip has already failed, purely
    for diagnosis.
    """
    if "|" in text:
        raise NameCheckError(
            f"{text!r} contains a literal '|', which the pipe-table Name column "
            f"cannot represent — rename it before writing the name check"
        )
    if _recovered_row_names(row) == [text]:
        return
    if "\n" in text or "\r" in text:
        raise NameCheckError(
            f"{text!r} contains a line break, which splits it across multiple "
            f"lines when the name check is written and read back — rename it "
            f"before writing the name check"
        )
    if not text.strip() or set(text.strip()) <= {"-", ":"}:
        raise NameCheckError(
            f"{text!r} is empty or made up only of '-'/':' characters — written "
            f"into the Name column it would be misread as a table separator row "
            f"and silently dropped on load; rename it before writing the name check"
        )
    raise NameCheckError(
        f"{text!r} does not survive being written into and read back from the "
        f"name check table — rename it before writing the name check"
    )


def _sanitise_note(note: str) -> str:
    """Replace a literal '|' in free-text commentary with '/'.

    The Note column is commentary written by the search skill at build
    time, not a key (contrast `_validate_name_for_render`, which rejects
    rather than sanitises) — breaking a build over one cosmetic character
    in a comment is worse than substituting it. Left as-is, a '|' would
    split the row into extra columns and `load_name_check` would silently
    truncate the note at the first pipe, since it only reads the fifth
    cell back.
    """
    return note.replace("|", "/")


def render_name_check_md(verdicts: List[Verdict], room_codename: str) -> str:
    """The pipe table gate 14 (and `names.cast_list`) parse.

    Every row carries outer pipes, and the Name is column 1 / Kind is
    column 2 — `cast_list` reads exactly those two positions. Do not
    reorder the columns or drop the outer pipes; both are load-bearing for
    the downstream gate, not stylistic.

    Raises `NameCheckError` if any verdict's `.text` does not survive being
    written into this row and read back — see `_validate_name_for_render`,
    which performs that round trip. A '|' in `.note` is sanitised rather
    than rejected — see `_sanitise_note`.

    Also raises if `.verdict` is not one of `VERDICTS`. This is the only
    place the vocabulary is enforced, and deliberately so: see the comment
    on `VERDICTS` for why the loader must stay permissive where this
    function does not. A typo caught here is caught while the author is
    still writing the record and can simply retype it; the same typo
    reaching the file by hand-edit is gate 14's problem, and gate 14 is
    built to WARN rather than crash on it.
    """
    lines = [
        f"# {room_codename} — name check",
        "",
        "Every invented name in the fact sheet, with the date it was checked.",
        "A search returning nothing is not proof of non-existence. Dormant companies,",
        "non-UK registers and non-English markets will not surface. A company that",
        "HELD a name and later renamed keeps its filing history but not its name, so",
        "it is invisible to a web search and to an exact current-name lookup — for an",
        "entity, the register's own search must be read for FORMER names too. And a",
        "company register only covers companies: a brand or product name rests on the",
        "web search alone, and would need a trade mark register to check properly.",
        "This reduces collision risk; it does not eliminate it.",
        "",
        "| Name | Kind | Verdict | Checked | Note |",
        "|---|---|---|---|---|",
    ]
    for v in verdicts:
        if v.verdict not in VERDICTS:
            valid = ", ".join(VERDICTS)
            raise NameCheckError(
                f"{v.text!r} records verdict {v.verdict!r}, which is not one of "
                f"{valid} — gate 14 reads this column and treats anything not "
                f"spelled 'clear' as unresolved, so a misspelled 'collision' "
                f"quietly becomes a WARN the build can pass with; correct it "
                f"before writing the name check"
            )
        note = _sanitise_note(v.note)
        row = f"| {v.text} | {v.kind} | {v.verdict} | {v.checked} | {note} |"
        _validate_name_for_render(v.text, row)
        lines.append(row)
    lines.append("")
    return "\n".join(lines)


def load_name_check(path: Path) -> List[Verdict]:
    """Read the pipe table `render_name_check_md` writes.

    Delegates each line's cell recovery to `_read_verdict_row` — the same
    function the round-trip guard in `_validate_name_for_render` calls, so
    a name that renders without raising is guaranteed to load back exactly.
    """
    out: List[Verdict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        cells = _read_verdict_row(line)
        if cells is None:
            continue
        out.append(
            Verdict(
                text=cells[0],
                kind=cells[1],
                verdict=cells[2],
                checked=cells[3],
                note=cells[4] if len(cells) > 4 else "",
            )
        )
    return out
