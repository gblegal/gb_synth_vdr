"""The answer-key model: findings and distractors.

YAML is canonical; findings.md is generated from it. Nothing else parses the key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

SEVERITIES = ("critical", "high", "medium", "low")


class SchemaError(Exception):
    """The answer key is malformed."""


@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    severity: str
    workstream: str
    multi_document: bool
    source: str
    location: str
    substance: str
    corroboration: List[str] = field(default_factory=list)
    cross_links: List[str] = field(default_factory=list)
    discoverable_from_blind: Optional[bool] = None
    audit_note: str = ""

    def evidence_paths(self) -> List[str]:
        return [self.source, *self.corroboration]


@dataclass(frozen=True)
class Distractor:
    id: str
    title: str
    location: str
    resolution: str
    shape_matches: Optional[str] = None


@dataclass(frozen=True)
class FindingSet:
    findings: List[Finding]
    room: str

    @property
    def by_id(self) -> Dict[str, Finding]:
        return {f.id: f for f in self.findings}

    def all_evidence_paths(self) -> Set[str]:
        return {p for f in self.findings for p in f.evidence_paths()}

    def carrier_paths(self) -> Set[str]:
        """Documents that receive an annotation block in the flagged tree."""
        return self.all_evidence_paths()


def _require(row: dict, key: str, context: str):
    if key not in row:
        raise SchemaError(f"{context}: missing required field {key!r}")
    return row[key]


def _load_yaml(path: Path) -> dict:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SchemaError(f"{path}: not valid YAML — {exc}") from exc
    return doc or {}


def load_findings(path: Path) -> FindingSet:
    doc = _load_yaml(path)
    rows = doc.get("findings") or []
    findings: List[Finding] = []
    seen: Set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SchemaError(
                f"{path}: findings[{index}] is not a mapping — got {type(row).__name__}"
            )
        fid = _require(row, "id", str(path))
        if fid in seen:
            raise SchemaError(f"{path}: duplicate finding id {fid}")
        seen.add(fid)
        severity = _require(row, "severity", f"{path}:{fid}")
        if severity not in SEVERITIES:
            raise SchemaError(
                f"{path}:{fid}: unknown severity {severity!r}; expected one of {SEVERITIES}"
            )
        findings.append(
            Finding(
                id=fid,
                title=_require(row, "title", f"{path}:{fid}"),
                severity=severity,
                workstream=_require(row, "workstream", f"{path}:{fid}"),
                multi_document=bool(row.get("multi_document", False)),
                source=_require(row, "source", f"{path}:{fid}"),
                location=row.get("location", ""),
                substance=_require(row, "substance", f"{path}:{fid}"),
                corroboration=list(row.get("corroboration") or []),
                cross_links=list(row.get("cross_links") or []),
                discoverable_from_blind=row.get("discoverable_from_blind"),
                audit_note=row.get("audit_note", ""),
            )
        )
    return FindingSet(findings=findings, room=doc.get("room", ""))


def load_distractors(path: Path) -> List[Distractor]:
    doc = _load_yaml(path)
    rows = doc.get("distractors") or []
    out: List[Distractor] = []
    seen: Set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SchemaError(
                f"{path}: distractors[{index}] is not a mapping — got {type(row).__name__}"
            )
        did = _require(row, "id", str(path))
        if did in seen:
            raise SchemaError(f"{path}: duplicate distractor id {did}")
        seen.add(did)
        out.append(
            Distractor(
                id=did,
                title=_require(row, "title", f"{path}:{did}"),
                location=_require(row, "location", f"{path}:{did}"),
                resolution=_require(row, "resolution", f"{path}:{did}"),
                shape_matches=row.get("shape_matches"),
            )
        )
    return out


def _path_errors(path: str, owner: str, field: str) -> List[str]:
    """Path hygiene: every stored path must be non-empty, relative to the
    room root, and free of parent-directory traversal.
    """
    if not path:
        return [f"{owner}: {field} is empty — every path must be relative and non-empty"]
    errors: List[str] = []
    if path.startswith("/"):
        errors.append(
            f"{owner}: {field} {path!r} is an absolute path — "
            "paths must be relative to the room root"
        )
    if ".." in path.split("/"):
        errors.append(
            f"{owner}: {field} {path!r} contains a '..' segment — "
            "paths must stay inside the room root"
        )
    return errors


def validate(findings: FindingSet, distractors: List[Distractor]) -> List[str]:
    errors: List[str] = []
    known = set(findings.by_id)

    # Map every evidence path to the finding(s) it belongs to, so a
    # distractor colliding with real evidence can be reported by id.
    evidence_owners: Dict[str, List[str]] = {}
    for finding in findings.findings:
        for path in finding.evidence_paths():
            evidence_owners.setdefault(path, []).append(finding.id)

    for finding in findings.findings:
        for link in finding.cross_links:
            if link not in known:
                errors.append(f"{finding.id}: cross_link {link} does not exist")
            if link == finding.id:
                errors.append(f"{finding.id}: cross_link points at itself")
        if finding.multi_document and not finding.corroboration:
            errors.append(
                f"{finding.id}: multi_document is true but corroboration is empty — "
                "a multi-document finding must name the other documents in its trail"
            )
        if not finding.multi_document and finding.corroboration:
            errors.append(
                f"{finding.id}: corroboration is set but multi_document is false"
            )

        errors.extend(_path_errors(finding.source, finding.id, "source"))
        for path in finding.corroboration:
            errors.extend(_path_errors(path, finding.id, "corroboration entry"))
        if finding.source and finding.source in finding.corroboration:
            errors.append(
                f"{finding.id}: corroboration re-lists its own source {finding.source!r} — "
                "corroboration must name other documents, not the source itself"
            )
        if len(finding.corroboration) != len(set(finding.corroboration)):
            dupes = sorted(
                {p for p in finding.corroboration if finding.corroboration.count(p) > 1}
            )
            errors.append(
                f"{finding.id}: corroboration contains duplicate path(s) {dupes}"
            )

    for distractor in distractors:
        if distractor.resolution == distractor.location:
            errors.append(
                f"{distractor.id}: resolution is the same document as location — "
                "a distractor's resolving evidence must live elsewhere"
            )
        if distractor.shape_matches and distractor.shape_matches not in known:
            errors.append(
                f"{distractor.id}: shape_matches {distractor.shape_matches} does not exist"
            )

        errors.extend(_path_errors(distractor.location, distractor.id, "location"))
        errors.extend(_path_errors(distractor.resolution, distractor.id, "resolution"))

        if distractor.location in evidence_owners:
            owners = ", ".join(evidence_owners[distractor.location])
            errors.append(
                f"{distractor.id}: location {distractor.location!r} is evidence for "
                f"finding(s) {owners} — a distractor's alarming document must not "
                "double as real evidence"
            )
        if distractor.resolution in evidence_owners:
            owners = ", ".join(evidence_owners[distractor.resolution])
            errors.append(
                f"{distractor.id}: resolution {distractor.resolution!r} is evidence for "
                f"finding(s) {owners} — a distractor's resolving document must be "
                "genuinely benign, not itself planted evidence"
            )
    return errors


def render_findings_md(findings: FindingSet, room_codename: str) -> str:
    lines = [
        f"# {room_codename} — answer key",
        "",
        "**Generated from `findings.yaml`. Do not hand-edit; edit the YAML and regenerate.**",
        "",
        "**Answer-key material. Never fed to a tool under test.**",
        "",
    ]
    for finding in sorted(findings.findings, key=lambda f: (SEVERITIES.index(f.severity), f.id)):
        lines += [
            f"## {finding.id} — {finding.title}",
            "",
            f"- **Severity:** {finding.severity}",
            f"- **Workstream:** {finding.workstream}",
            f"- **Source document:** `{finding.source}`",
            f"- **Location:** {finding.location or '—'}",
            "- **Corroboration:** "
            + (", ".join(f"`{p}`" for p in finding.corroboration) or "—"),
            f"- **Substance:** {finding.substance.strip()}",
            "- **Cross-links:** " + (", ".join(finding.cross_links) or "—"),
            "- **Discoverable from blind room:** "
            + {True: "yes", False: "NO — corpus bug", None: "not yet audited"}[
                finding.discoverable_from_blind
            ],
            "",
        ]
    return "\n".join(lines)
