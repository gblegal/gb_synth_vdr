"""Tests for the classification scorecard — `python3 -m synthvdr
score-classification <output> --room .`.

The room's classification answer key records facts: what each document is
and which of the room's own sections — hence which classifier workstream —
it belongs to. This scorer grades any tool's classification output against
those facts: document type, primary pile, the not-sure count, and a
confusion table saying where the misfiled documents went.

What it deliberately does NOT score: secondary deliveries. The key's
`secondary_workstreams` is empty by design — who ELSE should see a
document is the downstream project's routing policy, not a fact about the
room — so a scorer that counted a tool's secondaries as noise would
penalise every policy-following classifier for its own routing table. The
scorecard says this out loud rather than leaving a suspiciously absent
column to be guessed at.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synthvdr.classify_score import (
    ClassificationOutputError,
    ClassificationScoreError,
    load_classification_output,
    render_classification_scorecard,
    score_classification,
)
from synthvdr.score import check_provenance

KEY = [
    {"source_path": "01_corporate/articles.md", "document_type": "Articles of association",
     "primary_workstream": "corporate", "secondary_workstreams": []},
    {"source_path": "01_corporate/spa.md", "document_type": "Share purchase agreement",
     "primary_workstream": "corporate", "secondary_workstreams": []},
    {"source_path": "06_property/lease.md", "document_type": "Lease",
     "primary_workstream": "real-estate", "secondary_workstreams": []},
]


def _output_doc(rows, tool="acme/1.0", room_hash=""):
    return {"tool": tool, "room_hash": room_hash, "classifications": rows}


def _rows(*triples):
    """(path, type, workstream) shorthand; workstream None means unsure."""
    rows = []
    for path, doc_type, workstream in triples:
        rows.append({
            "source_path": path,
            "document_type": doc_type,
            "primary_workstream": workstream,
            "secondary_workstreams": [],
            "unsure": workstream is None,
        })
    return rows


def _write(tmp_path: Path, name: str, payload) -> Path:
    out = tmp_path / name
    if name.endswith(".jsonl"):
        out.write_text("".join(json.dumps(r) + "\n" for r in payload))
    else:
        out.write_text(json.dumps(payload))
    return out


def _load_key(rows):
    from synthvdr.classify_score import ClassificationRecord

    return [ClassificationRecord.from_row(r) for r in rows]


def test_a_perfect_output_scores_perfectly(tmp_path):
    path = _write(tmp_path, "out.json", _output_doc(_rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
        ("01_corporate/spa.md", "Share purchase agreement", "corporate"),
        ("06_property/lease.md", "Lease", "real-estate"),
    )))
    output = load_classification_output(path)
    card = score_classification(output, _load_key(KEY))
    assert card.documents == 3
    assert card.type_correct == 3
    assert card.primary_correct == 3
    assert card.unsure == 0
    assert card.workstreams["corporate"]["recall"] == 1.0
    assert card.workstreams["corporate"]["precision"] == 1.0
    assert card.confusion == []


def test_a_misfiled_document_shows_in_the_confusion_table(tmp_path):
    path = _write(tmp_path, "out.json", _output_doc(_rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
        ("01_corporate/spa.md", "Lease", "real-estate"),  # wrong pile
        ("06_property/lease.md", "Lease", "real-estate"),
    )))
    card = score_classification(load_classification_output(path), _load_key(KEY))
    assert card.type_correct == 2
    assert card.primary_correct == 2
    assert card.workstreams["corporate"]["recall"] == 0.5
    assert card.workstreams["real-estate"]["precision"] == 0.5, (
        "the wrongly filed SPA is noise in the real-estate pile"
    )
    assert card.confusion == [["corporate", "real-estate", 1]]


def test_an_unsure_document_is_counted_not_blamed_as_a_pile(tmp_path):
    path = _write(tmp_path, "out.json", _output_doc(_rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
        ("01_corporate/spa.md", None, None),
        ("06_property/lease.md", "Lease", "real-estate"),
    )))
    card = score_classification(load_classification_output(path), _load_key(KEY))
    assert card.unsure == 1
    assert card.workstreams["corporate"]["recall"] == 0.5, (
        "an unsure document still failed to reach its pile"
    )
    assert card.confusion == [["corporate", None, 1]], (
        "the confusion table records not-sure as its own destination, not a pile"
    )


def test_coverage_mismatch_refuses_to_score(tmp_path):
    path = _write(tmp_path, "out.json", _output_doc(_rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
    )))
    with pytest.raises(ClassificationScoreError, match="spa.md"):
        score_classification(load_classification_output(path), _load_key(KEY))


def test_a_duplicate_path_in_the_output_is_refused(tmp_path):
    rows = _rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
        ("01_corporate/articles.md", "Lease", "real-estate"),
    )
    path = _write(tmp_path, "out.json", _output_doc(rows))
    with pytest.raises(ClassificationOutputError, match="articles.md"):
        load_classification_output(path)


def test_a_bare_jsonl_manifest_loads_leniently(tmp_path):
    # A gb-docclass manifest is JSONL with these fields plus many more —
    # it must load as-is, with unknown tool and UNVERIFIED provenance.
    rows = _rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
        ("01_corporate/spa.md", "Share purchase agreement", "corporate"),
        ("06_property/lease.md", "Lease", "real-estate"),
    )
    for row in rows:
        row["decision_path"] = [{"pass": "folder"}]
        row["confidence"] = "very likely"
    path = _write(tmp_path, "manifest.jsonl", rows)
    output = load_classification_output(path)
    assert output.room_hash == ""
    assert len(output.records) == 3
    status = check_provenance(tmp_path, output)
    assert not status.verified


def test_sent_to_all_workstreams_reaches_every_pile(tmp_path):
    rows = _rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
        ("01_corporate/spa.md", "Share purchase agreement", "corporate"),
        ("06_property/lease.md", "Lease", "real-estate"),
    )
    rows[2]["primary_workstream"] = "corporate"
    rows[2]["sent_to_all_workstreams"] = True
    path = _write(tmp_path, "out.json", _output_doc(rows))
    card = score_classification(load_classification_output(path), _load_key(KEY))
    assert card.primary_correct == 2, (
        "fan-out does not make a wrong primary right — the lease's pile is real-estate"
    )
    assert card.workstreams["real-estate"]["recall"] == 0.0, (
        "primary-pile recall is about the pile a reviewer opens first; a "
        "sent-to-everyone copy is delivery, and delivery is the downstream "
        "eval's question, not this one's"
    )


def test_an_empty_output_is_an_error_not_a_zero_run(tmp_path):
    path = _write(tmp_path, "empty.jsonl", [])
    with pytest.raises(ClassificationOutputError):
        load_classification_output(path)


def test_the_scorecard_names_the_policy_boundary(tmp_path):
    path = _write(tmp_path, "out.json", _output_doc(_rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
        ("01_corporate/spa.md", "Share purchase agreement", "corporate"),
        ("06_property/lease.md", "Lease", "real-estate"),
    )))
    output = load_classification_output(path)
    card = score_classification(output, _load_key(KEY))
    text = render_classification_scorecard(card, output, provenance=None)
    assert "routing policy" in text, (
        "the scorecard must say why secondaries are not scored, or the "
        "absent column reads as an oversight"
    )
    assert "acme/1.0" in text
    assert "corporate" in text


CONF = """\
ROOM_CODENAME="Project Testbed"
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
ROOM_ROLE="eval"
"""


def _cli_room(tmp_path: Path) -> Path:
    (tmp_path / "room.conf").write_text(CONF)
    key_root = tmp_path / "_key"
    key_root.mkdir()
    (key_root / "answer-key.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in KEY)
    )
    return tmp_path


def test_cli_scores_a_room_and_exits_zero(tmp_path, capsys):
    from synthvdr.__main__ import main

    room = _cli_room(tmp_path)
    out = _write(tmp_path, "out.json", _output_doc(_rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
        ("01_corporate/spa.md", "Share purchase agreement", "corporate"),
        ("06_property/lease.md", "Lease", "real-estate"),
    )))
    code = main(["score-classification", str(out), "--room", str(room)])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "Classification scorecard" in printed
    assert "UNVERIFIED" in printed, (
        "no manifest.json in this room, so provenance must be reported "
        "unverified rather than silently assumed"
    )


def test_cli_refuses_a_room_with_no_answer_key(tmp_path, capsys):
    from synthvdr.__main__ import main

    (tmp_path / "room.conf").write_text(CONF)
    (tmp_path / "_key").mkdir()
    out = _write(tmp_path, "out.json", _output_doc(_rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
    )))
    code = main(["score-classification", str(out), "--room", str(tmp_path)])
    err = capsys.readouterr().err
    assert code == 2
    assert "answerkey" in err, (
        "a missing key must name the command that writes it, same as gate 19"
    )


def test_cli_refuses_a_proven_room_hash_mismatch(tmp_path, capsys):
    from synthvdr.__main__ import main

    room = _cli_room(tmp_path)
    (room / "_key" / "manifest.json").write_text(json.dumps({"content_hash": "sha256:aaa"}))
    out = _write(tmp_path, "out.json", _output_doc(_rows(
        ("01_corporate/articles.md", "Articles of association", "corporate"),
        ("01_corporate/spa.md", "Share purchase agreement", "corporate"),
        ("06_property/lease.md", "Lease", "real-estate"),
    ), room_hash="sha256:bbb"))
    code = main(["score-classification", str(out), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 2
    assert "different room" in captured.err
    assert "Classification scorecard" not in captured.out, (
        "a proven mismatch must abort before any scorecard is printed"
    )
