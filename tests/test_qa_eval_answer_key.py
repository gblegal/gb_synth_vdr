"""Tests for gate 19 — an eval room does not freeze without a complete,
current classification answer key.

`python3 -m synthvdr answerkey` already refuses to WRITE a partial key,
but nothing required an eval room to HAVE one: a room could declare
ROOM_ROLE="eval", pass all eighteen gates and /vdr-package --strict, and
ship unusable for the one thing an eval room exists to do — score the
classifier. And a key written early could silently go stale as later
waves added or relabelled documents. This gate closes both holes by
rebuilding the key in memory through the same code the CLI uses and
comparing it with the file on disk.

Exemplar rooms PASS with a detail saying why (they teach the classifier
and are never scored) — not SKIP, which --strict would turn into a hard
failure for every exemplar room ever packaged.
"""

from __future__ import annotations

import json
from pathlib import Path

from synthvdr.answer_key import answer_key_records
from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.qa.integrity import gate_19_eval_answer_key
from synthvdr.qa.runner import FAIL, PASS, SKIP, GateContext
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import FindingSet

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


def _room(
    tmp_path: Path,
    role: str | None = "eval",
    labels: str | None = LABELS,
    docs=("01_corporate/1.1.1_constitutional-01.md",),
):
    conf_text = CONF_LINES + (f'ROOM_ROLE="{role}"\n' if role else "")
    (tmp_path / "room.conf").write_text(conf_text)
    for rel in docs:
        doc = tmp_path / "data-room" / rel
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("body\n")
    key_root = tmp_path / "_key"
    key_root.mkdir(exist_ok=True)
    if labels is not None:
        (key_root / "labels.yaml").write_text(labels)
    conf = load_room_conf(tmp_path / "room.conf")
    return GateContext(
        room=tmp_path, conf=conf, findings=FindingSet([], ""), distractors=[], strict=False
    )


def _write_key(ctx: GateContext) -> Path:
    records = answer_key_records(ctx.room, ctx.conf, load_domain(DEFAULT_DOMAIN_ROOT))
    out = ctx.key_root / "answer-key.jsonl"
    out.write_text("".join(json.dumps(r) + "\n" for r in records))
    return out


def test_an_exemplar_room_passes_saying_why(tmp_path):
    result = gate_19_eval_answer_key(_room(tmp_path, role="exemplar", labels=None))
    assert result.status == PASS, (
        "an exemplar room must PASS, not SKIP: under --strict a skip is a "
        "hard failure, and every exemplar room ever packaged would fail a "
        "gate about an artefact it is right not to have"
    )
    assert "exemplar" in result.detail, (
        "a not-applicable pass must say why it is vacuous, or it reads as a "
        "check that ran"
    )


def test_an_undeclared_room_skips_deferring_to_gate_18(tmp_path):
    result = gate_19_eval_answer_key(_room(tmp_path, role=None, labels=None))
    assert result.status == SKIP, (
        "no ROOM_ROLE is gate 18's failure; this gate cannot tell whether a "
        "key is required, and says so — and --strict still refuses the room"
    )
    assert "18" in result.detail


def test_an_eval_room_with_no_key_fails_naming_the_command(tmp_path):
    result = gate_19_eval_answer_key(_room(tmp_path))
    assert result.status == FAIL, (
        "an eval room exists to score the classifier; freezing one with no "
        "classification key ships it unusable"
    )
    assert "answerkey" in result.detail, (
        "the failure must name the command that writes the key"
    )


def test_an_eval_room_with_a_current_key_passes(tmp_path):
    ctx = _room(tmp_path)
    _write_key(ctx)
    result = gate_19_eval_answer_key(ctx)
    assert result.status == PASS, result.detail
    assert "1" in result.detail, "the pass names how many documents are keyed"


def test_a_key_that_went_stale_fails_naming_the_document(tmp_path):
    # The key was written, then a later wave added a document and its
    # label. The key on disk no longer covers the room.
    ctx = _room(tmp_path)
    _write_key(ctx)
    extra = ctx.room / "data-room" / "01_corporate" / "1.1.2_constitutional-02.md"
    extra.write_text("body\n")
    (ctx.key_root / "labels.yaml").write_text(
        LABELS
        + '  - path: "01_corporate/1.1.2_constitutional-02.md"\n'
        + '    document_type: "Shareholders\' agreement"\n'
    )
    result = gate_19_eval_answer_key(ctx)
    assert result.status == FAIL
    assert "1.1.2_constitutional-02" in result.detail, (
        "the drift must be named by path, not summarised to 'stale'"
    )
    assert "answerkey" in result.detail, "the fix is a re-run; the failure must say so"


def test_a_relabelled_document_fails_the_stale_key(tmp_path):
    # Same paths on both sides, but the author corrected a label after the
    # key was written — the row differs, and the later value must not ride.
    ctx = _room(tmp_path)
    _write_key(ctx)
    (ctx.key_root / "labels.yaml").write_text(
        LABELS.replace("Articles of association", "Shareholders' agreement")
    )
    result = gate_19_eval_answer_key(ctx)
    assert result.status == FAIL
    assert "1.1.1_constitutional-01" in result.detail


def test_an_unlabelled_room_fails_through_the_builders_own_message(tmp_path):
    # The key file exists but labels.yaml is gone: the rebuild cannot run,
    # and the gate reports the builder's refusal rather than crashing.
    ctx = _room(tmp_path)
    _write_key(ctx)
    (ctx.key_root / "labels.yaml").unlink()
    result = gate_19_eval_answer_key(ctx)
    assert result.status == FAIL
    assert "labels.yaml" in result.detail


def test_a_malformed_key_line_fails_plainly(tmp_path):
    ctx = _room(tmp_path)
    _write_key(ctx)
    key = ctx.key_root / "answer-key.jsonl"
    key.write_text(key.read_text() + "not json\n")
    result = gate_19_eval_answer_key(ctx)
    assert result.status == FAIL
    assert "JSON" in result.detail
