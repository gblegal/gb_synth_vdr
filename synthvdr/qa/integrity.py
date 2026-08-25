"""Integrity gates: subset reconciliation, fact-sheet reconciliation, discoverability."""

from __future__ import annotations

import re
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


_DASH_ONLY_RE = re.compile(r"^[-\u2014\u2013\s]*$")  # hyphen, em dash, en dash, whitespace


def _cell_has_no_superseded_values(cell: str) -> bool:
    """True for any cell that carries no real superseded value.

    This is a property of the cell's content, not a fixed list of accepted
    sentinel strings: the target set (dashes and whitespace, any count, any
    mix of hyphen/em dash/en dash) is open-ended, while the false-positive
    set is empty — no legitimate figure value is made up solely of dashes.
    A cell of "--" must mean "none" exactly as much as "\u2014" does, and a
    literal list of accepted sentinels ("\u2014", "-", "") would leave "--"
    treated as a real value, which is then found "surviving" inside the
    separator row of any ordinary markdown table anywhere in the room.
    """
    return bool(_DASH_ONLY_RE.match(cell))


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
        superseded = [] if _cell_has_no_superseded_values(cells[2]) else [
            s.strip() for s in cells[2].split(";") if s.strip()
        ]
        figures.append(CanonicalFigure(key=cells[0], value=cells[1], superseded=superseded))
    return figures


def _has_canonical_figures_heading(fact_sheet_text: str) -> bool:
    return any(
        line.strip().lower().startswith("## canonical figures")
        for line in fact_sheet_text.splitlines()
    )


def _isolated_contains(needle: str, haystack: str) -> bool:
    """True if `needle` occurs in `haystack` at a real word boundary.

    Deliberately not a `\b`-word-boundary regex: `\b` fires between a
    non-word and a word character, but a currency symbol like "£" is
    itself non-word, so "\b£64.0m" never matches right after a space —
    neither side of that boundary is a word character, so every
    currency-prefixed figure would silently stop matching. This checks the
    actual neighbouring character instead: a match is rejected only when
    the character immediately before or after it is alphanumeric, which is
    exactly the case that makes "700m" a false hit inside "GBP 3700m" or
    "25m" a false miss inside "1725m" (the digit '7' butts right up
    against it) while still accepting a figure that starts or ends with
    punctuation, or sits at the very start or end of the text.
    """
    if not needle:
        return False
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            return False
        before = haystack[idx - 1] if idx > 0 else None
        after_idx = idx + len(needle)
        after = haystack[after_idx] if after_idx < len(haystack) else None
        before_is_alnum = before is not None and before.isalnum()
        after_is_alnum = after is not None and after.isalnum()
        if not before_is_alnum and not after_is_alnum:
            return True
        start = idx + 1


def gate_13_fact_sheet(ctx):
    """Canonical figures must appear in the room; superseded ones must not survive.

    Presence is checked with `_isolated_contains`, not plain substring `in`:
    a canonical or superseded value must occur at a real word boundary, not
    merely as a run of characters embedded inside a longer token. Without
    that, "700m" would be found "surviving" inside "GBP 3700m" (false FAIL)
    and "25m" would be found "present" inside "1725m" (false PASS — the
    worse direction, since this is the anti-thin-filler gate).

    A superseded value that is itself a substring of its own figure's
    canonical value at a real word boundary (e.g. canonical "31 March
    2026", superseded "March 2026") can never pass even the isolated check:
    the canonical value's own required presence in the room always carries
    that occurrence of the superseded value with it. That is a
    self-contradiction in the fact sheet, not a defect in the room, and no
    room edit could ever satisfy it — so it is detected and reported
    separately, naming the fact sheet as the thing to fix, rather than
    surfacing as an unfixable "superseded value still present". This check
    stays scoped to the same figure; a value that merely happens to overlap
    a *different* figure's canonical value is not this defect (see
    `_isolated_contains` above, which already stops "700m" from being
    falsely read inside "3700m" belonging to another figure entirely).
    """
    fact_sheet = ctx.key_root / "fact-sheet.md"
    if not fact_sheet.is_file():
        return skip("13", "fact-sheet reconciliation", "_key/fact-sheet.md absent")
    files = [p for p in ctx.blind_files() if p.suffix in (".md", ".csv")]
    if not files:
        return skip("13", "fact-sheet reconciliation", f"{ctx.blind_root} absent or empty")
    text = fact_sheet.read_text(encoding="utf-8")
    figures = parse_canonical_figures(text)
    if not figures:
        if _has_canonical_figures_heading(text):
            return skip(
                "13",
                "fact-sheet reconciliation",
                "'## Canonical figures' table present but malformed — no parsable "
                "Key | Value | Superseded row (check for a missing column)",
            )
        return skip("13", "fact-sheet reconciliation", "no '## Canonical figures' table in fact sheet")
    corpus = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in files)
    problems = []
    for figure in figures:
        if not _isolated_contains(figure.value, corpus):
            problems.append(f"{figure.key}: canonical value {figure.value!r} appears nowhere")
        for old in figure.superseded:
            if _isolated_contains(old, figure.value):
                problems.append(
                    f"{figure.key}: superseded value {old!r} is a substring of its own "
                    f"canonical value {figure.value!r} — the fact sheet is self-contradictory "
                    "(this can never pass), fix the fact sheet, not the room"
                )
                continue
            if _isolated_contains(old, corpus):
                problems.append(f"{figure.key}: superseded value {old!r} still present")
    if problems:
        return fail("13", "fact-sheet reconciliation", "; ".join(problems[:5]))
    return ok("13", "fact-sheet reconciliation", f"{len(figures)} canonical figures reconciled")


def gate_15_discoverability(ctx):
    """A finding nobody can reach from the blind room is a corpus bug no grep detects.

    `discoverable_from_blind` is a tri-state, and the distinction matters:
    False means an auditor tried and could not reach the finding from the
    blind room; None means no one has audited it yet. Both are gate
    failures, but they are different failures with different fixes, so
    both are named when both are present — reporting only the first
    category and hiding the second would cost the author a second gate run
    to learn something the gate already knew on the first pass. An
    unaudited finding is not, by default, presumed reachable.
    """
    findings = ctx.findings.findings
    if not findings:
        return skip("15", "discoverability audit", "no findings in the answer key")
    unreachable = [f.id for f in findings if f.discoverable_from_blind is False]
    unaudited = [f.id for f in findings if f.discoverable_from_blind is None]
    if unreachable or unaudited:
        parts = []
        if unreachable:
            parts.append(f"not reachable from the blind room: {', '.join(unreachable[:5])}")
        if unaudited:
            parts.append(f"not audited: {', '.join(unaudited[:5])} — run the vdr-auditor subagent")
        return fail("15", "discoverability audit", "; ".join(parts))
    return ok("15", "discoverability audit", f"{len(findings)} findings reachable from the blind room")
