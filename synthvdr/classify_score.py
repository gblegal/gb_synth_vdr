"""Score a tool's classification output against `_key/answer-key.jsonl`.

The room's classification answer key records facts: what each document is
(`document_type`, from the labels the authors wrote at authoring time) and
which of the room's own sections — hence which classifier workstream — it
belongs to. This module grades any classification tool against those
facts: document type, primary pile, the size of the not-sure pile, and a
confusion table saying where the misfiled documents went.

What it deliberately does NOT score: secondary deliveries. The key's
`secondary_workstreams` is empty by design — who ELSE should see a
document is the downstream project's routing policy, not a fact about the
room (see `answer_key.py`) — so counting a tool's secondaries as noise
here would penalise every policy-following classifier for its own routing
table, and crediting them would require a routing table this repo does
not own. The classifier's own eval (gb-docclass `evaluate.py`, with
`--secondaries-from-routing`) owns that question; the scorecard says so
out loud rather than leaving a suspiciously absent column to be guessed
at. For the same reason `sent_to_all_workstreams` does not make a wrong
primary right: primary-pile recall is about the pile a reviewer opens
first, and a copy sent to everyone is delivery, which is the downstream
eval's question.

Two input shapes, mirroring `load_tool_output`'s JSON-or-markdown
leniency one register down:

  - a `.json` object pinned by `schemas/classification-output.schema.json`
    — `{"tool": ..., "room_hash": ..., "classifications": [...]}`, with
    `room_hash` carried for the same provenance check the findings
    scorer runs;
  - anything else is read as JSONL, one record per line — which is
    exactly a gb-docclass manifest, so that file scores as-is. Extra
    fields on a record are ignored, tool is taken from the filename, and
    with no `room_hash` the scorecard reports UNVERIFIED rather than
    assuming a match.

A zero-record file is an error in both shapes, never a zero-document run:
a tool that classified nothing and a file that failed to say anything are
different claims, and only the JSON shape can make the first one
(`"classifications": []` is still refused here — an empty room cannot be
scored — but it fails as "empty", not as "unparseable").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .answer_key import ANSWER_KEY_NAME

_SHOW = 10
_CONFUSION_SHOW = 15


class ClassificationOutputError(Exception):
    """The output file is not a well-formed classification output."""


class ClassificationScoreError(Exception):
    """The output and the answer key cannot be scored against each other."""


def _named(paths: List[str], limit: int = _SHOW) -> str:
    shown = ", ".join(paths[:limit])
    remaining = len(paths) - limit
    if remaining > 0:
        shown += f" (+{remaining} more)"
    return shown


@dataclass(frozen=True)
class ClassificationRecord:
    source_path: str
    document_type: Optional[str] = None
    primary_workstream: Optional[str] = None
    secondary_workstreams: List[str] = field(default_factory=list)
    sent_to_all_workstreams: bool = False
    unsure: bool = False

    @classmethod
    def from_row(cls, row: dict) -> "ClassificationRecord":
        return cls(
            source_path=row["source_path"],
            document_type=row.get("document_type"),
            primary_workstream=row.get("primary_workstream"),
            secondary_workstreams=list(row.get("secondary_workstreams") or []),
            sent_to_all_workstreams=bool(row.get("sent_to_all_workstreams")),
            unsure=bool(row.get("unsure")),
        )


@dataclass(frozen=True)
class ClassificationOutput:
    tool: str
    room_hash: str
    records: List[ClassificationRecord]


@dataclass
class ClassificationScorecard:
    documents: int
    type_correct: int
    primary_correct: int
    unsure: int
    # workstream -> {"relevant": n, "filed": n, "recall": x|None, "precision": x|None}
    workstreams: Dict[str, dict]
    # [expected_workstream, predicted_workstream_or_None, count], most-common first
    confusion: List[list]


def _records_from_rows(rows, where: str) -> List[ClassificationRecord]:
    records: List[ClassificationRecord] = []
    seen: Dict[str, int] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ClassificationOutputError(
                f"{where}: record {index} must be an object, got {type(row).__name__}"
            )
        path = row.get("source_path")
        if not path or not isinstance(path, str):
            raise ClassificationOutputError(
                f"{where}: record {index} has no source_path — every "
                "classification must say which document it is about"
            )
        if path in seen:
            raise ClassificationOutputError(
                f"{where}: {path} is classified twice (records {seen[path]} "
                f"and {index}) — which copy wins would be luck, not intent; "
                "remove one"
            )
        seen[path] = index
        records.append(ClassificationRecord.from_row(row))
    return records


def load_classification_output(path: Path) -> ClassificationOutput:
    """Read a classification output — pinned JSON or lenient JSONL.

    Suffix decides, the same way `load_tool_output` splits JSON from
    markdown: `.json` is the pinned wrapper object, anything else is
    JSONL with one record per line. A gb-docclass manifest is JSONL and
    loads as-is; its extra per-record fields are ignored.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        doc = json.loads(text)
        if not isinstance(doc, dict):
            raise ClassificationOutputError(
                f"{path}: the JSON root must be an object with 'tool' and "
                f"'classifications' keys — got {type(doc).__name__}"
            )
        rows = doc.get("classifications")
        if not isinstance(rows, list):
            raise ClassificationOutputError(
                f"{path}: 'classifications' must be a list, got "
                f"{type(rows).__name__}"
            )
        if not rows:
            raise ClassificationOutputError(
                f"{path}: 'classifications' is empty — an empty room cannot "
                "be scored, and a tool that skipped every document should "
                "say so with per-document unsure records, not silence"
            )
        return ClassificationOutput(
            tool=doc.get("tool", "unknown"),
            room_hash=doc.get("room_hash", ""),
            records=_records_from_rows(rows, str(path)),
        )

    rows = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ClassificationOutputError(
                f"{path}: line {n} is not valid JSON ({exc}) — JSONL input "
                "needs one classification object per line"
            ) from exc
    if not rows:
        raise ClassificationOutputError(
            f"{path}: no records — an empty file is unparseable, not a "
            "zero-document run; use the JSON format if the tool has "
            "something explicit to say"
        )
    return ClassificationOutput(
        tool=path.stem, room_hash="", records=_records_from_rows(rows, str(path))
    )


