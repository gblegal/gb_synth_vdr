"""Dedicated tests for gate 18 — the room declares its role.

Kept separate from the other gate test files for the same reason they are
kept separate from each other: this gate needs none of the domain-pack or
flagged-tree fixtures those files build. All it reads is room.conf.
"""

from __future__ import annotations

from synthvdr.qa.runner import FAIL, PASS, GateContext
from synthvdr.qa.structural import gate_18_room_role
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


def _ctx(tmp_path, extra: str = "") -> GateContext:
    (tmp_path / "room.conf").write_text(CONF_LINES + extra)
    conf = load_room_conf(tmp_path / "room.conf")
    return GateContext(
        room=tmp_path, conf=conf, findings=FindingSet([], ""), distractors=[], strict=False
    )


def test_an_undeclared_room_fails_not_skips(tmp_path):
    result = gate_18_room_role(_ctx(tmp_path))
    assert result.status == FAIL, (
        "an undeclared room must FAIL, not SKIP: a skip under a lenient run "
        "would let the room slide into whichever use finds it first"
    )
    assert "exemplar" in result.detail and "eval" in result.detail, (
        "the failure must teach the vocabulary it is asking for"
    )


def test_a_declared_room_passes_naming_its_role(tmp_path):
    result = gate_18_room_role(_ctx(tmp_path, 'ROOM_ROLE="eval"\n'))
    assert result.status == PASS
    assert result.detail == "eval", (
        "the transcript line should say which side of the split the room is "
        "on, so a human scanning it can catch a mislabelled room"
    )
