"""Tests for the classification answer key — _key/labels.yaml in,
_key/answer-key.jsonl out.

The downstream classifier (gb-docclass) scores itself against rooms whose
generator recorded what every document is. This module's contract: the
labels file names a document type for every blind document, the domain
pack names the classifier's workstream for every section, and the builder
refuses to write a key that covers anything less than the whole room —
a partially-labelled room would grade the classifier against silence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synthvdr.answer_key import AnswerKeyError, build_answer_key, load_labels
from synthvdr.domain import DEFAULT_DOMAIN_ROOT, Archetype, DomainPack, Section, load_domain
from synthvdr.roomconf import load_room_conf

CONF_LINES = """\
ROOM_CODENAME="Test Room"
INDEX_TOTAL=1
BLIND_TOTAL=1
FLAGGED_TOTAL=1
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="CORP"
SECTION_DIRS="01_corporate"
EXPECTED_KDP_CARRIERS=0
"""

LABELS = """\
labels:
  - path: "01_corporate/1.1.1_constitutional-01.md"
    document_type: "Articles of association"
"""


def _room(tmp_path: Path, labels: str = LABELS, docs=("01_corporate/1.1.1_constitutional-01.md",)):
    (tmp_path / "room.conf").write_text(CONF_LINES)
    for rel in docs:
        doc = tmp_path / "data-room" / rel
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("body\n")
    key_root = tmp_path / "_key"
    key_root.mkdir(exist_ok=True)
    if labels is not None:
        (key_root / "labels.yaml").write_text(labels)
    return tmp_path, load_room_conf(tmp_path / "room.conf")


def test_a_fully_labelled_room_produces_the_key(tmp_path):
    room, conf = _room(tmp_path)
    out = build_answer_key(room, conf, load_domain(DEFAULT_DOMAIN_ROOT))
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert records == [
        {
            "source_path": "01_corporate/1.1.1_constitutional-01.md",
            "document_type": "Articles of association",
            "primary_workstream": "corporate",
            "secondary_workstreams": [],
        }
    ], (
        "one line per document, speaking the classifier's vocabulary: the "
        "type from the labels file, the workstream from the section's "
        "classifier_workstream"
    )
    assert out == room / "_key" / "answer-key.jsonl", (
        "the key is answer-key material and lives under KEY_ROOT"
    )


def test_an_unlabelled_document_refuses_the_whole_key(tmp_path):
    room, conf = _room(
        tmp_path,
        docs=(
            "01_corporate/1.1.1_constitutional-01.md",
            "01_corporate/1.1.2_constitutional-02.md",
        ),
    )
    with pytest.raises(AnswerKeyError, match="1.1.2_constitutional-02"):
        build_answer_key(room, conf, load_domain(DEFAULT_DOMAIN_ROOT))


def test_a_label_for_a_document_that_does_not_exist_is_refused(tmp_path):
    phantom = LABELS + (
        '  - path: "01_corporate/9.9.9_ghost.md"\n'
        '    document_type: "Lease"\n'
    )
    room, conf = _room(tmp_path, labels=phantom)
    with pytest.raises(AnswerKeyError, match="9.9.9_ghost"):
        build_answer_key(room, conf, load_domain(DEFAULT_DOMAIN_ROOT))


def test_a_duplicate_label_is_refused_not_last_wins(tmp_path):
    doubled = LABELS + (
        '  - path: "01_corporate/1.1.1_constitutional-01.md"\n'
        '    document_type: "Lease"\n'
    )
    room, conf = _room(tmp_path, labels=doubled)
    with pytest.raises(AnswerKeyError, match="1.1.1_constitutional-01"):
        load_labels(room / "_key" / "labels.yaml")


def test_missing_labels_file_is_a_plain_refusal(tmp_path):
    room, conf = _room(tmp_path, labels=None)
    with pytest.raises(AnswerKeyError, match="labels.yaml"):
        build_answer_key(room, conf, load_domain(DEFAULT_DOMAIN_ROOT))


def test_a_section_without_classifier_workstream_is_refused(tmp_path):
    room, conf = _room(tmp_path)
    bare = DomainPack(
        sections=[
            Section(
                number=1,
                dir_name="01_corporate",
                title="Corporate",
                workstream="corporate",
                weight=1.0,
                subsections=["constitutional"],
            )
        ],
        archetypes={"standard": Archetype(name="standard", floor=1, filename_patterns=[])},
        default_archetype="standard",
        tier_f_floor=1,
        finding_archetypes={"corporate": []},
    )
    with pytest.raises(AnswerKeyError, match="01_corporate") as excinfo:
        build_answer_key(room, conf, bare)
    assert "classifier_workstream" in str(excinfo.value), (
        "the fix is a field in sections.yaml; the error must name it"
    )


def test_the_shipped_pack_names_a_classifier_workstream_for_every_section():
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    missing = [s.dir_name for s in pack.sections if not s.classifier_workstream]
    assert missing == [], (
        "every shipped section must say which classifier workstream its "
        f"documents belong to; missing: {missing}"
    )


def test_cli_writes_the_key_and_reports(tmp_path):
    from synthvdr.__main__ import main

    _room(tmp_path)
    assert main(["answerkey", "--room", str(tmp_path)]) == 0
    assert (tmp_path / "_key" / "answer-key.jsonl").is_file()


def test_cli_refuses_plainly_without_labels(tmp_path, capsys):
    from synthvdr.__main__ import main

    _room(tmp_path, labels=None)
    assert main(["answerkey", "--room", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "labels.yaml" in err
    assert "\n" not in err.strip(), "errors print as one readable line"


# ---------------------------------------------------------------------------
# consolidate_wave_labels — the wave hand-back half. Same guarantees as
# consolidate_wave_incoming: pure, safe to re-run over untouched intake,
# and a conflict raises rather than letting the later value silently win.
# ---------------------------------------------------------------------------


def test_wave_labels_merge_sorted_by_path():
    from synthvdr.answer_key import consolidate_wave_labels

    merged = consolidate_wave_labels(
        {"labels": []},
        {
            "wave1-batch-b": {"labels": [
                {"path": "02_financial/2.1.1_accounts-01.md",
                 "document_type": "Statutory accounts"}]},
            "wave1-batch-a": {"labels": [
                {"path": "01_corporate/1.1.1_constitutional-01.md",
                 "document_type": "Articles of association"}]},
        },
    )
    assert [row["path"] for row in merged["labels"]] == [
        "01_corporate/1.1.1_constitutional-01.md",
        "02_financial/2.1.1_accounts-01.md",
    ], "labels.yaml is canonical and sorted, so reruns produce the same bytes"


def test_wave_labels_rerun_over_untouched_intake_is_a_no_op():
    from synthvdr.answer_key import consolidate_wave_labels

    incoming = {
        "wave1-batch-a": {"labels": [
            {"path": "01_corporate/1.1.1_constitutional-01.md",
             "document_type": "Articles of association"}]},
    }
    once = consolidate_wave_labels({"labels": []}, incoming)
    twice = consolidate_wave_labels(once, incoming)
    assert twice == once, (
        "wave n re-reads every prior wave's intake; an already-applied "
        "label must be a no-op, or every wave doubles the file"
    )


def test_wave_labels_conflict_raises_rather_than_last_wins():
    from synthvdr.answer_key import consolidate_wave_labels

    existing = {"labels": [
        {"path": "01_corporate/1.1.1_constitutional-01.md",
         "document_type": "Articles of association"}]}
    with pytest.raises(AnswerKeyError, match="1.1.1_constitutional-01") as excinfo:
        consolidate_wave_labels(
            existing,
            {"wave2-batch-a": {"labels": [
                {"path": "01_corporate/1.1.1_constitutional-01.md",
                 "document_type": "Lease"}]}},
        )
    message = str(excinfo.value)
    assert "Articles of association" in message and "Lease" in message, (
        "both claimed types must be named — the author needs to see what "
        "they are contradicting"
    )


def test_wave_labels_malformed_row_names_the_file_and_index():
    from synthvdr.answer_key import consolidate_wave_labels

    with pytest.raises(AnswerKeyError, match=r"wave1-batch-a.*labels\[1\]"):
        consolidate_wave_labels(
            {"labels": []},
            {"wave1-batch-a": {"labels": [
                {"path": "a.md", "document_type": "Lease"},
                {"path": "b.md"},
            ]}},
        )


# ---------------------------------------------------------------------------
# The optional vocabulary check — authors free-type document_type, and a
# drifted name ("NDA" for "Non-disclosure agreement") would score as a
# classifier miss. The classifier's own list, handed in as a file, catches
# the drift at key-build time.
# ---------------------------------------------------------------------------


def test_vocabulary_refuses_a_name_outside_the_list(tmp_path):
    room, conf = _room(tmp_path)
    with pytest.raises(AnswerKeyError, match="Articles of association"):
        build_answer_key(
            room, conf, load_domain(DEFAULT_DOMAIN_ROOT), vocabulary={"Lease"}
        )


def test_vocabulary_accepts_a_listed_name(tmp_path):
    room, conf = _room(tmp_path)
    out = build_answer_key(
        room, conf, load_domain(DEFAULT_DOMAIN_ROOT),
        vocabulary={"Articles of association"},
    )
    assert out.is_file()


def test_cli_vocabulary_flag(tmp_path, capsys):
    from synthvdr.__main__ import main

    _room(tmp_path)
    vocab = tmp_path / "types.txt"
    vocab.write_text("# the classifier's document list\nLease\n")
    assert main(["answerkey", "--room", str(tmp_path),
                 "--vocabulary", str(vocab)]) == 2
    assert "Articles of association" in capsys.readouterr().err
