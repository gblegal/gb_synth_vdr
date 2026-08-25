"""Domain packs: the section taxonomy, document archetypes and finding seeds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml

DEFAULT_DOMAIN_ROOT = Path(__file__).resolve().parent.parent / "domain" / "ma"


class DomainError(Exception):
    """A domain pack is missing or internally inconsistent."""


@dataclass(frozen=True)
class Section:
    number: int
    dir_name: str
    title: str
    workstream: str
    weight: float
    subsections: List[str]


@dataclass(frozen=True)
class Archetype:
    name: str
    floor: int
    filename_patterns: List[str]


@dataclass(frozen=True)
class DomainPack:
    sections: List[Section]
    archetypes: Dict[str, Archetype]
    default_archetype: str
    tier_f_floor: int
    finding_archetypes: Dict[str, List[str]]

    def section_dirs(self) -> List[str]:
        return [s.dir_name for s in self.sections]

    def section_by_dir(self, dir_name: str) -> Section:
        for section in self.sections:
            if section.dir_name == dir_name:
                return section
        raise DomainError(f"unknown section directory: {dir_name}")


def _read(path: Path) -> dict:
    if not path.is_file():
        raise DomainError(f"domain pack file missing: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_domain(root: Path) -> DomainPack:
    sections = [Section(**row) for row in _read(root / "sections.yaml")["sections"]]
    sections.sort(key=lambda s: s.number)

    arch_doc = _read(root / "archetypes.yaml")
    archetypes = {
        name: Archetype(name=name, floor=body["floor"], filename_patterns=body["filename_patterns"])
        for name, body in arch_doc["archetypes"].items()
    }
    finding_archetypes = _read(root / "finding-archetypes.yaml")["finding_archetypes"]

    total_weight = sum(s.weight for s in sections)
    if abs(total_weight - 1.0) > 1e-6:
        raise DomainError(f"{root}: section weights sum to {total_weight}, expected 1.0")

    # A tier-A anchor carries planted evidence; tier-F filler carries none.
    # If any archetype's floor sits below the flat tier-F floor, a
    # correctly tagged anchor could be held to a LOWER depth standard than
    # generic filler — backwards for the property this domain pack exists
    # to encode. Checked here, once, at load time, so a future edit to
    # either number can never silently regress it.
    tier_f_floor = arch_doc["tier_f_floor"]
    under_floor = sorted(name for name, a in archetypes.items() if a.floor < tier_f_floor)
    if under_floor:
        raise DomainError(
            f"{root}: archetype floor(s) {under_floor} are below tier_f_floor "
            f"({tier_f_floor}) — a tier-A anchor must never be held to a lower "
            "depth standard than tier-F filler"
        )

    return DomainPack(
        sections=sections,
        archetypes=archetypes,
        default_archetype=arch_doc["default_archetype"],
        tier_f_floor=arch_doc["tier_f_floor"],
        finding_archetypes=finding_archetypes,
    )
