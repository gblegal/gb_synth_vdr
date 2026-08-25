"""The answer-key model: findings and distractors.

YAML is canonical; findings.md is generated from it. Nothing else parses the key.
"""

from __future__ import annotations

import re
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


def allocate_new_finding_ids(
    existing_ids,
    prefix_for_workstream,
    discoveries,
):
    """Deterministically assign real finding IDs to findings discovered mid-authoring.

    Gate B fixes IDs and severities for everything known at that point (`## 5.1` of the
    design spec). A finding an author genuinely discovers while writing a document is
    appended with "the next free number in the owning workstream" — but `/vdr-build` fans a
    wave out across several `vdr-author` subagents running in parallel, with no channel
    between them, so "the next free number" cannot be something each one picks for itself:
    two authors racing for the same workstream would silently collide on one number for two
    distinct issues, which is exactly what the "one distinct issue is one finding ID" rule
    exists to prevent. So allocation is split from discovery: an author only ever proposes a
    *provisional* id scoped to its own wave-and-batch label (`<label>-NEW-1`, `<label>-NEW-2`,
    ...) and never writes a real finding id. This function is the single place, run once by
    `/vdr-build`'s consolidation step after a wave completes, that turns those proposals into
    real ids.

    `discoveries` is an iterable of `(label, provisional_id, workstream)` triples — one per
    `new_findings` row across every `_key/incoming/*.yaml` file the wave produced.
    `prefix_for_workstream` is the workstream -> finding-id-prefix mapping declared in
    `room.conf`'s `FINDING_PREFIXES` (this module has no opinion on room.conf, so the caller
    resolves it once and passes it in). Raises `SchemaError` naming any workstream with no
    entry there, rather than silently inventing a prefix.

    Returns `{provisional_id: final_id}`.

    Determinism is why allocation is sorted rather than processed in whatever order the
    incoming files were read: two runs over the *same* intake — the same set of discoveries —
    must produce the same ids, and dict/glob/filesystem order is not guaranteed stable across
    a rerun, a different machine, or a different Python version. Sorting by
    `(label, provisional_id)` before allocating is what makes the numbering reproducible
    regardless of which subagent happened to finish first or which order its file was read in;
    it also makes the allocation reviewable, since the mapping the wave manifest declares is
    exactly the order this function will always produce for that intake.
    """
    discoveries = list(discoveries)
    unknown = sorted({w for _, _, w in discoveries if w not in prefix_for_workstream})
    if unknown:
        raise SchemaError(
            f"no FINDING_PREFIXES entry for workstream(s): {', '.join(unknown)}"
        )

    next_number: Dict[str, int] = {}
    for fid in existing_ids:
        prefix, _, number = fid.rpartition("-")
        if prefix and number.isdigit():
            next_number[prefix] = max(next_number.get(prefix, 0), int(number))

    mapping: Dict[str, str] = {}
    for label, provisional_id, workstream in sorted(discoveries):
        prefix = prefix_for_workstream[workstream]
        next_number[prefix] = next_number.get(prefix, 0) + 1
        mapping[provisional_id] = f"{prefix}-{next_number[prefix]}"
    return mapping


def derive_prefix_for_workstream(workstreams, prefixes, existing_findings=()):
    """Build and validate the workstream -> finding-id-prefix mapping `allocate_new_finding_ids`
    needs, from `room.conf`'s `FINDING_PREFIXES` token list and the domain pack's declared
    workstream order.

    `FINDING_PREFIXES` carries no explicit workstream labels — `/vdr-scope` declares it as
    "one token per workstream in the domain pack," so the correspondence between the two lists
    is positional by convention, never enforced by any format. A hand-edited room.conf, or a
    domain pack whose workstream order later changes, can silently shift that correspondence:
    same length, wrong pairing, and a bare `zip()` would never notice — a newly discovered
    finding would then be numbered under the WRONG workstream's prefix with no error at all,
    which is worse than the loud failure a merely-short list already gives.

    This checks the one thing it CAN check without a separately stored mapping: every
    workstream that already has at least one existing finding has a KNOWN prefix, read
    straight off that finding's own id (Gate B already fixed it, and it necessarily agrees
    with the room's real FINDING_PREFIXES because the finding was authored against it) — so
    the zip's answer for that workstream must agree. A workstream with no existing finding yet
    cannot be cross-checked this way; only the list length is checked for those.

    Raises `SchemaError`, naming the exact mismatch, on a length disagreement or on any
    already-established workstream whose known prefix disagrees with the zip — never silently
    returning a mapping that could misattribute a new finding's workstream.
    """
    workstreams = list(workstreams)
    prefixes = list(prefixes)
    if len(workstreams) != len(prefixes):
        raise SchemaError(
            f"FINDING_PREFIXES has {len(prefixes)} token(s) but the domain pack declares "
            f"{len(workstreams)} workstream(s) — they must correspond one-to-one, in order"
        )
    mapping = dict(zip(workstreams, prefixes))

    known_prefix_for_workstream: Dict[str, str] = {}
    for finding in existing_findings:
        prefix, _, number = finding.id.rpartition("-")
        if prefix and number.isdigit():
            known_prefix_for_workstream.setdefault(finding.workstream, prefix)

    for workstream, known_prefix in known_prefix_for_workstream.items():
        if workstream not in mapping:
            raise SchemaError(
                f"FINDING_PREFIXES has no entry for workstream {workstream!r}, but an "
                f"existing finding already uses prefix {known_prefix!r} for it"
            )
        if mapping[workstream] != known_prefix:
            raise SchemaError(
                f"FINDING_PREFIXES appears reordered: workstream {workstream!r} already has "
                f"finding(s) prefixed {known_prefix!r}, but the declared order maps it to "
                f"{mapping[workstream]!r} instead — check FINDING_PREFIXES against the domain "
                "pack's workstream order"
            )
    return mapping


