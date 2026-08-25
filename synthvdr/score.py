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
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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
    for index, finding_id in adjudicated.items():
        if finding_id:
            matched[index] = finding_id
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
) -> str:
    lines = [f"# Scorecard — {output.tool}", ""]
    if provenance is not None:
        marker = "verified" if provenance.verified else "UNVERIFIED"
        lines += [f"- **Provenance: {marker}** — {provenance.detail}", ""]
    lines += [
        f"- **Recall:** {card.recall:.0%} ({len(findings.findings) - len(card.misses)}/{len(findings.findings)})",
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
    if card.unadjudicated:
        lines += [
            "",
            f"**{len(card.unadjudicated)} reported findings cited no known document and were not "
            "adjudicated.** They count against precision and are listed for review.",
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
