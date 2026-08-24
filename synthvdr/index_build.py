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

# Acronyms that should be uppercased in presentation (singular forms only)
ACRONYMS = {"vat", "nda", "cpse", "hse", "qms", "ncr", "capa", "dc", "ropa", "dpia", "jv", "esg", "it", "spa", "epc"}


def _titleise(text: str) -> str:
    """Render text in sentence case with acronyms uppercased.

    - Capitalises the first letter only (sentence case, not title case)
    - Acronyms are uppercased in place: 'vat' -> 'VAT', 'nda' -> 'NDA'
    - Plural acronyms keep the acronym uppercased and add lowercase 's': 'ndas' -> 'NDAs'
    - Special case: 'w and i' -> 'W&I'

    Examples:
        'statutory accounts' -> 'Statutory accounts'
        'cpse replies' -> 'CPSE replies'
        'nda' -> 'NDA'
        'ndas' -> 'NDAs'
        'w and i' -> 'W&I'
    """
    # Special case for W&I
    if text.lower() == "w and i":
        return "W&I"

    words = text.split()
    result = []
    for i, word in enumerate(words):
        word_lower = word.lower()

        # Check if word is an acronym or acronym + 's'
        if word_lower in ACRONYMS:
            result.append(word_lower.upper())
        elif word_lower.endswith("s") and word_lower[:-1] in ACRONYMS:
            # Handle plural acronyms: 'ndas' -> 'NDAs'
            result.append(word_lower[:-1].upper() + "s")
        elif i == 0:
            # First word: capitalize only the first letter (sentence case)
            result.append(word_lower.capitalize())
        else:
            # Other non-acronym words: keep as-is (lowercase)
            result.append(word_lower)

    return " ".join(result)


def write_index_sources(slots: List[Slot], pack: DomainPack, index_src: Path) -> None:
    index_src.mkdir(parents=True, exist_ok=True)
    # Clean up stale section files to ensure exact regeneration
    for stale in index_src.glob("*.md"):
        stale.unlink()
    (index_src / "00_preamble.txt").write_text(PREAMBLE, encoding="utf-8")
    for section in pack.sections:
        rows = [s for s in slots if s.section_dir == section.dir_name]
        lines = [f"## {section.number}. {section.title}", ""]
        current_sub = None
        for slot in rows:
            if slot.subsection != current_sub:
                current_sub = slot.subsection
                pretty = current_sub.split("_", 1)[1].replace("-", " ")
                pretty = _titleise(pretty)
                lines.append(f"### {current_sub.split('_')[0]} {pretty}")
                lines.append("")
            title = slot.slug.split("_", 1)[1].replace("-", " ")
            title = _titleise(title)
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