NEW_FINDING_LEDGER_ROW = re.compile(
    r"^\|\s*([\w-]+)\s*\|\s*([A-Z]+-\d+)\s*\|\s*(\w+)\s*\|\s*$", re.MULTILINE
)


def parse_new_findings_ledger(build_status_text: str) -> Dict[str, str]:
    """`{provisional_id: final_id}` already recorded in `_key/build-status.md`'s
    "New findings" table.

    This is the durable idempotency record `consolidate_wave_incoming` reads before
    allocating: a provisional id already present here was allocated in an earlier attempt at
    this build (possibly one whose gate later failed) and must never be allocated again, or a
    resumed build silently doubles every mid-authoring discovery under a second, higher id —
    the exact defect `/vdr-build`'s own resumability guarantee exists to rule out. An absent
    or empty ledger (a fresh build, or one with no discoveries yet) parses to `{}`.
    """
    return {
        provisional: final
        for provisional, final, _workstream in NEW_FINDING_LEDGER_ROW.findall(build_status_text)
    }


@dataclass(frozen=True)
class ConsolidationResult:
    """Result of one `consolidate_wave_incoming` call."""

    findings_doc: dict
    new_mapping: Dict[str, str]
    workstream_by_final_id: Dict[str, str]


def consolidate_wave_incoming(
    findings_doc: dict,
    incoming_docs: Dict[str, dict],
    already_mapped: Dict[str, str],
    prefix_for_workstream: Dict[str, str],
) -> ConsolidationResult:
    """Merge a wave's `_key/incoming/*.yaml` files into `_key/findings.yaml`'s parsed
    document, and allocate real ids for any genuinely new discovery.

    Pure and file-I/O-free by design, so it can be called twice over the same inputs and
    checked for exactly the property `/vdr-build`'s resumability depends on: calling it again
    with `already_mapped` updated from the first call's `new_mapping` must allocate NOTHING
    new and must leave `findings_doc` unchanged, because every provisional id it would
    otherwise see is already in `already_mapped` and is skipped rather than re-allocated.
    Skipping, not re-deriving state some other way, is what makes a rerun over an UNTOUCHED
    intake safe — `/vdr-build` can call this after every wave attempt, gate pass or gate fail,
    with no risk of duplicating a discovery that a failed gate left sitting in the incoming
    directory.

    `incoming_docs` maps each incoming file's label (its filename stem) to its parsed YAML
    document. `findings_doc` is `_key/findings.yaml`'s parsed document (with a `findings` key,
    a list of row mappings — the same shape `synthvdr.schema.load_findings` reads). A
    `findings:` row inside an incoming doc upserts onto the matching existing row by id, and
    raises `SchemaError` if that id is not already in `findings_doc` (Gate B's registry is
    closed; consolidation never introduces a new id under that key). A `new_findings:` row
    proposes a discovery under a provisional id; unless that id is already in `already_mapped`,
    it is passed to `allocate_new_finding_ids` (sorted there by `(label, provisional_id)`, so
    the allocation itself is deterministic across reruns too) and the resulting row is added
    under its real, newly allocated id.
    """
    by_id = {row["id"]: dict(row) for row in (findings_doc.get("findings") or [])}

    discoveries = []
    new_rows_by_provisional: Dict[str, dict] = {}
    for label, incoming in sorted(incoming_docs.items()):
        for row in incoming.get("findings") or []:
            if row["id"] not in by_id:
                raise SchemaError(
                    f"{label}: finding {row['id']!r} is not in the Gate B registry — "
                    "consolidation only refines an existing finding, it never adds one "
                    "under the `findings:` key"
                )
            by_id[row["id"]].update(row)
        for row in incoming.get("new_findings") or []:
            if row["id"] in already_mapped:
                continue
            discoveries.append((label, row["id"], row["workstream"]))
            new_rows_by_provisional[row["id"]] = row

    new_mapping = allocate_new_finding_ids(set(by_id), prefix_for_workstream, discoveries)
    for provisional_id, final_id in new_mapping.items():
        row = dict(new_rows_by_provisional[provisional_id])
        row["id"] = final_id
        by_id[final_id] = row

    updated_doc = dict(findings_doc)
    updated_doc["findings"] = list(by_id.values())
    workstream_by_final_id = {
        final_id: new_rows_by_provisional[provisional_id]["workstream"]
        for provisional_id, final_id in new_mapping.items()
    }
    return ConsolidationResult(updated_doc, new_mapping, workstream_by_final_id)
