"""Dedicated tests for gate 18 — the room declares its role.

Kept separate from the other gate test files for the same reason they are
kept separate from each other: this gate needs none of the domain-pack or
flagged-tree fixtures those files build. All it reads is room.conf.
"""

from __future__ import annotations

from synthvdr.qa.runner import FAIL, PASS, GateContext
from synthvdr.qa.structural import gate_18_room_role
from tests.conftest import gate_ctx, room_conf_text


def _ctx(tmp_path, role) -> GateContext:
    (tmp_path / "room.conf").write_text(room_conf_text(ROOM_CODENAME="Test Room", ROOM_ROLE=role))
    return gate_ctx(tmp_path)


def test_an_undeclared_room_fails_not_skips(tmp_path):
    result = gate_18_room_role(_ctx(tmp_path, role=None))
    assert result.status == FAIL, (
        "an undeclared room must FAIL, not SKIP: a skip under a lenient run "
        "would let the room slide into whichever use finds it first"
    )
    assert "exemplar" in result.detail and "eval" in result.detail, (
        "the failure must teach the vocabulary it is asking for"
    )


def test_a_declared_room_passes_naming_its_role(tmp_path):
    result = gate_18_room_role(_ctx(tmp_path, role="eval"))
    assert result.status == PASS
    assert result.detail == "eval", (
        "the transcript line should say which side of the split the room is "
        "on, so a human scanning it can catch a mislabelled room"
    )
