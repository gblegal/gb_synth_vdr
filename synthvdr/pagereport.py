"""What a long room's agreements rendered to, and what its scan trees will cost.

`python3 -m synthvdr pages --room .` — after the PDF render: rendered page
counts per length band against each band's range. Information, not a gate: it
is the check on lengths.yaml's words_per_page (spec §3, §9).

`python3 -m synthvdr pages --room . --estimate` — before the render: the
listed scans' estimated page count and the size of both scan trees.
`_key/scanned.csv` lists whole documents and draws only on evidence, which in
a long room is often a 20-45-page agreement.
"""

from __future__ import annotations

import csv
import math
import re
import statistics
from pathlib import Path
from typing import Dict, List

from .lengths import Lengths
from .qa.depth import wordcount

# /Type /Page but not /Type /Pages (the page-tree node).
_PAGE_OBJECT = re.compile(rb"/Type\s*/Page(?![A-Za-z])")

# Known issue #1's measurement, per page: a four-page scanned document is
# 564KB pristine and 9.05MB under the office profile.
PRISTINE_KB_PER_SCANNED_PAGE = 141
OFFICE_MB_PER_SCANNED_PAGE = 2.26


def pdf_page_count(path: Path) -> int:
    return len(_PAGE_OBJECT.findall(path.read_bytes()))


def page_report(blind_root: Path, pdf_root: Path, lengths: Lengths) -> List[str]:
    counts: Dict[str, List[int]] = {name: [] for name in lengths.bands}
    missing = 0
    for source in sorted(blind_root.rglob("*.md")):
        rel = source.relative_to(blind_root).as_posix()
        row = lengths.row_for_rel_path(rel)
        if row is None:
            continue
        pdf = pdf_root / Path(rel).with_suffix(".pdf")
        if not pdf.is_file():
            missing += 1
            continue
        counts[row.band].append(pdf_page_count(pdf))
    lines = []
    for name, band in lengths.bands.items():
        pages = counts[name]
        if not pages:
            lines.append(f"{name:9} no rendered agreements")
            continue
        below = sum(1 for p in pages if p < band.pages[0])
        lines.append(
            f"{name:9} n={len(pages):3}  pages min {min(pages)} / median "
            f"{statistics.median(pages):g} / max {max(pages)}  "
            f"(band {band.pages[0]}-{band.pages[1]}; {below} below)"
        )
    if missing:
        lines.append(f"{missing} agreement(s) have no PDF under {pdf_root} — render first")
    return lines


def scan_estimate(blind_root: Path, scanned_csv: Path, words_per_page: int = 500) -> str:
    with scanned_csv.open(newline="", encoding="utf-8") as handle:
        slots = [row["slot"].strip() for row in csv.DictReader(handle)]
    pages = 0
    for slot in slots:
        source = blind_root / Path(slot).with_suffix(".md")
        if source.is_file():
            pages += max(1, math.ceil(wordcount(source.read_text(encoding="utf-8")) / words_per_page))
    return (
        f"{len(slots)} scanned document(s), ~{pages} page(s): the pristine scans come to "
        f"~{pages * PRISTINE_KB_PER_SCANNED_PAGE / 1024:.1f}MB; an office-profile tree would "
        f"add ~{pages * OFFICE_MB_PER_SCANNED_PAGE:.0f}MB"
    )
