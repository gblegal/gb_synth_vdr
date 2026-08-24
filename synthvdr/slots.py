"""Slot manifests: the deterministic list of document slots in a room."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .domain import DomainPack, Section

TIER_ANCHOR = "A"
TIER_FILLER = "F"


@dataclass(frozen=True)
class SizePreset:
    name: str
    docs: int
    findings: int
    distractors: int


SIZE_PRESETS: Dict[str, SizePreset] = {
    "XS": SizePreset("XS", docs=20, findings=4, distractors=2),
    "S": SizePreset("S", docs=60, findings=12, distractors=5),
    "M": SizePreset("M", docs=200, findings=25, distractors=10),
    "L": SizePreset("L", docs=800, findings=60, distractors=18),
    "XL": SizePreset("XL", docs=2000, findings=90, distractors=25),
}


@dataclass(frozen=True)
class Slot:
    slot_id: str
    section_dir: str
    subsection: str
    slug: str
    tier: str

    @property
    def rel_path(self) -> str:
        return f"{self.section_dir}/{self.subsection}/{self.slug}.md"


def _allocate(pack: DomainPack, total: int) -> Dict[str, int]:
    """Largest-remainder allocation, floored at one slot per section."""
    sections = pack.sections
    if total < len(sections):
        raise ValueError(f"total {total} is below the section count {len(sections)}")
    base = {s.dir_name: 1 for s in sections}
    remaining = total - len(sections)
    exact = {s.dir_name: s.weight * remaining for s in sections}
    floors = {k: int(v) for k, v in exact.items()}
    shortfall = remaining - sum(floors.values())
    order = sorted(sections, key=lambda s: (-(exact[s.dir_name] - floors[s.dir_name]), s.number))
    for section in order[:shortfall]:
        floors[section.dir_name] += 1
    return {k: base[k] + floors[k] for k in base}


def _subsection_name(section: Section, position: int) -> str:
    index = position % len(section.subsections)
    return f"{section.number}.{index + 1}_{section.subsections[index]}"


def build_slot_manifest(pack: DomainPack, preset: SizePreset) -> List[Slot]:
    allocation = _allocate(pack, preset.docs)
    slots: List[Slot] = []
    for section in pack.sections:
        count = allocation[section.dir_name]
        per_sub: Dict[int, int] = {}
        for position in range(count):
            sub_index = position % len(section.subsections)
            per_sub[sub_index] = per_sub.get(sub_index, 0) + 1
            ordinal = per_sub[sub_index]
            subsection = _subsection_name(section, position)
            slot_id = f"{section.number}.{sub_index + 1}.{ordinal}"
            slug = f"{slot_id}_{section.subsections[sub_index]}-{ordinal:02d}"
            tier = TIER_ANCHOR if position < max(1, round(count * 0.35)) else TIER_FILLER
            slots.append(
                Slot(
                    slot_id=slot_id,
                    section_dir=section.dir_name,
                    subsection=subsection,
                    slug=slug,
                    tier=tier,
                )
            )
    return slots


def write_anchors_csv(slots: List[Slot], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["slot_id", "tier", "rel_path"])
        for slot in slots:
            writer.writerow([slot.slot_id, slot.tier, slot.rel_path])


def read_anchors_csv(path: Path) -> Dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["slot_id"]: row["tier"] for row in csv.DictReader(handle)}
