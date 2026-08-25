"""Score a tool's output against the answer key.

Matching is two-stage. Stage one is deterministic: a tool that cites a finding's
source or corroboration documents is matched to that finding. Stage two is LLM
adjudication of what is left, performed by the /vdr-score skill and passed back
in as Adjudication rows, so the scoring logic itself stays deterministic and
every judgement is recorded rather than re-derived.

Provenance. A scorecard is only meaningful if the tool output being scored was
actually produced against the room whose answer key is doing the scoring —
otherwise recall/precision are a confident, precise, entirely meaningless
number. check_provenance() compares ToolOutput.room_hash against
`_key/manifest.json`'s content_hash and raises ProvenanceError on a proven
mismatch. Absence of either input (no manifest yet, or an empty room_hash) is
NOT treated as a mismatch — /vdr-package, which writes the manifest, is a
later step in this project's build order, so "no manifest yet" is the normal
case today. That path scores normally but is reported UNVERIFIED, never
silently as if it had been checked.

Adjudications. `_key/adjudications.yaml` is auto-loaded from the room by the
CLI when present — there is no flag for it, because every other answer-key
artefact (findings.yaml, distractors.yaml) is read the same way. Absence is
normal (adjudication is a later step in the pipeline) and is reported as "0
adjudications applied", distinctly from a file that exists and applied N, so
a silent zero is never mistaken for "there was nothing to load". A file that
exists but fails to parse, or that names a tool_index outside the tool
output or a finding_id absent from the answer key, is always an error
(AdjudicationError) — never silently treated as empty or dropped, because a
scorecard rendered without a real adjudication is missing a match it was
supposed to have.

A recall of 0.0 does not by itself mean "nothing could be matched" — it is
also what a fully-adjudicated run reports when every report was positively
confirmed to match nothing. Scorecard.unadjudicated is what tells the two
apart: non-empty means some reports are still unresolved and could still
raise recall once adjudicated, so render_scorecard marks the Recall line and
the reported findings themselves as provisional in that case; empty means
every report was resolved one way or the other, and the number is final.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

from .schema import SEVERITIES, Distractor, FindingSet


@dataclass(frozen=True)
class ToolFinding:
    title: str
    severity: str
    documents: List[str] = field(default_factory=list)
    summary: str = ""


@dataclass(frozen=True)
class ToolOutput:
    tool: str
    room_hash: str
    findings: List[ToolFinding]


@dataclass(frozen=True)
class Adjudication:
    tool_index: int
    finding_id: Optional[str]
    reason: str


@dataclass
class Scorecard:
    by_severity: Dict[str, Tuple[int, int]]
    recall: float
    precision: float
    false_alarms: List[str]
    partial_trails: List[str]
    misses: List[str]
    hit_table: List[Tuple[str, str, bool]]
    unadjudicated: List[int]


class ProvenanceError(Exception):
    """The tool output was proven to have been produced against a different room."""


@dataclass(frozen=True)
class ProvenanceStatus:
    """The outcome of check_provenance() for a run that was allowed to proceed.

    verified is True only when both room_hash and the manifest's content_hash
    were present and equal. detail is a human-readable sentence naming what
    was compared, or what was missing — it is what render_scorecard shows, so
    it must stand on its own without the caller having to explain further.
    """

    verified: bool
    detail: str


_MANIFEST_RELATIVE_PATH = Path("_key") / "manifest.json"


def check_provenance(room: Path, output: ToolOutput) -> ProvenanceStatus:
    """Compare `output.room_hash` against `_key/manifest.json`'s content_hash.

    - Manifest present, both hashes non-empty, and they differ: raises
      ProvenanceError. This is the one case that must never silently produce
      a scorecard — scoring a report against the wrong room's answer key
      yields a confident, precise, entirely meaningless result.
    - Manifest absent, unreadable, or either hash empty: returns an
      unverified ProvenanceStatus naming what was missing. This is the
      normal case today, since /vdr-package (which writes the manifest)
      is a later step in this project's build order.
    - Manifest present and both hashes present and equal: returns a
      verified ProvenanceStatus.
    """
    manifest_path = room / _MANIFEST_RELATIVE_PATH
    if not manifest_path.is_file():
        return ProvenanceStatus(
            False,
            f"UNVERIFIED provenance — no {_MANIFEST_RELATIVE_PATH} found in this room; "
            "the tool output's room_hash could not be checked against it.",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ProvenanceStatus(
            False,
            f"UNVERIFIED provenance — {_MANIFEST_RELATIVE_PATH} could not be read ({exc}); "
            "the tool output's room_hash could not be checked against it.",
        )
    manifest_hash = ""
    if isinstance(manifest, dict):
        manifest_hash = manifest.get("content_hash") or ""
    if not manifest_hash:
        return ProvenanceStatus(
            False,
            f"UNVERIFIED provenance — {_MANIFEST_RELATIVE_PATH} has no content_hash; "
            "the tool output's room_hash could not be checked against it.",
        )
    if not output.room_hash:
        return ProvenanceStatus(
            False,
            "UNVERIFIED provenance — the tool output carries no room_hash; "
            f"it could not be checked against {_MANIFEST_RELATIVE_PATH}'s content_hash "
            f"({manifest_hash!r}).",
        )
    if output.room_hash != manifest_hash:
        raise ProvenanceError(
            f"tool output room_hash {output.room_hash!r} does not match this room's "
            f"{_MANIFEST_RELATIVE_PATH} content_hash {manifest_hash!r} — the output was "
            "produced against a different room; refusing to score it against this "
            "room's answer key."
        )
    return ProvenanceStatus(
        True,
        f"provenance verified — room_hash matches {_MANIFEST_RELATIVE_PATH}'s "
        f"content_hash ({manifest_hash!r}).",
    )


class AdjudicationError(Exception):
    """`_key/adjudications.yaml` is malformed, or an entry cannot be
    reconciled against the tool output or the answer key it is meant to
    apply to. Always raised, never swallowed into an empty adjudication
    list — a malformed or unreconcilable file means the scorecard is
    missing matches it was supposed to have, which is a worse failure mode
    than refusing to score at all.
    """


@dataclass(frozen=True)
class AdjudicationSummary:
    """What auto-loading `_key/adjudications.yaml` did, for rendering.

    applied is the number of adjudications actually passed to score() — 0
    both when the file is absent and when it is present but empty, but
    detail always distinguishes the two in words, so a silent zero is never
    mistaken for "there was nothing to load".
    """

    applied: int
    detail: str


_ADJUDICATIONS_RELATIVE_PATH = Path("_key") / "adjudications.yaml"


def load_adjudications(path: Path) -> List[Adjudication]:
    """Parse `_key/adjudications.yaml`.

    Raises AdjudicationError on anything that is not a well-formed list of
    {tool_index, finding_id, reason} rows. A missing tool_index or reason,
    or a tool_index/finding_id of the wrong type, is a shape error caught
    here; whether tool_index and finding_id actually resolve against a
    specific run is checked separately by validate_adjudications, since
    that needs the ToolOutput and FindingSet this file is being applied to.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AdjudicationError(f"{path}: could not be read as YAML — {exc}") from exc
    doc = raw or {}
    if not isinstance(doc, dict) or "adjudications" not in doc:
        raise AdjudicationError(f"{path}: missing required top-level key 'adjudications'")
    rows = doc["adjudications"]
    if not isinstance(rows, list):
        raise AdjudicationError(f"{path}: 'adjudications' must be a list")

    result: List[Adjudication] = []
    for index, row in enumerate(rows):
        context = f"{path}: adjudications[{index}]"
        if not isinstance(row, dict):
            raise AdjudicationError(f"{context} is not a mapping — got {type(row).__name__}")
        if "tool_index" not in row:
            raise AdjudicationError(f"{context}: missing required field 'tool_index'")
        tool_index = row["tool_index"]
        if not isinstance(tool_index, int) or isinstance(tool_index, bool):
            raise AdjudicationError(f"{context}: tool_index must be an integer, got {tool_index!r}")
        if "finding_id" not in row:
            raise AdjudicationError(
                f"{context}: missing required field 'finding_id' — use null for a confirmed "
                "non-match, not an absent key"
            )
        finding_id = row["finding_id"]
        if finding_id is not None and not isinstance(finding_id, str):
            raise AdjudicationError(f"{context}: finding_id must be a string or null, got {finding_id!r}")
        if "reason" not in row:
            raise AdjudicationError(f"{context}: missing required field 'reason'")
        result.append(Adjudication(tool_index=tool_index, finding_id=finding_id, reason=row["reason"]))
    return result


