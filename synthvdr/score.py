"""Score a tool's output against the answer key.

Matching is two-stage. Stage one is deterministic: a tool that cites a
finding's source or corroboration documents is matched to that finding — to
every finding whose evidence it cites, not just one, because a single report
can legitimately evidence more than one planted finding at once and the
cited paths say so plainly. That is not ambiguous; ambiguity is reserved for
a report that cites no known document at all, which is what the adjudication
list is for. Stage two is LLM adjudication of what is left, performed by the
/vdr-score skill and passed back in as Adjudication rows, so the scoring
logic itself stays deterministic and every judgement is recorded rather than
re-derived.

Recall is the count of *distinct* findings matched by anything, over the
total findings in the key. Precision is the count of reports that matched
*at least one* finding, over the total reports — so two correct reports of
the same finding score precision 1.0 (both were right), not 0.5 (as if one
were a duplicate mistake), while still only crediting that one finding
towards recall once. A multi-document finding's partial-trail check is
computed over the *union* of documents cited by every report matched to it,
not per report — a trail split across two separate reports is still a
complete trail. This is also what keeps hit_table and partial_trails from
disagreeing about the same finding: they are now built from the same
per-finding view of the evidence, not two different per-report ones.

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
supposed to have. finding_id may be a string, null (a confirmed non-match),
or a list of strings (the same many-to-many truth prematch can express),
using the same normalisation either way.

A recall of 0.0 does not by itself mean "nothing could be matched" — it is
also what a fully-adjudicated run reports when every report was positively
confirmed to match nothing. Scorecard.unadjudicated is what tells the two
apart: non-empty means some reports are still unresolved and could still
raise recall once adjudicated, so render_scorecard marks the Recall line and
the reported findings themselves as provisional in that case; empty means
every report was resolved one way or the other, and the number is final.

A tool-output file that cannot actually be parsed — a JSON root that is not
an object, a findings list that is not a list, a finding row that is not an
object, or a markdown report with no recognisable finding headings at all —
raises ToolOutputError rather than silently degrading into a valid-looking
zero-finding run. If a tool genuinely found nothing, the JSON format's
explicit empty findings list is how it says so; an empty or prose-only
markdown file does not, because there is no way to tell that apart from a
report this parser simply failed to understand.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

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
    """One adjudicator judgement for one reported finding.

    finding_id is the COMPLETE set of findings tool_index should be
    credited with — not an addition to whatever prematch already found.
    score() always overwrites, never merges, because overwrite is the
    primitive a correction needs: `finding_id: null` (or an empty list,
    the same thing) removes a wrong pre-match, and an additive merge would
    make that unexpressible. In the normal workflow the adjudicator is
    only ever shown reports prematch could not resolve at all, so naming a
    tool_index prematch DID already resolve is, by definition, a
    correction, not an addition — score() records it as one in
    Scorecard.corrections (before -> after) precisely so that is visible
    rather than inferred, and an adjudicator who intended to add credit on
    top of an existing match sees immediately that they replaced it
    instead.
    """

    tool_index: int
    finding_id: Optional[Union[str, List[str]]]
    reason: str


@dataclass
class Scorecard:
    by_severity: Dict[str, Tuple[int, int]]
    recall: float
    precision: float
    false_alarms: List[str]
    distractor_citations: List[str]
    partial_trails: List[str]
    misses: List[str]
    hit_table: List[Tuple[str, str, bool]]
    unadjudicated: List[int]
    corrections: List[Tuple[int, List[str], List[str]]]


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


def check_provenance(room: Path, output) -> ProvenanceStatus:
    """Compare `output.room_hash` against `_key/manifest.json`'s content_hash.

    `output` is any tool output carrying a `room_hash` attribute — the
    findings ToolOutput above, or classify_score.ClassificationOutput. The
    annotation is deliberately loose: naming both types here would import
    classify_score into this module for a type hint alone.

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


def _normalize_finding_ids(finding_id: Optional[Union[str, Sequence[str]]]) -> List[str]:
    """Normalize an Adjudication.finding_id — a string, None, or a sequence
    of strings — into a sorted, de-duplicated list. This is the same shape
    prematch() produces for a pre-match, so score() can treat pre-matched
    and adjudicated report->finding links identically once normalized, and
    it is sorted here (not left as a set, and not left in citation order)
    for the same reason prematch sorts: the result must never depend on
    PYTHONHASHSEED-salted set order, or on the order ids happened to be
    listed in the YAML file.
    """
    if finding_id is None:
        ids: List[str] = []
    elif isinstance(finding_id, str):
        ids = [finding_id]
    else:
        ids = list(finding_id)
    return sorted(set(ids))


