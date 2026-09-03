"""The classification answer key: _key/labels.yaml -> _key/answer-key.jsonl.

The downstream classifier (gb-docclass) scores itself against rooms whose
generator recorded what every document is, in the classifier's own
vocabulary — its taxonomy's document-type names and its fixed workstream
IDs. That taxonomy is derived in part from the SALI LMSS (MIT, Copyright
(c) 2022 SALI Alliance); the README's licence section says what that means
for this repository and where the full provenance is recorded. Two sources
feed the key:

  - `_key/labels.yaml` — one entry per blind document, written by the
    authoring flow at the moment the document is written (never derived
    afterwards by reading the finished room, which would grade the
    classifier against a second classifier rather than against intent):

        labels:
          - path: "01_corporate/1.1.1_constitutional-01.md"
            document_type: "Articles of association"

  - the domain pack's per-section `classifier_workstream`, which says
    which of the classifier's workstreams a section's documents belong to.

The output format is pinned by gb-docclass (docs/answer-key-format.md
there): one JSON line per document — source_path, document_type,
primary_workstream, secondary_workstreams. This side emits what the
generator actually knows: the type it deliberately wrote and the
workstream the room's own organisation assigns. secondary_workstreams is
always empty here — which OTHER teams should see a document is that
project's routing policy, not a fact about the room.

The builder refuses a key that covers anything less than the whole blind
tree, in either direction: an unlabelled document would grade the
classifier against silence, and a label pointing at nothing is a typo
that would otherwise sit unnoticed until it mattered. Output is sorted
and carries no timestamp, so the same room produces the same bytes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import yaml

from .domain import DomainPack
from .roomconf import RoomConf

ANSWER_KEY_NAME = "answer-key.jsonl"
LABELS_NAME = "labels.yaml"
# Where a room keeps the classifier's own document-type list, one name per
# line. It is a COPY of the downstream taxonomy's type names, held in the
# room so the authoring flow can be handed it without reaching into the
# classifier's repository — and so a wave can be checked against it while
# the wave that caused a drift is still the one being worked on.
VOCABULARY_NAME = "classifier-vocab.txt"

_SHOW = 10


class AnswerKeyError(Exception):
    """The labels file or the domain pack cannot support a complete key."""


def load_vocabulary(path: Path) -> set:
    """The classifier's document-type names, one per line.

    Blank lines and `#` comments are skipped so the file can carry a
    provenance header; everything else is taken verbatim, because a type
    name is matched by exact string equality and normalising it here would
    silently accept a label the classifier itself would not.
    """
    return {
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def labels_outside_vocabulary(labels: Dict[str, str], vocabulary) -> Dict[str, list]:
    """`{document_type: [paths]}` for every label the classifier's list does
    not contain, sorted.

    This exists so a drift can be caught in the WAVE THAT CAUSED IT rather
    than at package time. `answer_key_records` refuses the same condition,
    but it refuses by raising, at the end of the build, once — which is far
    too late to be actionable: the room that surfaced this had 184 of 200
    labels outside the list, written across ten author batches over two
    waves, and every one of them had to be remapped by hand after the fact
    because nothing checked while the authors were still running.

    Returns rather than raises, and groups by type rather than listing
    paths flat, because the caller is a per-wave loop that wants to see
    which NAMES drifted (usually a handful of near-misses — 'NDA' for
    'Non-disclosure agreement') and which documents each one touched.
    """
    out: Dict[str, list] = {}
    for path, document_type in labels.items():
        if document_type not in vocabulary:
            out.setdefault(document_type, []).append(path)
    return {k: sorted(v) for k, v in sorted(out.items())}


def _named(paths, limit: int = _SHOW) -> str:
    shown = ", ".join(paths[:limit])
    remaining = len(paths) - limit
    if remaining > 0:
        shown += f" (+{remaining} more)"
    return shown


def load_labels(path: Path) -> Dict[str, str]:
    """path -> document_type for every labelled document.

    A duplicate path is refused whether or not the two types agree, for
    the same reason room.conf refuses a key set twice: which copy was
    edited is luck, not intent.
    """
    if not path.is_file():
        raise AnswerKeyError(
            f"no {path.name} at {path} — the authoring flow records each "
            "document's type there as it writes it, and without it there is "
            "nothing to build a key from"
        )
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = doc.get("labels")
    if not isinstance(rows, list):
        raise AnswerKeyError(f"{path}: expected a top-level 'labels:' list")
    labels: Dict[str, str] = {}
    for i, row in enumerate(rows, start=1):
        rel = (row or {}).get("path")
        doc_type = (row or {}).get("document_type")
        if not rel or not doc_type:
            raise AnswerKeyError(
                f"{path}: entry {i} needs both 'path' and 'document_type'"
            )
        if rel in labels:
            raise AnswerKeyError(
                f"{path}: {rel} is labelled twice ({labels[rel]!r} and "
                f"{doc_type!r}) — the later entry would silently win; "
                "remove one"
            )
        labels[rel] = doc_type
    return labels


def consolidate_wave_labels(labels_doc: dict, incoming_docs: Dict[str, dict]) -> dict:
    """Merge a wave's `_key/incoming/*.yaml` label rows into `_key/labels.yaml`'s
    parsed document, and return the merged document.

    Pure and file-I/O-free, with the same two guarantees as
    `schema.consolidate_wave_incoming`, because /vdr-build re-reads every
    prior wave's untouched intake on every wave: an already-applied label
    (same path, same type) is a no-op, and a conflicting one (same path,
    different type) raises naming the file, the path and both claimed
    types — the later value must never silently win, for the same reason
    room.conf refuses a key set twice. Output is sorted by path so the
    canonical file's bytes do not depend on wave order.
    """
    merged: Dict[str, str] = {}
    claimed_by: Dict[str, str] = {}
    for i, row in enumerate(labels_doc.get("labels") or [], start=1):
        rel = (row or {}).get("path")
        doc_type = (row or {}).get("document_type")
        if not rel or not doc_type:
            raise AnswerKeyError(
                f"labels.yaml: entry {i} needs both 'path' and 'document_type'"
            )
        merged[rel] = doc_type
        claimed_by[rel] = "labels.yaml"
    for label, incoming in sorted(incoming_docs.items()):
        for index, row in enumerate((incoming or {}).get("labels") or []):
            rel = (row or {}).get("path")
            doc_type = (row or {}).get("document_type")
            if not rel or not doc_type:
                raise AnswerKeyError(
                    f"{label}: labels[{index}] needs both 'path' and "
                    "'document_type'"
                )
            if rel in merged and merged[rel] != doc_type:
                raise AnswerKeyError(
                    f"{label}: {rel} is already labelled "
                    f"{merged[rel]!r} (by {claimed_by[rel]}) and this row "
                    f"says {doc_type!r} — the later value must not silently "
                    "win; decide which is right and fix the losing file"
                )
            merged[rel] = doc_type
            claimed_by.setdefault(rel, label)
    return {
        "labels": [
            {"path": rel, "document_type": merged[rel]} for rel in sorted(merged)
        ]
    }


def answer_key_records(
    room: Path, conf: RoomConf, pack: DomainPack, vocabulary=None
) -> list:
    """The key's records, in file order, without touching the output file.

    This is the whole derivation — `build_answer_key` writes exactly what
    this returns, and gate 19 rebuilds through this same function to test
    the file on disk for staleness. One implementation, two callers: a
    second derivation would drift from the first, and the gate would then
    certify agreement with itself rather than with the room.

    `vocabulary`, when given, is the classifier's own set of document-type
    names; a label outside it is refused by name. Authors free-type the
    labels, and a drifted name ('NDA' for 'Non-disclosure agreement')
    would otherwise ride into the key and score as a classifier miss.
    Refusals only — it never alters a record — so a rebuild without the
    vocabulary still reproduces a key that was written with one.
    """
    blind_root = room / conf.get_relative_path("BLIND_TREE")
    key_root = room / conf.get_relative_path("KEY_ROOT")
    labels = load_labels(key_root / LABELS_NAME)

    if vocabulary is not None:
        # Same predicate the per-wave check uses, so a wave that clears
        # `labels_outside_vocabulary` cannot fail here for a different reason.
        unknown = sorted(labels_outside_vocabulary(labels, vocabulary))
        if unknown:
            raise AnswerKeyError(
                f"{len(unknown)} label(s) use a name outside the "
                "classifier's document list — likely author drift; fix the "
                "label or extend the list: " + _named(unknown)
            )

    docs = sorted(
        p.relative_to(blind_root).as_posix()
        for p in blind_root.rglob("*")
        if p.is_file() and p.suffix in (".md", ".csv")
    )
    unlabelled = [d for d in docs if d not in labels]
    if unlabelled:
        raise AnswerKeyError(
            f"{len(unlabelled)} blind document(s) have no label — a key "
            "that skips them would grade the classifier against silence: "
            + _named(unlabelled)
        )
    phantom = sorted(set(labels) - set(docs))
    if phantom:
        raise AnswerKeyError(
            f"{len(phantom)} label(s) point at no blind document — a typo "
            "in the path, or a document since removed: " + _named(phantom)
        )

    workstream_by_dir: Dict[str, str] = {}
    records = []
    for rel in docs:
        section_dir = rel.split("/", 1)[0]
        if section_dir not in workstream_by_dir:
            section = pack.section_by_dir(section_dir)
            if not section.classifier_workstream:
                raise AnswerKeyError(
                    f"section {section_dir} has no classifier_workstream in "
                    "the domain pack's sections.yaml — add the field before "
                    "building a key from this pack"
                )
            workstream_by_dir[section_dir] = section.classifier_workstream
        records.append(
            {
                "source_path": rel,
                "document_type": labels[rel],
                "primary_workstream": workstream_by_dir[section_dir],
                "secondary_workstreams": [],
            }
        )
    return records


def build_answer_key(
    room: Path, conf: RoomConf, pack: DomainPack, vocabulary=None
) -> Path:
    """Write _key/answer-key.jsonl covering every blind document."""
    records = answer_key_records(room, conf, pack, vocabulary=vocabulary)
    out = room / conf.get_relative_path("KEY_ROOT") / ANSWER_KEY_NAME
    out.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return out
