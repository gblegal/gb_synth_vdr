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
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List

from .names import entity_tokens

VERDICTS = ("clear", "collision", "ambiguous")

# The five kinds a fact sheet may declare in `## Invented names`, plus
# "person", which is never declared — it comes only from `## Cast` rows.
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
    """Yield the data-row cells of the first pipe table under a `##`
    heading whose lowercased text starts with `heading_prefix`.

    Stops at the next `##` heading. Header and separator rows are dropped
    by the same test `namecheck.load_name_check` and `names.cast_list` use:
    the first cell is empty, reads "Name", or is made up only of the
    dash/colon characters a markdown table separator uses.
    """
    in_section = False
    for line in fact_sheet_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(heading_prefix):
            in_section = True
            continue
        if in_section and stripped.startswith("##"):
            break
        if not in_section or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or _is_header_or_separator(cells[0]):
            continue
        yield cells


def _declared_candidates(fact_sheet_text: str) -> List[CandidateName]:
    """Names declared in `## Invented names` (`| Name | Kind |`)."""
    out = []
    for cells in _table_rows(fact_sheet_text, "## invented names"):
        if len(cells) < 2:
            continue
        out.append(CandidateName(text=cells[0], kind=cells[1].strip().lower()))
    return out


def _cast_candidates(fact_sheet_text: str) -> List[CandidateName]:
    """People from `## Cast` (`| Name | Role |`), always tagged "person"."""
    return [CandidateName(text=cells[0], kind="person") for cells in _table_rows(fact_sheet_text, "## cast")]


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
    """
    by_text: Dict[str, CandidateName] = {}
    for name in sorted(entity_tokens(fact_sheet_text)):
        by_text[name] = CandidateName(text=name, kind="entity")
    for candidate in _cast_candidates(fact_sheet_text):
        by_text.setdefault(candidate.text, candidate)
    for candidate in _declared_candidates(fact_sheet_text):
        by_text[candidate.text] = candidate
    return sorted(by_text.values(), key=lambda c: c.text)


def names_needing_check(candidates: List[CandidateName], existing: List[Verdict]) -> List[CandidateName]:
    checked = {v.text for v in existing}
    return [c for c in candidates if c.text not in checked]


def unresolved(verdicts: List[Verdict]) -> List[Verdict]:
    return [v for v in verdicts if v.verdict != "clear"]


def render_name_check_md(verdicts: List[Verdict], room_codename: str) -> str:
    """The pipe table gate 14 (and `names.cast_list`) parse.

    Every row carries outer pipes, and the Name is column 1 / Kind is
    column 2 — `cast_list` reads exactly those two positions. Do not
    reorder the columns or drop the outer pipes; both are load-bearing for
    the downstream gate, not stylistic.
    """
    lines = [
        f"# {room_codename} — name check",
        "",
        "Every invented name in the fact sheet, with the date it was checked.",
        "A search returning nothing is not proof of non-existence: dormant companies",
        "and non-English markets will not surface. This reduces collision risk; it",
        "does not eliminate it.",
        "",
        "| Name | Kind | Verdict | Checked | Note |",
        "|---|---|---|---|---|",
    ]
    for v in verdicts:
        lines.append(f"| {v.text} | {v.kind} | {v.verdict} | {v.checked} | {v.note} |")
    lines.append("")
    return "\n".join(lines)


def load_name_check(path: Path) -> List[Verdict]:
    out: List[Verdict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4 or _is_header_or_separator(cells[0]):
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
