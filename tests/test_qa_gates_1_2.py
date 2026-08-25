"""Dedicated tests for gate_01_index and gate_02_counts.

Kept separate from tests/test_qa_runner.py, which exercises run_gates()
generically via trivial lambda gates and needs none of the domain-pack,
slot-manifest or real-flagged-tree fixtures these gates need. Also kept
separate from the tests/test_qa_structural.py that a later task adds for
gates 6-9 (that file is created from scratch by that task's own brief, with
its own fixtures) so this file cannot collide with or be overwritten by it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.index_build import render_index, write_index_sources
from synthvdr.qa.runner import GateContext
from synthvdr.qa.structural import gate_01_index, gate_02_counts
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import FindingSet
from synthvdr.slots import SIZE_PRESETS, build_slot_manifest
from synthvdr.twin import MARKER_NAME, build_flagged_tree

DEFAULT_CONF = {
    "ROOM_CODENAME": "Test Room",
    "INDEX_TOTAL": "1",
    "BLIND_TOTAL": "1",
    "FLAGGED_TOTAL": "1",
    "BLIND_TREE": "data-room",
    "FLAGGED_TREE": "_key/flagged",
    "KEY_ROOT": "_key",
    "FLAG_STRING_1": "Key diligence points",
    "FLAG_STRING_2": "DD flag",
    "FINDING_PREFIXES": "CORP",
    "SECTION_DIRS": "01_corporate",
    "EXPECTED_KDP_CARRIERS": "0",
}


def write_conf(room: Path, **overrides) -> None:
    values = {**DEFAULT_CONF, **overrides}
    text = "".join(f'{k}="{v}"\n' for k, v in values.items())
    (room / "room.conf").write_text(text, encoding="utf-8")


def ctx_for(room: Path) -> GateContext:
    return GateContext(
        room=room,
        conf=load_room_conf(room / "room.conf"),
        findings=FindingSet([], "Test Room"),
        distractors=[],
    )


# ---------------------------------------------------------------------------
# gate_01_index
# ---------------------------------------------------------------------------


def build_index(room: Path):
    """Write a real, internally-consistent index.md + _key/index-src/ pair
    from the real domain pack and slot manifest, and return (slots, text)."""
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    slots = build_slot_manifest(pack, SIZE_PRESETS["S"])
    index_src = room / "_key" / "index-src"
    write_index_sources(slots, pack, index_src)
    text = render_index(index_src)
    (room / "index.md").write_text(text, encoding="utf-8")
    return slots, text


def test_gate_01_passes_on_faithful_regeneration(tmp_path):
    slots, _ = build_index(tmp_path)
    write_conf(tmp_path, INDEX_TOTAL=str(len(slots)))
    assert gate_01_index(ctx_for(tmp_path)).status == "PASS"


def test_gate_01_fails_on_wrong_slot_count(tmp_path):
    slots, _ = build_index(tmp_path)
    write_conf(tmp_path, INDEX_TOTAL=str(len(slots) + 1))
    result = gate_01_index(ctx_for(tmp_path))
    assert result.status == "FAIL"
    assert str(len(slots)) in result.detail
    assert str(len(slots) + 1) in result.detail


def test_gate_01_fails_on_hand_edited_index_and_points_at_the_source(tmp_path):
    slots, text = build_index(tmp_path)
    write_conf(tmp_path, INDEX_TOTAL=str(len(slots)))
    # Append text that a regeneration would never produce, without adding
    # another '- N.N.N ' entry line, so the slot COUNT still matches and the
    # failure is forced down the regeneration-diff path, not the count path.
    tampered = text.rstrip("\n") + "\n\nHand-edited note that regeneration would never produce.\n"
    (tmp_path / "index.md").write_text(tampered, encoding="utf-8")
    result = gate_01_index(ctx_for(tmp_path))
    assert result.status == "FAIL"
    assert "_key/index-src" in result.detail
    assert "hand-edit" in result.detail.lower()


def test_gate_01_does_not_rewrite_index_md_on_failure(tmp_path):
    """A gate that silently repairs what it exists to detect is worse than no
    gate. Regenerating and fixing index.md here would erase the evidence of
    the very hand-edit gate 1 is meant to catch."""
    slots, text = build_index(tmp_path)
    write_conf(tmp_path, INDEX_TOTAL=str(len(slots)))
    tampered = text.rstrip("\n") + "\n\nHand-edited note.\n"
    (tmp_path / "index.md").write_text(tampered, encoding="utf-8")
    before = (tmp_path / "index.md").read_bytes()

    result = gate_01_index(ctx_for(tmp_path))

    after = (tmp_path / "index.md").read_bytes()
    assert result.status == "FAIL"
    assert after == before


def test_gate_01_skips_when_index_md_is_absent(tmp_path):
    build_index(tmp_path)
    (tmp_path / "index.md").unlink()
    write_conf(tmp_path)
    result = gate_01_index(ctx_for(tmp_path))
    assert result.status == "SKIP"
    assert "index.md" in result.detail
    assert "_key/index-src" in result.detail


def test_gate_01_skips_when_index_src_is_absent(tmp_path):
    build_index(tmp_path)
    shutil.rmtree(tmp_path / "_key" / "index-src")
    write_conf(tmp_path)
    result = gate_01_index(ctx_for(tmp_path))
    assert result.status == "SKIP"
    assert "_key/index-src" in result.detail


# ---------------------------------------------------------------------------
# gate_02_counts
# ---------------------------------------------------------------------------


def make_docs(root: Path, count: int) -> None:
    d = root / "01_corporate"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (d / f"1.1.{i + 1}_doc.md").write_text(f"# doc {i}\n\nBody.\n", encoding="utf-8")


def test_gate_02_passes_on_exact_blind_count(tmp_path):
    make_docs(tmp_path / "data-room", 2)
    write_conf(tmp_path, BLIND_TOTAL="2")
    assert gate_02_counts(ctx_for(tmp_path)).status == "PASS"


def test_gate_02_fails_when_blind_tree_holds_too_many(tmp_path):
    make_docs(tmp_path / "data-room", 3)
    write_conf(tmp_path, BLIND_TOTAL="2")
    result = gate_02_counts(ctx_for(tmp_path))
    assert result.status == "FAIL"
    assert "3" in result.detail
    assert "2" in result.detail


def test_gate_02_fails_when_blind_tree_holds_too_few(tmp_path):
    make_docs(tmp_path / "data-room", 1)
    write_conf(tmp_path, BLIND_TOTAL="2")
    result = gate_02_counts(ctx_for(tmp_path))
    assert result.status == "FAIL"
    assert "1" in result.detail
    assert "2" in result.detail


def test_gate_02_passes_on_blind_alone_when_flagged_tree_absent(tmp_path):
    make_docs(tmp_path / "data-room", 2)
    write_conf(tmp_path, BLIND_TOTAL="2")
    result = gate_02_counts(ctx_for(tmp_path))
    assert result.status == "PASS"
    assert "flagged tree absent" in result.detail


def test_gate_02_fails_when_flagged_tree_holds_wrong_count(tmp_path):
    make_docs(tmp_path / "data-room", 2)
    write_conf(tmp_path, BLIND_TOTAL="2", FLAGGED_TOTAL="2")
    conf = load_room_conf(tmp_path / "room.conf")
    build_flagged_tree(tmp_path, conf, FindingSet([], "Test Room"))
    # Corrupt a genuinely-built flagged tree with one stray extra document.
    (tmp_path / "_key/flagged/01_corporate/1.1.99_extra.md").write_text(
        "# extra\n\nBody.\n", encoding="utf-8"
    )
    result = gate_02_counts(ctx_for(tmp_path))
    assert result.status == "FAIL"
    assert "3" in result.detail
    assert "2" in result.detail


def test_gate_02_counts_md_and_csv_only_so_the_marker_never_shifts_the_count(tmp_path):
    """Build a REAL flagged tree via synthvdr.twin.build_flagged_tree so
    MARKER_NAME is genuinely present, then confirm gate 2's count matches
    FLAGGED_TOTAL exactly — i.e. the marker was not counted as a document.
    """
    make_docs(tmp_path / "data-room", 2)
    write_conf(tmp_path, BLIND_TOTAL="2", FLAGGED_TOTAL="2")
    conf = load_room_conf(tmp_path / "room.conf")
    build_flagged_tree(tmp_path, conf, FindingSet([], "Test Room"))

    flagged_root = tmp_path / "_key" / "flagged"
    marker = flagged_root / MARKER_NAME
    assert marker.is_file(), "the real twin builder must have written the marker"

    result = gate_02_counts(ctx_for(tmp_path))
    assert result.status == "PASS"
    assert "flagged 2" in result.detail


def test_gate_02_skips_when_blind_tree_is_absent(tmp_path):
    write_conf(tmp_path, BLIND_TOTAL="2")
    result = gate_02_counts(ctx_for(tmp_path))
    assert result.status == "SKIP"
    assert "data-room" in result.detail
