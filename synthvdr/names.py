"""Entity and cast-name helpers.

Deliberately NOT in qa/leakage.py: Task 14's namecheck module needs these, and a
top-level module must not import from the gate package — synthvdr/qa/__init__.py
imports every gate, so that dependency would drag the whole suite in.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set

ENTITY_SUFFIXES = (
    "Limited", "Ltd", "PLC", "plc", "LLP", "LLC", "Inc", "Incorporated",
    "GmbH", "AG", "SAS", "SARL", "SA", "BV", "NV", "AB", "Oy", "SpA", "KK",
)

_ENTITY = re.compile(
    r"\b((?:[A-Z][\w&'’-]*\s+){1,4}(?:" + "|".join(re.escape(s) for s in ENTITY_SUFFIXES) + r"))\b"
)


def entity_tokens(text: str) -> Set[str]:
    """Capitalised phrases ending in a corporate suffix."""
    return {match.group(1).strip() for match in _ENTITY.finditer(text)}


def cast_list(path: Path) -> Set[str]:
    """Names from the first column of a pipe table (the name-check record)."""
    names: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and cells[0] and cells[0].lower() != "name":
            names.add(cells[0])
    return names
