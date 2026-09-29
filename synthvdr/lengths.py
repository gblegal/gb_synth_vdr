"""Long-mode document lengths: the bands, the agreement-bearing subfolders,
and the anatomy an author outlines against.

Read only when a room's room.conf says DOC_LENGTH="long" — a short room never
opens either file, which is what keeps short mode byte-for-byte what it was.
Spec: docs/superpowers/specs/2026-09-29-long-form-documents-design.md §5–§6.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

import yaml

from .domain import DEFAULT_DOMAIN_ROOT, DomainError, DomainPack

PAPERS = ("target", "counterparty")

# The batching weight of every slot that is not a long-form agreement: the
# median document in Project Frithcombe (ll_vdr_08), measured 29 September
# 2026. It sizes /vdr-build's long-mode batches and nothing else — it is not a
# floor.
SHORT_DOC_WEIGHT = 1200


@dataclass(frozen=True)
class Band:
    name: str
    pages: Tuple[int, int]
    floor: int
    target: int


@dataclass(frozen=True)
class Part:
    name: str
    share: int


@dataclass(frozen=True)
class AnatomyFamily:
    name: str
    title: str
    parts: Tuple[Part, ...]
    clauses: Tuple[str, ...]


@dataclass(frozen=True)
class LengthRow:
    section: str
    subsection: str
    band: str
    paper: str
    anatomy: str
    hint: str


@dataclass(frozen=True)
class Lengths:
    words_per_page: int
    bands: Dict[str, Band]
    rows: Tuple[LengthRow, ...]
    families: Dict[str, AnatomyFamily]

    def __post_init__(self) -> None:
        """Internal invariants, on the type — DomainPack.__post_init__'s
        reasoning: a Lengths built in memory is held to them too, not only one
        read from disk. The invariants that need the domain pack are in
        `check_against_pack`."""
        for band in self.bands.values():
            low, high = band.pages
            if not 0 < low < high:
                raise DomainError(f"band {band.name!r}: pages {band.pages} is not an increasing range")
            if band.floor != low * self.words_per_page:
                raise DomainError(
                    f"band {band.name!r}: floor {band.floor} is not {low} pages x "
                    f"{self.words_per_page} words — a floor must be the bottom of the page "
                    "range it claims, or the two drift apart"
                )
            if not band.floor < band.target <= high * self.words_per_page:
                raise DomainError(
                    f"band {band.name!r}: target {band.target} must sit above the floor "
                    f"({band.floor}) and no higher than {high} pages x {self.words_per_page} words"
                )
        for family in self.families.values():
            if not family.parts or not family.clauses:
                raise DomainError(f"anatomy {family.name!r} needs at least one part and one clause")
            total = sum(part.share for part in family.parts)
            if total != 100 or any(part.share <= 0 for part in family.parts):
                raise DomainError(
                    f"anatomy {family.name!r}: part shares sum to {total}, not 100 "
                    "(and every share must be positive)"
                )
        seen = set()
        for row in self.rows:
            where = f"{row.section}/{row.subsection}"
            if (row.section, row.subsection) in seen:
                raise DomainError(f"{where} is declared twice")
            seen.add((row.section, row.subsection))
            if row.band not in self.bands:
                raise DomainError(f"{where}: unknown band {row.band!r}")
            if row.paper not in PAPERS:
                raise DomainError(f"{where}: paper {row.paper!r} is not one of {PAPERS}")
            if row.anatomy not in self.families:
                raise DomainError(f"{where}: unknown anatomy {row.anatomy!r}")

    def row_for(self, section_dir: str, subsection_name: str) -> Optional[LengthRow]:
        for row in self.rows:
            if row.section == section_dir and row.subsection == subsection_name:
                return row
        return None

    def row_for_rel_path(self, rel_path: str) -> Optional[LengthRow]:
        """The row for a slot path `<section>/<N.k_subsection>/<file>`, or None.

        Only the last three components are read, so an absolute blind-tree
        path answers the same as a room-relative one.
        """
        parts = rel_path.replace("\\", "/").split("/")
        if len(parts) < 3 or "_" not in parts[-2]:
            return None
        return self.row_for(parts[-3], parts[-2].split("_", 1)[1])

    def weight_for(self, rel_path: str) -> int:
        row = self.row_for_rel_path(rel_path)
        return self.bands[row.band].target if row is not None else SHORT_DOC_WEIGHT


def check_against_pack(lengths: Lengths, pack: DomainPack) -> None:
    """The invariants that need the domain pack.

    Every row must name a real subfolder, and long mode must never make a
    slot LESS demanding than short mode would. Pass the FULL pack: a room
    that builds a subset of sections still loads every row, and rows for the
    sections it dropped simply match no slot.
    """
    from .qa.depth import classify_archetype  # lazy: synthvdr.qa imports this module

    by_dir = {section.dir_name: section for section in pack.sections}
    for row in lengths.rows:
        section = by_dir.get(row.section)
        if section is None or row.subsection not in section.subsections:
            raise DomainError(f"{row.section}/{row.subsection}: no such subsection in the domain pack")
        archetype = classify_archetype(f"0.0.0_{row.subsection}-01.md", pack)
        short_floor = max(pack.archetypes[archetype].floor, pack.tier_f_floor)
        long_floor = lengths.bands[row.band].floor
        if long_floor < short_floor:
            raise DomainError(
                f"{row.section}/{row.subsection}: long floor {long_floor} is below the "
                f"{short_floor} this slot gets in short mode — long mode may never make a "
                "slot less demanding"
            )


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise DomainError(f"domain pack file missing: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_lengths(root: Path = DEFAULT_DOMAIN_ROOT, pack: Optional[DomainPack] = None) -> Lengths:
    """`lengths.yaml` and `anatomy.yaml` from `root`, validated.

    With `pack`, also checked against it (`check_against_pack`). Every refusal
    is re-raised naming `root`, so a bad pack on disk says which pack.
    """
    try:
        doc = _read_yaml(root / "lengths.yaml")
        anatomy = _read_yaml(root / "anatomy.yaml")
        lengths = Lengths(
            words_per_page=doc["words_per_page"],
            bands={
                name: Band(name, tuple(body["pages"]), body["floor"], body["target"])
                for name, body in doc["bands"].items()
            },
            rows=tuple(LengthRow(**row) for row in doc["subsections"]),
            families={
                name: AnatomyFamily(
                    name,
                    body["title"],
                    tuple(Part(**part) for part in body["parts"]),
                    tuple(body["clauses"]),
                )
                for name, body in anatomy["families"].items()
            },
        )
        if pack is not None:
            check_against_pack(lengths, pack)
    except DomainError as exc:
        raise DomainError(f"{root}: {exc}") from exc
    return lengths


def brief_for(rel_path: str, lengths: Lengths, parents: Mapping[str, str]) -> Optional[str]:
    """The long-form brief /vdr-build hands a slot's author, or None when the
    slot is not an agreement. `parents` is `houseforms.house_form_of(...)`.

    Part budgets are the anatomy share of the band TARGET, to the nearest
    hundred — the floor is what gate 10 enforces, the target is what the
    author aims at.
    """
    row = lengths.row_for_rel_path(rel_path)
    if row is None:
        return None
    band = lengths.bands[row.band]
    family = lengths.families[row.anatomy]
    lines = [
        f"Long-form agreement: {row.hint}. Band {band.name}, {band.pages[0]}-{band.pages[1]} pages: "
        f"write to about {band.target:,} words; the floor is {band.floor:,}.",
        f"Outline ({family.title}) — one level-2 heading (## ) per part, in this order:",
    ]
    lines += [f"  - {part.name}: ~{round(part.share * band.target / 100, -2):,.0f} words" for part in family.parts]
    lines.append("Clauses it must contain, almost all in ordinary benign form: " + "; ".join(family.clauses) + ".")
    if rel_path in parents:
        lines.append(
            f"Derived contract: {parents[rel_path]} has been copied to this path. Edit it — fill "
            "every bracketed blank, rewrite the schedules, and make at least two negotiated changes "
            "to clauses that carry no blank in the house form (two to five, all benign: an edit "
            "inside a clause with a blank counts as filling it, not as a change); plant a finding "
            "or distractor only where your registry rows say so."
        )
    elif rel_path in set(parents.values()):
        lines.append(
            "House form: write the target's unsigned standard terms. Blanks are bracketed labels "
            "starting with a capital letter — [Customer name], [Commencement Date] — and one of "
            "them is [Template — not for signature]. Benign by rule: no finding and no distractor "
            "goes in a house form."
        )
    return "\n".join(lines)