def validate_adjudications(
    adjudications: Sequence[Adjudication],
    output: ToolOutput,
    findings: FindingSet,
    source: str = "adjudications",
) -> None:
    """Cross-check every adjudication against the specific tool output and
    answer key it is meant to score. A tool_index outside the reported
    findings, a finding_id absent from the answer key, or two adjudications
    naming the same tool_index (one would silently override the other in
    score()) are all errors naming the offending entry — none of them may
    be silently dropped, because a dropped adjudication is a silently
    wrong score.
    """
    known_ids = set(findings.by_id)
    seen: Dict[int, Optional[str]] = {}
    for adjudication in adjudications:
        if not (0 <= adjudication.tool_index < len(output.findings)):
            raise AdjudicationError(
                f"{source}: adjudication tool_index {adjudication.tool_index} is out of range — "
                f"the tool output has {len(output.findings)} finding(s) "
                f"(valid indices: 0..{len(output.findings) - 1})"
            )
        if adjudication.finding_id is not None and adjudication.finding_id not in known_ids:
            raise AdjudicationError(
                f"{source}: adjudication for tool_index {adjudication.tool_index} names "
                f"finding_id {adjudication.finding_id!r}, which does not exist in the answer key"
            )
        if adjudication.tool_index in seen:
            raise AdjudicationError(
                f"{source}: tool_index {adjudication.tool_index} is adjudicated more than once "
                f"({seen[adjudication.tool_index]!r} and {adjudication.finding_id!r}) — one of "
                "these would silently override the other"
            )
        seen[adjudication.tool_index] = adjudication.finding_id