def load_adjudications(path: Path) -> List[Adjudication]:
    """Parse `_key/adjudications.yaml`.

    Raises AdjudicationError on anything that is not a well-formed list of
    {tool_index, finding_id, reason} rows. finding_id may be a string, null,
    or a list of strings — the same many-to-many truth a pre-match can
    express. A missing tool_index or reason, or a tool_index/finding_id of
    the wrong shape, is caught here; whether tool_index and finding_id
    actually resolve against a specific run is checked separately by
    validate_adjudications, since that needs the ToolOutput and FindingSet
    this file is being applied to.
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
        is_string_list = isinstance(finding_id, list) and all(isinstance(x, str) for x in finding_id)
        if finding_id is not None and not isinstance(finding_id, str) and not is_string_list:
            raise AdjudicationError(
                f"{context}: finding_id must be a string, null, or a list of strings, got {finding_id!r}"
            )
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
    findings, a finding_id (or any id inside a finding_id list) absent from
    the answer key, or two adjudications naming the same tool_index (one
    would silently override the other in score()) are all errors naming the
    offending entry — none of them may be silently dropped, because a
    dropped adjudication is a silently wrong score.
    """
    known_ids = set(findings.by_id)
    seen: Dict[int, Optional[Union[str, List[str]]]] = {}
    for adjudication in adjudications:
        if not (0 <= adjudication.tool_index < len(output.findings)):
            raise AdjudicationError(
                f"{source}: adjudication tool_index {adjudication.tool_index} is out of range — "
                f"the tool output has {len(output.findings)} finding(s) "
                f"(valid indices: 0..{len(output.findings) - 1})"
            )
        for finding_id in _normalize_finding_ids(adjudication.finding_id):
            if finding_id not in known_ids:
                raise AdjudicationError(
                    f"{source}: adjudication for tool_index {adjudication.tool_index} names "
                    f"finding_id {finding_id!r}, which does not exist in the answer key"
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


class ToolOutputError(Exception):
    """A tool-output file is not a well-formed ToolOutput — the wrong JSON
    shape (root not an object, findings not a list, a finding row not an
    object), or a markdown report with no recognisable finding headings at
    all. Always raised, never silently downgraded into a valid-looking
    zero-finding ToolOutput: a wrong conclusion drawn from a file that could
    not actually be parsed is exactly what this project's SKIP discipline
    exists to prevent. If a tool genuinely found nothing, the JSON format's
    explicit empty findings list is how it says so — an empty or
    prose-only markdown file does not.
    """


# One reported finding per level 2-4 heading. `(?!#)` bounds the hash run at
# the top as well as the bottom, and is load-bearing: `^#{2,4}` alone is
# anchored only at the start, so on a level-5 heading it matched the first
# four hashes and handed the fifth back inside the capture —
# "##### Root cause" became a finding titled "# Root cause". A level-5
# heading is sub-structure INSIDE a finding's write-up, not another finding,
# and counting it as one inflates the precision denominator (precision is
# matched / len(output.findings)), so a tool lost precision for nothing but
# its own choice of heading depth. With the lookahead, `#{2,4}` cannot settle
# on a shorter run to dodge it — 4 hashes, then 3, then 2 all fail against a
# following '#' — so a 5-or-deeper heading matches nothing at all, which is
# what it should be. Level 1 was already excluded: that is the report title.
#
# The separator stays `\s*`, deliberately NOT the `[ \t]+` that
# synthvdr.render.docx._ATX_HEADING requires. That constant parses THIS
# project's own generated markdown, where CommonMark conformance is the whole
# point; this one parses a report written by an arbitrary third-party tool
# under test, where refusing to read "##Finding 1" would be scoring a tool
# down for its markdown style rather than its findings. Different inputs,
# different contracts — do not reconcile them.
_MD_FINDING = re.compile(r"^#{2,4}(?!#)\s*(.+)$", re.MULTILINE)
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
    if not findings:
        raise ToolOutputError(
            "could not parse any findings from this markdown report — expected one or more "
            "level 2-4 headings ('##', '###', or '####'), one per finding. If the tool "
            "genuinely reported zero findings, use the JSON format with an explicit empty "
            "'findings' list instead of an empty or prose-only markdown file."
        )
    return ToolOutput(tool=tool, room_hash="", findings=findings)


def load_tool_output(path: Path) -> ToolOutput:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        doc = json.loads(text)
        if not isinstance(doc, dict):
            raise ToolOutputError(
                f"{path}: the JSON root must be an object with 'tool' and 'findings' keys — "
                f"got {type(doc).__name__}"
            )
        raw_findings = doc.get("findings")
        if raw_findings is None:
            raw_findings = []
        if not isinstance(raw_findings, list):
            raise ToolOutputError(
                f"{path}: 'findings' must be a list, got {type(raw_findings).__name__}"
            )
        findings: List[ToolFinding] = []
        for index, row in enumerate(raw_findings):
            if not isinstance(row, dict):
                raise ToolOutputError(
                    f"{path}: findings[{index}] must be an object, got {type(row).__name__}"
                )
            findings.append(
                ToolFinding(
                    title=row.get("title", ""),
                    severity=row.get("severity", "medium"),
                    documents=list(row.get("documents") or []),
                    summary=row.get("summary", ""),
                )
            )
        return ToolOutput(
            tool=doc.get("tool", "unknown"),
            room_hash=doc.get("room_hash", ""),
            findings=findings,
        )
    return parse_markdown_report(text, tool=path.stem)


def prematch(output: ToolOutput, findings: FindingSet) -> Tuple[Dict[int, List[str]], List[int]]:
    """Match each reported finding against every planted finding whose
    source or corroboration document it cites — not just one. A report
    citing the source documents of two findings is not ambiguous: the
    cited paths say plainly that it matches both, so both are credited.
    Ambiguity is reserved for a report that cites no known document at
    all, which lands in the second return value for adjudication.

    Returns ({tool_index: sorted list of matched finding ids}, [indices
    citing no known document]). Each match list is built from a set and
    sorted before being returned, so which findings a report is credited
    with never depends on the order it happened to list its citations, or
    on PYTHONHASHSEED-salted set iteration order.
    """
    owners: Dict[str, Set[str]] = {}
    for finding in findings.findings:
        for path in finding.evidence_paths():
            owners.setdefault(path, set()).add(finding.id)

    matched: Dict[int, List[str]] = {}
    unmatched: List[int] = []
    for index, reported in enumerate(output.findings):
        hit_ids: Set[str] = set()
        for document in reported.documents:
            hit_ids.update(owners.get(document, ()))
        if hit_ids:
            matched[index] = sorted(hit_ids)
        else:
            unmatched.append(index)
    return matched, unmatched


def score(
    output: ToolOutput,
    findings: FindingSet,
    distractors: Sequence[Distractor],
    adjudications: Sequence[Adjudication] = (),
) -> Scorecard:
    matched, unmatched = prematch(output, findings)
    adjudicated = {a.tool_index: _normalize_finding_ids(a.finding_id) for a in adjudications}
    # Adjudications take precedence over the deterministic pre-match where
    # both apply — in either direction. A non-empty id list assigns or
    # reassigns the match for that index; an empty one (from finding_id
    # None, or an explicit empty list) is a positive confirmation that the
    # index matches nothing, which must be able to remove a pre-match too,
    # not just decline to add one. In the normal workflow the adjudicator is
    # only ever shown reports prematch left unmatched, so an adjudication
    # naming an index prematch already resolved is, by definition, a
    # correction — recorded here (before -> after) so overwriting a
    # pre-match is always visible on the scorecard, never silent.
    corrections: List[Tuple[int, List[str], List[str]]] = []
    for index, ids in adjudicated.items():
        before = matched.get(index)
        if before is not None:
            corrections.append((index, before, ids))
        if ids:
            matched[index] = ids
        else:
            matched.pop(index, None)
    still_unmatched = [i for i in unmatched if i not in adjudicated]
    corrections.sort(key=lambda c: c[0])

    # A distractor cited inside an otherwise-matched report is not a false
    # alarm — the report also cites real evidence, so the tool made a
    # genuine find — but it should not vanish either, since it shows the
    # trap partly worked. false_alarms keeps its existing meaning (a
    # distractor cited by a report matching nothing); distractor_citations
    # is the separate, explicitly-labelled record of the bundled case, so
    # precision and false_alarms are never corrupted by it while the fact
    # is still visible on the scorecard.
    #
    # EVERY distractor a report cites counts, not just the first one found.
    # This used to short-circuit on `next(...)`, which meant a single report
    # citing two traps was scored as having fallen for one: the second id was
    # dropped before the de-duplication below ever saw it, so the headline
    # "false alarms" figure under-reported by exactly the number of extra
    # traps in the report. Measuring false alarms is a stated reason the
    # distractors exist at all, and a tool that takes two baits in one breath
    # is not the same result as one that takes a single bait.
    distractor_docs = {d.location: d.id for d in distractors}
    false_alarms: List[str] = []
    distractor_citations: List[str] = []
    for index, reported in enumerate(output.findings):
        cited_ids = {distractor_docs[d] for d in reported.documents if d in distractor_docs}
        if not cited_ids:
            continue
        # Sorted before extending so the per-report contribution does not
        # depend on PYTHONHASHSEED-salted set order, the same discipline
        # prematch() applies to its own match lists.
        if index in matched:
            distractor_citations.extend(sorted(cited_ids))
        else:
            false_alarms.extend(sorted(cited_ids))

    # Recall counts distinct findings matched by anything, across every
    # report — a finding hit by two reports is still one finding found.
    found_ids: Set[str] = set()
    for ids in matched.values():
        found_ids.update(ids)

    by_severity: Dict[str, Tuple[int, int]] = {}
    for severity in SEVERITIES:
        pool = [f for f in findings.findings if f.severity == severity]
        by_severity[severity] = (len([f for f in pool if f.id in found_ids]), len(pool))

    total = len(findings.findings)
    recall = len(found_ids) / total if total else 0.0

    # Precision counts reports that matched at least one finding — matched
    # only ever holds a report index with a non-empty id list, so its
    # length is exactly that count. Two correct reports of the same
    # finding are both right (precision 1.0), not one right and one wrong.
    reported_count = len(output.findings)
    precision = len(matched) / reported_count if reported_count else 0.0

    # A multi-document finding's partial-trail check is computed over the
    # UNION of documents cited by every report matched to it, not per
    # report — a trail split across two reports (one citing the source,
    # the other the corroboration) is a complete trail, not two partial
    # ones, and this is what keeps hit_table and partial_trails agreeing.
    trail_docs: Dict[str, Set[str]] = {}
    for index, ids in matched.items():
        cited = set(output.findings[index].documents)
        for finding_id in ids:
            trail_docs.setdefault(finding_id, set()).update(cited)

    partial: List[str] = []
    for finding_id, cited_union in trail_docs.items():
        finding = findings.by_id[finding_id]
        if finding.multi_document and not set(finding.evidence_paths()) <= cited_union:
            partial.append(finding_id)

    return Scorecard(
        by_severity=by_severity,
        recall=recall,
        precision=precision,
        false_alarms=sorted(set(false_alarms)),
        distractor_citations=sorted(set(distractor_citations)),
        partial_trails=sorted(set(partial)),
        misses=sorted(f.id for f in findings.findings if f.id not in found_ids),
        hit_table=[(f.id, f.severity, f.id in found_ids) for f in findings.findings],
        unadjudicated=still_unmatched,
        corrections=corrections,
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
        f"- **Distractor citations inside otherwise-matched reports:** {len(card.distractor_citations)}"
        + (f" — {', '.join(card.distractor_citations)}" if card.distractor_citations else "")
        + (
            " (a genuine find that also fell for part of a trap — not a false alarm, "
            "since the report matched a real finding, but the trap partly worked)"
            if card.distractor_citations
            else ""
        ),
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
    if card.corrections:
        lines += [
            "",
            "## Adjudications that overrode a pre-match",
            "",
            "The adjudicator is only ever shown reports the deterministic pre-match left "
            "unmatched, so each row below named an index pre-match had already resolved — "
            "these are corrections, not additions, and replace rather than add to the "
            "pre-match credit shown in \"before\".",
            "",
            "| Tool index | Pre-match (before) | Adjudicated to (after) |",
            "|---|---|---|",
        ]
        for index, before, after in card.corrections:
            before_text = ", ".join(before) if before else "—"
            after_text = ", ".join(after) if after else "(none — removed)"
            lines.append(f"| {index} | {before_text} | {after_text} |")
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
