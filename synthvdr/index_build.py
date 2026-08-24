"""Generate index.md from _key/index-src/.

index.md is tool-facing and sits outside the blind tree. It is generated, never
hand-edited: gate 1 regenerates it and fails on any difference.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

from .domain import DomainPack
from .slots import Slot

INDEX_ENTRY = re.compile(r"^- (\d+\.\d+\.\d+) ", re.MULTILINE)

PREAMBLE = (
    "This is the contents list for the documents made available in connection with "
    "the proposed transaction. Documents are numbered by section, sub-section and "
    "item. Numbering is stable: an item keeps its number for the life of the "
    "process.\n"
)


def write_index_sources(slots: List[Slot], pack: DomainPack, index_src: Path) -> None:
    index_src.mkdir(parents=True, exist_ok=True)
    (index_src / "00_preamble.txt").write_text(PREAMBLE, encoding="utf-8")
    for section in pack.sections:
        rows = [s for s in slots if s.section_dir == section.dir_name]
        lines = [f"## {section.number}. {section.title}", ""]
        current_sub = None
        for slot in rows:
            if slot.subsection != current_sub:
                current_sub = slot.subsection
                pretty = current_sub.split("_", 1)[1].replace("-", " ").capitalize()
                lines.append(f"### {current_sub.split('_')[0]} {pretty}")
                lines.append("")
            title = slot.slug.split("_", 1)[1].replace("-", " ").capitalize()
            lines.append(f"- {slot.slot_id} {title}")
        lines.append("")
        (index_src / f"{section.dir_name}.md").write_text("\n".join(lines), encoding="utf-8")


def render_index(index_src: Path) -> str:
    preamble = (index_src / "00_preamble.txt").read_text(encoding="utf-8").rstrip("\n")
    parts = [preamble, ""]
    for path in sorted(p for p in index_src.glob("*.md")):
        parts.append(path.read_text(encoding="utf-8").rstrip("\n"))
        parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"


def count_slots(index_text: str) -> int:
    return len(INDEX_ENTRY.findall(index_text))
