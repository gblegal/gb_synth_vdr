"""Integrity gates: subset reconciliation, fact-sheet reconciliation, discoverability,
answer-key validation."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import List

from ..schema import validate as validate_answer_key
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


def _is_dash_char(ch: str) -> bool:
    """True for any dash-like character, checked as a property, not a list.

    Unicode category "Pd" (Dash Punctuation) already covers HYPHEN-MINUS,
    HYPHEN, NON-BREAKING HYPHEN, FIGURE DASH, EN DASH, EM DASH and the rest
    of that family in one test. U+2212 MINUS SIGN is the one dash-shaped
    character Unicode puts outside "Pd" — it is category "Sm" (Symbol,
    math) — so it is named explicitly rather than folded into the
    category check.
    """
    return unicodedata.category(ch) == "Pd" or ch == "\u2212"


def _cell_has_no_superseded_values(cell: str) -> bool:
    """True for any cell that carries no real superseded value.

    This is a property of the cell's content, not a fixed list of accepted
    sentinel strings: the target set (any dash-like character, any count,
    any mix, plus whitespace) is open-ended, while the false-positive set
    is empty — no legitimate figure value is made up solely of dashes and
    whitespace. A single ASCII "-" must mean "none" exactly as much as a
    NON-BREAKING HYPHEN or FIGURE DASH does; a literal list of accepted
    dash characters would always be one character behind whatever a fact
    sheet's editing tool happens to autocorrect punctuation into. A
    genuine negative figure like "-5m" is not dash-only — "5m" is neither
    a dash nor whitespace — and correctly survives as a real value.
    """
    return all(_is_dash_char(ch) or ch.isspace() for ch in cell)


def parse_canonical_figures(fact_sheet_text: str) -> List[CanonicalFigure]:
    """Every canonical figure declared under EVERY `## Canonical figures` heading
    in the fact sheet, not just the first.

    Final review, F3: this used to `break` out of the whole scan on the first
    `##` heading following a canonical-figures table, which silently stopped
    parsing at that point — a fact sheet that groups figures under more than
    one `## Canonical figures` heading (financial figures first, commercial
    figures under their own heading further down) had every figure after the
    first heading's table go unchecked, with gate 13 reporting a confident
    PASS on whatever fraction it happened to see. `continue` instead of
    `break`: leaving the current table (`in_table = False`) on an unrelated
    `##` heading, but carrying on scanning the rest of the file so a LATER
    `## Canonical figures` heading is picked back up.
    """
    figures: List[CanonicalFigure] = []
    in_table = False
    for line in fact_sheet_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## canonical figures"):
            in_table = True
            continue
        if in_table and stripped.startswith("##"):
            in_table = False
            continue
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


def _diagnose_malformed_table(fact_sheet_text: str) -> str:
    """Explain why the '## Canonical figures' table produced no figures.

    Distinguishes the shapes an author is actually likely to hit, rather
    than guessing "missing column" for every empty-figures result: that
    diagnosis is right for a 2-column header, but wrong — and misleading —
    for a well-formed 3-column header that simply has no data rows under
    it yet, or for a heading with no table beneath it at all.
    """
    # Mirrors parse_canonical_figures's continue-not-break fix (F3): scans
    # every '## Canonical figures' table in the file, not just the first,
    # so a diagnosis of "no rows"/"malformed header" is never reported
    # against only a truncated prefix of the fact sheet.
    in_table = False
    rows: List[List[str]] = []
    for line in fact_sheet_text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## canonical figures"):
            in_table = True
            continue
        if in_table and stripped.startswith("##"):
            in_table = False
            continue
        if not in_table or not stripped.startswith("|"):
            continue
        rows.append([c.strip() for c in stripped.strip("|").split("|")])

    if not rows:
        return "'## Canonical figures' heading found but no table rows beneath it"

    header = rows[0]
    if len(header) < 3:
        return (
            "'## Canonical figures' table present but malformed — its header row "
            "has fewer than 3 columns (Key | Value | Superseded); check for a "
            "missing column"
        )

    data_rows = [r for r in rows if r[0].lower() != "key" and not (set(r[0]) <= {"-", ":"})]
    if not data_rows:
        return (
            "'## Canonical figures' table present with a valid 3-column header but "
            "no data rows beneath it"
        )

    return "'## Canonical figures' table present but malformed"


def _would_extend_a_token(ch) -> bool:
    """True iff `ch` sitting next to a match would extend it into a longer
    number or word — the only thing `_isolated_contains` exists to detect.

    Deliberately `isdecimal() or isalpha()`, not `isalnum()`. `isalnum()`
    is Unicode-wide and also true for category "No" (Number, other), which
    includes superscript and subscript digits such as footnote markers
    ("²", "¹") — those are annotations glued onto a figure, not digits
    extending it, and must not disqualify a match. `isdecimal()` still
    catches genuine embedding by a non-ASCII decimal digit (full-width
    "０", Arabic-Indic "٣"), and `isalpha()` still catches embedding inside
    a word — between them they express the actual invariant, no broader
    and no narrower: a figure must not sit inside a longer number or word.
    """
    return ch is not None and (ch.isdecimal() or ch.isalpha())


def _isolated_contains(needle: str, haystack: str) -> bool:
    """True if `needle` occurs in `haystack` at a real word boundary.

    Deliberately not a `\b`-word-boundary regex: `\b` fires between a
    non-word and a word character, but a currency symbol like "£" is
    itself non-word, so "\b£64.0m" never matches right after a space —
    neither side of that boundary is a word character, so every
    currency-prefixed figure would silently stop matching. This checks the
    actual neighbouring character instead, via `_would_extend_a_token`: a
    match is rejected only when the character immediately before or after
    it would extend it into a longer number or word, which is exactly the
    case that makes "700m" a false hit inside "GBP 3700m" or "25m" a false
    miss inside "1725m" (the digit '7' butts right up against it), while
    still accepting a figure that starts or ends with punctuation — or a
    footnote marker like "GBP 725m¹" — or sits at the very start or end of
    the text.
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
        if not _would_extend_a_token(before) and not _would_extend_a_token(after):
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
            return skip("13", "fact-sheet reconciliation", _diagnose_malformed_table(text))
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