def load_adjudications_for_room(
    room: Path, output: ToolOutput, findings: FindingSet
) -> Tuple[List[Adjudication], AdjudicationSummary]:
    """Auto-load `_key/adjudications.yaml` from `room` if it exists.

    No file is the normal case today (adjudication is a later step in the
    pipeline) and is not a warning — it returns an empty adjudication list
    with a summary that says plainly that there was no file to load, rather
    than the same "0 applied" a present-but-empty file would also produce.
    A present file that is malformed, or that cannot be reconciled against
    `output`/`findings`, raises AdjudicationError; it is never silently
    treated as "no adjudications".
    """
    path = room / _ADJUDICATIONS_RELATIVE_PATH
    if not path.is_file():
        return [], AdjudicationSummary(
            0, f"no {_ADJUDICATIONS_RELATIVE_PATH} found in this room — 0 adjudications applied"
        )
    adjudications = load_adjudications(path)
    validate_adjudications(adjudications, output, findings, source=str(path))
    return adjudications, AdjudicationSummary(
        len(adjudications),
        f"{len(adjudications)} adjudication(s) applied from {_ADJUDICATIONS_RELATIVE_PATH}",
    )


_MD_FINDING = re.compile(r"^#{2,4}\s*(.+)$", re.MULTILINE)
_MD_PATH = re.compile(r"`([\w./-]+\.(?:md|csv|pdf|docx))`")
_MD_SEVERITY = re.compile(r"\b(critical|high|medium|low)\b", re.IGNORECASE)


def parse_markdown_report(text: str, tool: str = "unknown") -> ToolOutput:
    findings: List[ToolFinding] = []
    headings = list(_MD_FINDING.finditer(text))
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[start:end]
        severity_match = _MD_SEVERITY.search(body)
        findings.append(
            ToolFinding(
                title=match.group(1).strip(),
                severity=(severity_match.group(1).lower() if severity_match else "medium"),
                documents=_MD_PATH.findall(body),
                summary=" ".join(body.split())[:400],
            )
        )
    return ToolOutput(tool=tool, room_hash="", findings=findings)


def load_tool_output(path: Path) -> ToolOutput:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        doc = json.loads(text)
        return ToolOutput(
            tool=doc.get("tool", "unknown"),
            room_hash=doc.get("room_hash", ""),
            findings=[
                ToolFinding(
                    title=row.get("title", ""),
                    severity=row.get("severity", "medium"),
                    documents=list(row.get("documents") or []),
                    summary=row.get("summary", ""),
                )
                for row in doc.get("findings") or []
            ],
        )
    return parse_markdown_report(text, tool=path.stem)


def prematch(output: ToolOutput, findings: FindingSet) -> Tuple[Dict[int, str], List[int]]:
    by_source = {f.source: f.id for f in findings.findings}
    by_corroboration: Dict[str, str] = {}
    for finding in findings.findings:
        for path in finding.corroboration:
            by_corroboration.setdefault(path, finding.id)

    matched: Dict[int, str] = {}
    unmatched: List[int] = []
    for index, reported in enumerate(output.findings):
        hit = next((by_source[d] for d in reported.documents if d in by_source), None)
        if hit is None:
            hit = next((by_corroboration[d] for d in reported.documents if d in by_corroboration), None)
        if hit is None:
            unmatched.append(index)
        else:
            matched[index] = hit
    return matched, unmatched


