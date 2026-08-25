"""Integrity gates: subset reconciliation, fact-sheet reconciliation, discoverability."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..subset import check_subset
from .runner import fail, ok, skip


def gate_11_subset(ctx):
    subset_dir = ctx.room / "subset"
    if not subset_dir.is_dir():
        return skip("11", "subset reconciliation", "subset/ not built")
    report = check_subset(ctx.room, ctx.conf, ctx.findings, out_dir=subset_dir)
    if not report.complete:
        return fail("11", "subset reconciliation", "; ".join(report.errors[:5]))
    return ok(
        "11",
        "subset reconciliation",
        f"{report.total} documents, {report.findings_covered}/{report.findings_total} findings covered",
    )


@dataclass(frozen=True)
class CanonicalFigure:
    key: str
    value: str
    superseded: List[str] = field(default_factory=list)


def parse_canonical_figures(fact_sheet_text: str) -> List[CanonicalFigure]:
    figures: List[CanonicalFigure] = []
    in_table = False
    for line in fact_sheet_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## canonical figures"):
            in_table = True
            continue
        if in_table and stripped.startswith("##"):
            break
        if not in_table or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3 or cells[0].lower() == "key" or set(cells[0]) <= {"-", ":"}:
            continue
        superseded = [] if cells[2] in ("—", "-", "") else [
            s.strip() for s in cells[2].split(";") if s.strip()
        ]
        figures.append(CanonicalFigure(key=cells[0], value=cells[1], superseded=superseded))
    return figures


def gate_13_fact_sheet(ctx):
    """Canonical figures must appear in the room; superseded ones must not survive.

    A superseded value that is itself a substring of its own figure's
    canonical value (e.g. canonical "31 March 2026", superseded "March 2026")
    can never pass the plain substring check below: the canonical value's own
    required presence in the room always carries the superseded substring
    with it. That is a self-contradiction in the fact sheet, not a defect in
    the room, and no room edit could ever satisfy it — so it is detected and
    reported separately, naming the fact sheet as the thing to fix, rather
    than surfacing as an unfixable "superseded value still present".
    """
    fact_sheet = ctx.key_root / "fact-sheet.md"
    if not fact_sheet.is_file():
        return skip("13", "fact-sheet reconciliation", "_key/fact-sheet.md absent")
    files = [p for p in ctx.blind_files() if p.suffix in (".md", ".csv")]
    if not files:
        return skip("13", "fact-sheet reconciliation", f"{ctx.blind_root} absent or empty")
    figures = parse_canonical_figures(fact_sheet.read_text(encoding="utf-8"))
    if not figures:
        return skip("13", "fact-sheet reconciliation", "no '## Canonical figures' table in fact sheet")
    corpus = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in files)
    problems = []
    for figure in figures:
        if figure.value not in corpus:
            problems.append(f"{figure.key}: canonical value {figure.value!r} appears nowhere")
        for old in figure.superseded:
            if old in figure.value:
                problems.append(
                    f"{figure.key}: superseded value {old!r} is a substring of its own "
                    f"canonical value {figure.value!r} — the fact sheet is self-contradictory "
                    "(this can never pass), fix the fact sheet, not the room"
                )
                continue
            if old in corpus:
                problems.append(f"{figure.key}: superseded value {old!r} still present")
    if problems:
        return fail("13", "fact-sheet reconciliation", "; ".join(problems[:5]))
    return ok("13", "fact-sheet reconciliation", f"{len(figures)} canonical figures reconciled")


def gate_15_discoverability(ctx):
    """A finding nobody can reach from the blind room is a corpus bug no grep detects.

    `discoverable_from_blind` is a tri-state, and the distinction matters:
    False means an auditor tried and could not reach the finding from the
    blind room; None means no one has audited it yet. Both are gate
    failures, but they are different failures with different fixes, so they
    are reported separately rather than folded into one "not True" bucket —
    an unaudited finding is not, by default, presumed reachable.
    """
    findings = ctx.findings.findings
    if not findings:
        return skip("15", "discoverability audit", "no findings in the answer key")
    unreachable = [f.id for f in findings if f.discoverable_from_blind is False]
    unaudited = [f.id for f in findings if f.discoverable_from_blind is None]
    if unreachable:
        return fail(
            "15",
            "discoverability audit",
            f"not reachable from the blind room: {', '.join(unreachable[:5])}",
        )
    if unaudited:
        return fail(
            "15",
            "discoverability audit",
            f"not audited: {', '.join(unaudited[:5])} — run the vdr-auditor subagent",
        )
    return ok("15", "discoverability audit", f"{len(findings)} findings reachable from the blind room")