def gate_17_answer_key_validation(ctx):
    """`synthvdr.schema.validate()` — the answer key's own internal-consistency
    check — wired into the gate suite so a malformed key can never reach
    `/vdr-package` silently.

    Before this gate existed, `validate()` was called nowhere in `synthvdr/`
    at all: it appeared only as a manual step documented in the
    `/vdr-findings` skill. `load_findings`/`load_distractors` only check
    required fields, types and severities — they never ran a dangling
    `cross_links` entry, a `multi_document`/`corroboration` mismatch, a
    corroboration path that re-lists its own source, or a distractor whose
    `location`/`resolution` doubles as real evidence for a finding, through
    `validate()`'s own checks. A findings/distractors document with any of
    those defects could load cleanly, pass every one of the other sixteen
    gates, pass `/vdr-qa --strict` and pass `/vdr-package --strict`, and
    ship with a silently broken answer key — precisely the failure class
    this project's SKIP discipline exists to rule out, just for a check
    nobody had ever wired to a gate. Made a gate, rather than called
    directly from `/vdr-qa`'s or `/vdr-package`'s CLI, so it inherits SKIP
    discipline and `--strict` the same way every other answer-key check in
    this module already does, instead of being a second, differently-gated
    mechanism.

    SKIPs only when there is nothing at all to validate (no findings and no
    distractors loaded) — the same convention gate 15 uses for an absent
    findings file. `validate()`'s own return value is a list of
    human-readable problem strings; they are passed straight through to
    `detail`, never summarised down to "the answer key is invalid", so a
    FAIL names the specific defect(s) the way every other gate here does.
    """
    if not ctx.findings.findings and not ctx.distractors:
        return skip("17", "answer-key validation", "no findings or distractors to validate")
    problems = validate_answer_key(ctx.findings, ctx.distractors)
    if problems:
        return fail("17", "answer-key validation", "; ".join(problems[:5]))
    return ok(
        "17",
        "answer-key validation",
        f"{len(ctx.findings.findings)} finding(s), {len(ctx.distractors)} distractor(s) internally consistent",
    )