def score(
    output: ToolOutput,
    findings: FindingSet,
    distractors: Sequence[Distractor],
    adjudications: Sequence[Adjudication] = (),
) -> Scorecard:
    matched, unmatched = prematch(output, findings)
    adjudicated = {a.tool_index: a.finding_id for a in adjudications}
    # Adjudications take precedence over the deterministic pre-match where
    # both apply — in either direction. A finding_id assigns or reassigns
    # the match for that index; None is a positive confirmation that the
    # index matches nothing, which must be able to remove a pre-match too,
    # not just decline to add one.
    for index, finding_id in adjudicated.items():
        if finding_id:
            matched[index] = finding_id
        else:
            matched.pop(index, None)
    still_unmatched = [i for i in unmatched if i not in adjudicated]

    distractor_docs = {d.location: d.id for d in distractors}
    false_alarms: List[str] = []
    for index, reported in enumerate(output.findings):
        if index in matched:
            continue
        for document in reported.documents:
            if document in distractor_docs:
                false_alarms.append(distractor_docs[document])
                break

    found_ids = set(matched.values())
    by_severity: Dict[str, Tuple[int, int]] = {}
    for severity in SEVERITIES:
        pool = [f for f in findings.findings if f.severity == severity]
        by_severity[severity] = (len([f for f in pool if f.id in found_ids]), len(pool))

    total = len(findings.findings)
    recall = len(found_ids) / total if total else 0.0
    reported_count = len(output.findings)
    precision = len(found_ids) / reported_count if reported_count else 0.0

    partial: List[str] = []
    for index, finding_id in matched.items():
        finding = findings.by_id[finding_id]
        if finding.multi_document:
            cited = set(output.findings[index].documents)
            if not set(finding.evidence_paths()) <= cited:
                partial.append(finding_id)

    return Scorecard(
        by_severity=by_severity,
        recall=recall,
        precision=precision,
        false_alarms=sorted(set(false_alarms)),
        partial_trails=sorted(set(partial)),
        misses=sorted(f.id for f in findings.findings if f.id not in found_ids),
        hit_table=[(f.id, f.severity, f.id in found_ids) for f in findings.findings],
        unadjudicated=still_unmatched,
    )


def render_scorecard(
    card: Scorecard,
    output: ToolOutput,
    findings: FindingSet,
    provenance: Optional[ProvenanceStatus] = None,
    adjudication_summary: Optional[AdjudicationSummary] = None,
) -> str:
    provisional = bool(card.unadjudicated)
    lines = [f"# Scorecard — {output.tool}", ""]
    if provenance is not None:
        marker = "verified" if provenance.verified else "UNVERIFIED"
        lines += [f"- **Provenance: {marker}** — {provenance.detail}", ""]
    if adjudication_summary is not None:
        lines += [f"- **Adjudications:** {adjudication_summary.detail}", ""]
    lines += [
        f"- **Recall:** {card.recall:.0%} ({len(findings.findings) - len(card.misses)}/{len(findings.findings)})"
        + (" — provisional, pending adjudication" if provisional else ""),
        f"- **Precision:** {card.precision:.0%}",
        f"- **False alarms (distractors reported):** {len(card.false_alarms)}"
        + (f" — {', '.join(card.false_alarms)}" if card.false_alarms else ""),
        f"- **Partial trails (multi-document findings cited incompletely):** {len(card.partial_trails)}"
        + (f" — {', '.join(card.partial_trails)}" if card.partial_trails else ""),
        "",
        "## Recall by severity",
        "",
        "| Severity | Found | Total |",
        "|---|---|---|",
    ]
    for severity in SEVERITIES:
        found, total = card.by_severity[severity]
        lines.append(f"| {severity} | {found} | {total} |")
    lines += ["", "## Per-finding result", "", "| Finding | Severity | Result |", "|---|---|---|"]
    for finding_id, severity, hit in card.hit_table:
        lines.append(f"| {finding_id} | {severity} | {'hit' if hit else 'miss'} |")
    if provisional:
        lines += [
            "",
            f"**{len(card.unadjudicated)} reported findings cited no known document and were not "
            "adjudicated.** They count against precision as reported, and could still raise "
            "recall above if adjudicated to a currently-missed finding — the recall and "
            "precision above are provisional until they are resolved, not a final score.",
        ]
    lines.append("")
    return "\n".join(lines)


def diff_scorecards(baseline: Scorecard, current: Scorecard) -> str:
    lines = ["# Scorecard diff", "", "| Metric | Baseline | Current | Change |", "|---|---|---|---|"]
    rows = [
        ("recall", baseline.recall, current.recall),
        ("precision", baseline.precision, current.precision),
    ]
    for name, before, after in rows:
        lines.append(f"| {name} | {before:.0%} | {after:.0%} | {after - before:+.0%} |")
    gained = sorted(set(baseline.misses) - set(current.misses))
    lost = sorted(set(current.misses) - set(baseline.misses))
    lines += [
        "",
        f"- **Newly found:** {', '.join(gained) or 'none'}",
        f"- **Newly missed:** {', '.join(lost) or 'none'}",
        "",
    ]
    return "\n".join(lines)
