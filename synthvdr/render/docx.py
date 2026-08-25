"""Optional DOCX render. Never imported at core-build time.

`render_tree_docx` deliberately does NOT delete-and-rebuild its output
directory the way `synthvdr.twin`, `synthvdr.subset` and
`synthvdr.index_build` do. All three of those shipped a defect where they
destroyed data they did not own before the shared ownership guard in
`synthvdr.ownership` closed it — the cheapest fix to a destructive site is
one that never exists. This writer only ever creates directories
(`mkdir(parents=True, exist_ok=True)`) and (re)writes the `.docx` files it
is responsible for; it never removes a file or directory, including a
render whose source document was since renamed or deleted. That kind of
staleness is a corpus-consistency problem, not a rendering problem, and is
caught instead by `synthvdr.qa.renders.gate_16_render_parity`, checking in
both directions: sources missing a render, and renders missing a source.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import List

from ..schema import FindingSet


class RenderUnavailable(Exception):
    """A render toolchain is not installed."""


# CommonMark requires whitespace between the '#' run and the heading text —
# `#MeToo` or `#1 supplier` are NOT headings in any markdown dialect, they
# are paragraphs that happen to start with a hash. A bare `stripped.startswith
# ("#")` check (this module's original implementation) got that wrong, and
# `lstrip("# ")` compounded it by eating any further leading '#'/' ' pairs —
# a heading whose own title legitimately starts with '#' (e.g. "# #1
# priority") lost that leading character too. Bounded at 6 hashes, per
# CommonMark ATX headings — a 7th leading '#' means the line has no opening
# sequence at all, and stays a plain paragraph, hashes and all.
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def rotation_for(slot_id: str, page: int) -> float:
    """Deterministic skew in +/- 0.4-1.1 degrees, derived from sha256 of the
    slot id and page number. No RNG, no bare `hash()`, no clock: the same
    (slot_id, page) pair must produce the same angle on every run, in every
    process, regardless of `PYTHONHASHSEED` — sha256 does not depend on it,
    which is precisely why it is used here instead of Python's built-in
    `hash()`, whose string hashing IS seed-salted.
    """
    digest = hashlib.sha256(f"{slot_id}:{page}".encode("utf-8")).digest()
    magnitude = 0.4 + (digest[0] / 255) * 0.7
    return magnitude if digest[1] % 2 == 0 else -magnitude


def scanned_slots(findings: FindingSet, count: int) -> List[str]:
    """Pick `count` scanned slots, drawn only from evidence documents — the
    OCR challenge belongs where the substance is, so OCR failure costs the
    tool a finding. Never backfilled with non-evidence documents: if
    `count` exceeds the number of distinct evidence paths, fewer than
    `count` slots are returned rather than diluting the bias.

    Deterministic and reproducible across processes and across reorderings
    of `findings`: `all_evidence_paths()` returns a set, so any incoming
    order collapses through it, and the two sorts below (first
    lexicographic, then by sha256 digest) fix one specific, seed-independent
    order out of that set every time.
    """
    evidence = sorted(findings.all_evidence_paths())
    ordered = sorted(evidence, key=lambda p: hashlib.sha256(p.encode("utf-8")).hexdigest())
    return ordered[:count]


def render_tree_docx(src: Path, out: Path) -> int:
    """Render every markdown file under `src` to a `.docx` twin under `out`,
    mirroring `src`'s relative layout. Returns the number of files written.

    Non-destructive by design (see module docstring): creates directories
    as needed and writes/overwrites the `.docx` files it produces, but
    never deletes anything, including a stale render left behind by a
    since-renamed or since-deleted source.
    """
    try:
        from docx import Document
    except ImportError as exc:
        raise RenderUnavailable(
            "python-docx is not installed. Install it with: pip install 'synthvdr[docx]'"
        ) from exc

    written = 0
    for path in sorted(src.rglob("*.md")):
        rel = path.relative_to(src).with_suffix(".docx")
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        document = Document()
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.rstrip()
            heading = _ATX_HEADING.match(stripped)
            if heading:
                # group(1) is exactly the matched hashes (1-6 of them, by
                # construction of the regex); group(2) is exactly what
                # follows the required whitespace — never a blanket
                # lstrip, so a title starting with '#' is preserved.
                level = min(len(heading.group(1)), 4)
                document.add_heading(heading.group(2), level=level)
            elif stripped:
                document.add_paragraph(stripped)
        document.save(str(target))
        written += 1
    return written
