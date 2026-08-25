"""CLI-level tests for synthvdr.qa.__main__.

room.conf and the answer key are user-authored, and load_room_conf validates
path-valued keys and touches the filesystem, so both raise more often than a
first glance at the CLI wrapper suggests. This module's output is the most
user-facing surface in the project: a bad or missing room.conf, or a
malformed answer key, must print one readable line and exit non-zero, never
dump a traceback.
"""

from __future__ import annotations

from pathlib import Path

from synthvdr.qa.__main__ import main

VALID_CONF = {
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
    values = {**VALID_CONF, **overrides}
    text = "".join(f'{k}="{v}"\n' for k, v in values.items())
    (room / "room.conf").write_text(text, encoding="utf-8")


def test_missing_room_conf_exits_cleanly_without_a_traceback(tmp_path, capsys):
    code = main(["--room", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
    assert "room.conf" in captured.err


def test_nonexistent_room_directory_exits_cleanly_without_a_traceback(tmp_path, capsys):
    ghost = tmp_path / "does-not-exist-at-all"
    code = main(["--room", str(ghost)])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


def test_room_conf_missing_required_keys_exits_cleanly_without_a_traceback(tmp_path, capsys):
    (tmp_path / "room.conf").write_text('ROOM_CODENAME="Test Room"\n', encoding="utf-8")
    code = main(["--room", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err
    assert "missing" in captured.err.lower()


def test_malformed_answer_key_exits_cleanly_without_a_traceback(tmp_path, capsys):
    write_conf(tmp_path)
    (tmp_path / "_key").mkdir()
    (tmp_path / "_key" / "findings.yaml").write_text(
        "findings:\n  - severity: critical\n    title: no id here\n", encoding="utf-8"
    )
    code = main(["--room", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out
