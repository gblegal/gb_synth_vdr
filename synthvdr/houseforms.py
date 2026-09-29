"""House forms: the target's own standard terms, and the executed contracts
derived from them by copy-and-edit.

Long mode only. In a subfolder where the target holds the pen (`paper: target`
in domain/ma/lengths.yaml) and the room gives it at least MIN_FAMILY_SLOTS
slots, the ordinal-1 slot is an unsigned standard-terms document and every
later slot is an executed contract on those terms — the way a real room's
customer contracts are mostly the target's own paper. Spec §7.

A house form is benign by rule (gate 21 enforces it): a clause planted in one
would propagate into every derived contract with evidence declared on none.

Nothing here imports synthvdr.qa, which imports this module for gate 21.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .lengths import Lengths
from .slots import Slot

MIN_FAMILY_SLOTS = 3
MIN_DEVIATIONS = 2

# A blank: a bracketed label starting with a capital letter — "[Customer name]",
# "[Template — not for signature]". The capital is the discriminator from the
# room's ordinary bracketed asides ("[signature page not scanned]").
BLANK = re.compile(r"\[[A-Z][^\[\]\n]{0,80}\]")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t\r]*\n")


@dataclass(frozen=True)
class HouseFormFamily:
    house_form: Slot
    derived: Tuple[Slot, ...]


def _slot_key(slot: Slot) -> Tuple[int, ...]:
    return tuple(int(part) for part in slot.slot_id.split("."))


def families(slots: Iterable[Slot], lengths: Lengths) -> List[HouseFormFamily]:
    """Every house-form family in a room, sorted by slot id.

    A pure function of the slot manifest and lengths.yaml, so /vdr-findings,
    /vdr-build and gate 21 all see the same families without a file recording
    them. Independent of the order `slots` arrives in.
    """
    groups: Dict[Tuple[str, str], List[Slot]] = {}
    for slot in slots:
        row = lengths.row_for_rel_path(slot.rel_path)
        if row is not None and row.paper == "target":
            groups.setdefault((slot.section_dir, slot.subsection), []).append(slot)
    found = []
    for members in groups.values():
        if len(members) < MIN_FAMILY_SLOTS:
            continue
        members = sorted(members, key=_slot_key)
        found.append(HouseFormFamily(members[0], tuple(members[1:])))
    return sorted(found, key=lambda family: _slot_key(family.house_form))


def house_form_paths(fams: Iterable[HouseFormFamily]) -> Set[str]:
    return {family.house_form.rel_path for family in fams}


def house_form_of(fams: Iterable[HouseFormFamily]) -> Dict[str, str]:
    return {slot.rel_path: family.house_form.rel_path for family in fams for slot in family.derived}


def blanks(text: str) -> Set[str]:
    return set(BLANK.findall(text))


def _paragraphs(text: str) -> List[str]:
    return [" ".join(block.split()) for block in _PARAGRAPH_BREAK.split(text) if block.strip()]


def deviation_count(house_text: str, derived_text: str) -> int:
    """How many paragraphs a derived contract changed beyond its blanks.

    Standard paragraphs (no blank in the house form) edited or deleted, plus
    new paragraphs beyond the one-for-one replacement of the house form's
    blank-carrying paragraphs.
    """
    house = _paragraphs(house_text)
    derived = _paragraphs(derived_text)
    house_set, derived_set = set(house), set(derived)
    blank_paragraphs = sum(1 for p in house if BLANK.search(p))
    changed = sum(1 for p in house if not BLANK.search(p) and p not in derived_set)
    novel = sum(1 for p in derived if p not in house_set)
    return changed + max(0, novel - blank_paragraphs)


def derivation_problems(house_text: str, derived_text: str) -> List[str]:
    problems = []
    surviving = sorted(blank for blank in blanks(house_text) if blank in derived_text)
    if surviving:
        problems.append(
            f"still carries {len(surviving)} of its house form's blanks, e.g. {surviving[0]}"
        )
    count = deviation_count(house_text, derived_text)
    if count < MIN_DEVIATIONS:
        problems.append(
            f"differs from its house form in {count} paragraph(s) beyond the blanks; "
            f"at least {MIN_DEVIATIONS} negotiated changes are required"
        )
    return problems


def seed(blind_root: Path, fams: Iterable[HouseFormFamily], only: Optional[Set[str]] = None) -> List[str]:
    """Copy each written house form to its derived slots' paths.

    Creates files only — never overwrites — so it is safe to re-run on a
    resumed build. `only` limits it to one wave's slots. Returns the rel_paths
    it created. The only Python writer into the blind tree.
    """
    created = []
    for family in fams:
        source = blind_root / family.house_form.rel_path
        if not source.is_file():
            continue
        body = source.read_bytes()
        for slot in family.derived:
            if only is not None and slot.rel_path not in only:
                continue
            target = blind_root / slot.rel_path
            # lexists, not exists: a dangling symlink "does not exist", and
            # write_bytes would follow it out of the blind tree.
            if os.path.lexists(target):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            created.append(slot.rel_path)
    return created


def authored_paths(blind_root: Path, slots: Iterable[Slot], fams: Iterable[HouseFormFamily]) -> Set[str]:
    """Slots whose document exists and counts as authored.

    A derived contract counts only once it clears `derivation_problems`
    against its house form, so a seeded copy an interrupted wave never edited
    is re-dispatched by the resumed build rather than skipped.
    """
    parent = house_form_of(fams)
    done = set()
    for slot in slots:
        path = blind_root / slot.rel_path
        if not path.is_file():
            continue
        house = parent.get(slot.rel_path)
        if house is not None:
            house_path = blind_root / house
            if not house_path.is_file() or derivation_problems(
                house_path.read_text(encoding="utf-8"), path.read_text(encoding="utf-8")
            ):
                continue
        done.add(slot.rel_path)
    return done


def ready_slots(order: Sequence[Slot], done: Set[str], fams: Iterable[HouseFormFamily]) -> List[Slot]:
    """`order` without what is done, and without any derived slot whose house
    form is not yet authored — so a derived slot never shares a wave with its
    own house form, and its seed always exists before its author starts."""
    parent = house_form_of(fams)
    return [
        slot
        for slot in order
        if slot.rel_path not in done and (slot.rel_path not in parent or parent[slot.rel_path] in done)
    ]