def load_classification_key(room: Path, conf) -> List[ClassificationRecord]:
    """The room's classification answer key, or a refusal naming the fix."""
    key_path = room / conf.get_relative_path("KEY_ROOT") / ANSWER_KEY_NAME
    if not key_path.is_file():
        raise ClassificationScoreError(
            f"no {ANSWER_KEY_NAME} at {key_path} — run "
            "python3 -m synthvdr answerkey to build it from _key/labels.yaml "
            "(gate 19 requires it before an eval room packages)"
        )
    rows = []
    for n, line in enumerate(key_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ClassificationScoreError(
                f"{key_path}: line {n} is not valid JSON ({exc}) — rewrite "
                "the key with python3 -m synthvdr answerkey"
            ) from exc
    return _records_from_rows(rows, str(key_path))


def score_classification(
    output: ClassificationOutput, key: List[ClassificationRecord]
) -> ClassificationScorecard:
    """Grade the output against the key. Coverage must match exactly.

    A document the output skips would be graded against silence, and a
    classification of a document the key does not know is a path typo or
    a stale key — either way the score would be quietly wrong, so both
    directions refuse loudly instead, naming paths.
    """
    key_by_path = {record.source_path: record for record in key}
    out_by_path = {record.source_path: record for record in output.records}
    missing = sorted(set(key_by_path) - set(out_by_path))
    extra = sorted(set(out_by_path) - set(key_by_path))
    if missing or extra:
        parts = []
        if missing:
            parts.append(
                f"{len(missing)} key document(s) the output never classified "
                "(a skipped document would be graded against silence): "
                + _named(missing)
            )
        if extra:
            parts.append(
                f"{len(extra)} classified path(s) the key does not know "
                "(a path typo, or a stale key): " + _named(extra)
            )
        raise ClassificationScoreError(
            "the output and the answer key do not cover the same documents — "
            + "; ".join(parts)
        )

    workstream_ids = sorted(
        {r.primary_workstream for r in key if r.primary_workstream}
    )
    per_ws = {ws: {"relevant": 0, "filed": 0, "hit": 0} for ws in workstream_ids}
    type_correct = primary_correct = unsure = 0
    confusion: Dict[tuple, int] = {}

    for path, truth in key_by_path.items():
        record = out_by_path[path]
        predicted = None if record.unsure else record.primary_workstream
        if record.document_type == truth.document_type:
            type_correct += 1
        if record.unsure:
            unsure += 1
        if predicted == truth.primary_workstream:
            primary_correct += 1
        else:
            confusion[(truth.primary_workstream, predicted)] = (
                confusion.get((truth.primary_workstream, predicted), 0) + 1
            )
        expected = truth.primary_workstream
        if expected in per_ws:
            per_ws[expected]["relevant"] += 1
            per_ws[expected]["hit"] += predicted == expected
        if predicted in per_ws:
            per_ws[predicted]["filed"] += 1

    workstreams = {}
    for ws, stats in per_ws.items():
        workstreams[ws] = {
            "relevant": stats["relevant"],
            "filed": stats["filed"],
            "recall": (
                round(stats["hit"] / stats["relevant"], 4)
                if stats["relevant"]
                else None
            ),
            "precision": (
                round(stats["hit"] / stats["filed"], 4) if stats["filed"] else None
            ),
        }

    return ClassificationScorecard(
        documents=len(key),
        type_correct=type_correct,
        primary_correct=primary_correct,
        unsure=unsure,
        workstreams=workstreams,
        confusion=sorted(
            ([expected, predicted, count]
             for (expected, predicted), count in confusion.items()),
            key=lambda row: (-row[2], row[0] or "", row[1] or ""),
        ),
    )


def render_classification_scorecard(
    card: ClassificationScorecard,
    output: ClassificationOutput,
    provenance=None,
) -> str:
    def pct(part: int) -> str:
        return f"{part / card.documents:.0%}" if card.documents else "-"

    lines = [f"# Classification scorecard — {output.tool}", ""]
    if provenance is not None:
        marker = "verified" if provenance.verified else "UNVERIFIED"
        lines += [f"- **Provenance: {marker}** — {provenance.detail}", ""]
    lines += [
        f"- **Documents:** {card.documents}",
        f"- **Document type right:** {pct(card.type_correct)} "
        f"({card.type_correct}/{card.documents})",
        f"- **Primary pile right:** {pct(card.primary_correct)} "
        f"({card.primary_correct}/{card.documents})",
        f"- **Not sure:** {card.unsure} ({pct(card.unsure)})",
        "",
        "Who else should see a document is routing policy, not a fact "
        "about the room — the key's secondaries are empty by design, so "
        "secondary deliveries are not scored here; the classifier's own "
        "eval owns that question.",
        "",
        "## Right pile, by workstream",
        "",
        "| Workstream | In the key | Recall | Filed there | Precision |",
        "|---|---|---|---|---|",
    ]
    for ws, stats in sorted(card.workstreams.items()):
        recall = "-" if stats["recall"] is None else f"{stats['recall']:.0%}"
        precision = (
            "-" if stats["precision"] is None else f"{stats['precision']:.0%}"
        )
        lines.append(
            f"| {ws} | {stats['relevant']} | {recall} | {stats['filed']} | "
            f"{precision} |"
        )
    if card.confusion:
        lines += [
            "",
            "## Where documents went instead",
            "",
            "| Should be | Went to | Documents |",
            "|---|---|---|",
        ]
        for expected, predicted, count in card.confusion[:_CONFUSION_SHOW]:
            went = "(not sure)" if predicted is None else predicted
            lines.append(f"| {expected or '?'} | {went} | {count} |")
        remaining = len(card.confusion) - _CONFUSION_SHOW
        if remaining > 0:
            lines.append(f"| … | … | +{remaining} more pairs |")
    lines.append("")
    return "\n".join(lines)
