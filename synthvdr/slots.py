"""Slot manifests: the deterministic list of document slots in a room."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set

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
    "XS": SizePreset("XS", docs=40, findings=4, distractors=2),
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


def slot_slug(section: Section, sub_index: int, ordinal: int) -> str:
    """The exact filename stem (no `.md`) `build_slot_manifest` gives the `ordinal`-th
    (1-based) slot at `section`'s `sub_index`-th (0-based) subsection.

    Factored out of the loop below so anything that needs to know "what filename would
    this tool actually emit for this position" — a test validating a skill's example
    evidence paths against the real naming scheme, say — calls this rather than keeping a
    second copy of the `f"{slot_id}_{name}-{ordinal:02d}"` format that could silently drift
    from it. Same discipline as `_subsection_name` above, which this shares its two inputs
    with.
    """
    slot_id = f"{section.number}.{sub_index + 1}.{ordinal}"
    return f"{slot_id}_{section.subsections[sub_index]}-{ordinal:02d}"


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
            slug = slot_slug(section, sub_index, ordinal)
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
    # The slots were appended in round-robin order across subsections, which
    # is what spreads the anchor tier evenly instead of concentrating it in
    # the first subsection of each section. That ordering is right for TIER
    # ASSIGNMENT and wrong for the manifest, where a reader expects a
    # section's subsections to run contiguously — so the tiers are assigned
    # first and the list is re-sorted here, rather than building it in
    # manifest order and losing the spread.
    #
    # Sorting on the parsed triple, not on slot_id: slot_id is a dotted
    # STRING, so "10" sorts before "2" and section 10 would land between 1
    # and 2. Every component is compared as an integer for that reason.
    def slot_sort_key(slot: Slot) -> tuple:
        parts = slot.slot_id.split(".")
        section_number = int(parts[0])
        sub_index = int(parts[1]) - 1
        ordinal = int(parts[2])
        return (section_number, sub_index, ordinal)

    return sorted(slots, key=slot_sort_key)


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


def read_slot_manifest(path: Path) -> List[Slot]:
    """`anchors.csv` back into the `Slot` objects `write_anchors_csv` wrote.

    `read_anchors_csv` above returns only `{slot_id: tier}`, which is all gate
    10's depth lint needs. `authoring_order` needs `rel_path` as well, because
    the answer key names documents by path and never by slot id, so the two
    only meet there.
    """
    slots: List[Slot] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            section_dir, subsection, filename = row["rel_path"].split("/")
            slots.append(
                Slot(
                    slot_id=row["slot_id"],
                    section_dir=section_dir,
                    subsection=subsection,
                    slug=filename[: -len(".md")],
                    tier=row["tier"],
                )
            )
    return slots


def authoring_order(slots: Iterable[Slot], load_bearing: Set[str]) -> List[Slot]:
    """The order `/vdr-build` authors a room in: load-bearing slots first, tier
    order after, manifest order within each group.

    `load_bearing` is a set of rel_paths — `synthvdr.schema.load_bearing_paths`
    over the room's findings and distractors. A path in it that matches no slot
    is ignored here; that is a findings.yaml defect, and gate 12 and
    `build_flagged_tree` are the two things that already name it.

    WHY THIS IS NOT "SORT BY TIER". `/vdr-build` used to say exactly that, and
    glossed tier `A` as "anchor — carries a finding, a distractor, or is
    otherwise load-bearing". Tier means nothing of the sort. `build_slot_manifest`
    assigns it POSITIONALLY, at /vdr-scope time, before the findings registry
    exists to consult: the first ~35% of each section's slots are `A` and the
    rest are `F`, and nothing ever revisits that. In the XS build that surfaced
    this, 6 of the 10 registry evidence paths were tier `F`, including both
    distractor documents. Sorting by tier therefore deferred half the findings'
    evidence behind every filler-tier document — the precise opposite of the
    invariant the rule was written to protect, which is that an interrupted
    build never strands a finding half-planted.

    Tier is kept as the SECOND key rather than dropped. It is a real signal,
    just not the one the old rule claimed: it is a structural depth and
    prominence tier (gate 10's floors are keyed off it), so among documents the
    answer key does not depend on, the more substantial ones are still written
    first.

    Sorting is stable, so slots keep manifest order inside each group and a
    resumed build picks up where the manifest says. Re-tiering `anchors.csv`
    after /vdr-findings was the alternative considered; it was rejected because
    tier drives gate 10's depth floors, so promoting a slot silently changes
    what an existing room is required to meet.
    """
    return sorted(
        slots,
        key=lambda slot: (
            slot.rel_path not in load_bearing,
            slot.tier != TIER_ANCHOR,
        ),
    )
