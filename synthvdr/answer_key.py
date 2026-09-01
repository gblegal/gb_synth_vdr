"""The classification answer key: _key/labels.yaml -> _key/answer-key.jsonl.

The downstream classifier (gb-docclass) scores itself against rooms whose
generator recorded what every document is, in the classifier's own
vocabulary — its taxonomy's document-type names and its fixed workstream
IDs. Two sources feed the key:

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

_SHOW = 10


class AnswerKeyError(Exception):
    """The labels file or the domain pack cannot support a complete key."""


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


def build_answer_key(room: Path, conf: RoomConf, pack: DomainPack) -> Path:
    """Write _key/answer-key.jsonl covering every blind document."""
    blind_root = room / conf.get_relative_path("BLIND_TREE")
    key_root = room / conf.get_relative_path("KEY_ROOT")
    labels = load_labels(key_root / LABELS_NAME)

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

    out = key_root / ANSWER_KEY_NAME
    out.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return out
