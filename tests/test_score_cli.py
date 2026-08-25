"""CLI-level tests for synthvdr.__main__ (`python3 -m synthvdr score ...`).

Two things this file exists to catch that unit tests on synthvdr.score
cannot:

  - the CLI wiring itself — exit codes, what gets printed where, and that a
    proven room_hash mismatch actually aborts before a scorecard is
    printed, rather than merely being detectable by a caller who inspects
    the return value;
  - that adding a package-level synthvdr/__main__.py did not change how
    `python3 -m synthvdr.qa` resolves. `-m package.submodule` runs
    `package/submodule/__main__.py` directly when submodule is itself a
    package with its own __main__.py (synthvdr.qa is), so in principle this
    file should never even be imported by that invocation — but "in
    principle" is exactly the kind of claim this project verifies with a
    subprocess rather than trusts.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from synthvdr.__main__ import main

CONF = '''ROOM_CODENAME="Project Testbed"
INDEX_TOTAL=1
BLIND_TOTAL=1
FLAGGED_TOTAL=1
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="ENV"
EXPECTED_KDP_CARRIERS=0
SECTION_DIRS="11_environmental-hs"
'''

SRC = "11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md"

FINDINGS_YAML = f"""\
room: Project Testbed
findings:
  - id: ENV-1
    title: Contamination under-provisioned
    severity: critical
    workstream: environmental
    multi_document: false
    source: {SRC}
    location: Table 4
    substance: estimate exceeds provision
  - id: EMP-2
    title: Contractors misclassified
    severity: medium
    workstream: employment
    multi_document: false
    source: 09_employment/9.1_contracts/9.1.4_consultancy.md
    location: clause 3
    substance: misclassification
"""

DISTRACTORS_YAML = "distractors: []\n"


@pytest.fixture
def room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF, encoding="utf-8")
    key = tmp_path / "_key"
    key.mkdir()
    (key / "findings.yaml").write_text(FINDINGS_YAML, encoding="utf-8")
    (key / "distractors.yaml").write_text(DISTRACTORS_YAML, encoding="utf-8")
    return tmp_path


def write_output(room, name="out.json", room_hash="", documents=None, tool="acme/1.0"):
    path = room / name
    path.write_text(
        json.dumps(
            {
                "tool": tool,
                "room_hash": room_hash,
                "findings": [
                    {"title": "t", "severity": "critical", "documents": documents or [], "summary": "s"}
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def write_multi_output(room, name, findings_rows, room_hash="", tool="acme/1.0"):
    """Like write_output, but for tests that need more than one reported
    finding — e.g. so tool_index 1 exists for an adjudication to target.
    """
    path = room / name
    path.write_text(json.dumps({"tool": tool, "room_hash": room_hash, "findings": findings_rows}), encoding="utf-8")
    return path


def write_manifest(room, content_hash):
    (room / "_key" / "manifest.json").write_text(
        json.dumps(
            {"room": "Project Testbed", "content_hash": content_hash, "documents": 1, "findings": 1, "built": "2026-08-24"}
        ),
        encoding="utf-8",
    )


def write_adjudications(room, text):
    (room / "_key" / "adjudications.yaml").write_text(text, encoding="utf-8")


# --- provenance wired into the CLI --------------------------------------------


def test_cli_scores_and_marks_unverified_when_no_manifest_exists(room, capsys):
    out_path = write_output(room, documents=[SRC])
    code = main(["score", str(out_path), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 0
    assert "UNVERIFIED" in captured.out
    assert "ENV-1" in captured.out


def test_cli_refuses_and_prints_nothing_to_stdout_on_a_proven_mismatch(room, capsys):
    write_manifest(room, "abc123")
    out_path = write_output(room, room_hash="different-hash", documents=[SRC])
    code = main(["score", str(out_path), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 2
    assert "abc123" in captured.err
    assert "different-hash" in captured.err
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_cli_reports_verified_provenance_when_hashes_match(room, capsys):
    write_manifest(room, "matching-hash")
    out_path = write_output(room, room_hash="matching-hash", documents=[SRC])
    code = main(["score", str(out_path), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 0
    assert "Provenance: verified" in captured.out
    assert "matching-hash" in captured.out


# --- ordinary CLI failure modes ------------------------------------------------


def test_cli_missing_room_conf_exits_cleanly_without_a_traceback(tmp_path, capsys):
    out_path = tmp_path / "out.json"
    out_path.write_text(json.dumps({"tool": "acme", "findings": []}), encoding="utf-8")
    code = main(["score", str(out_path), "--room", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "room.conf" in captured.err
    assert "Traceback" not in captured.err


def test_cli_missing_tool_output_file_exits_cleanly_without_a_traceback(room, capsys):
    code = main(["score", str(room / "does-not-exist.json"), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err


def test_cli_malformed_tool_output_json_exits_cleanly_without_a_traceback(room, capsys):
    bad = room / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    code = main(["score", str(bad), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err


def test_cli_with_no_subcommand_is_a_controlled_argparse_exit():
    with pytest.raises(SystemExit):
        main([])


# --- baseline diff ---------------------------------------------------------------


def test_cli_baseline_diff_reports_the_change(room, capsys):
    current = write_output(room, name="current.json", documents=[SRC])
    baseline = write_output(room, name="baseline.json", documents=[])
    code = main(["score", str(current), "--room", str(room), "--baseline", str(baseline)])
    captured = capsys.readouterr()
    assert code == 0
    assert "Scorecard diff" in captured.out
    assert "Newly found" in captured.out
    assert "ENV-1" in captured.out


def test_cli_baseline_file_missing_exits_cleanly_without_a_traceback(room, capsys):
    current = write_output(room, name="current.json", documents=[SRC])
    code = main(["score", str(current), "--room", str(room), "--baseline", str(room / "ghost.json")])
    captured = capsys.readouterr()
    assert code == 2
    assert "Traceback" not in captured.err


# --- module resolution: this file must not disturb `python3 -m synthvdr.qa` --


def test_python_dash_m_synthvdr_score_works_as_a_subprocess(room):
    out_path = write_output(room, documents=[SRC])
    result = subprocess.run(
        [sys.executable, "-m", "synthvdr", "score", str(out_path), "--room", str(room)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "ENV-1" in result.stdout
    assert "Traceback" not in result.stderr


def test_python_dash_m_synthvdr_dot_qa_still_runs_the_qa_cli_unchanged(room):
    result = subprocess.run(
        [sys.executable, "-m", "synthvdr.qa", "--room", str(room)],
        capture_output=True,
        text=True,
    )
    # This room fixture is minimal (no data-room/ tree, no flagged twin), so
    # the QA gates themselves are expected to fail or skip — the point of
    # this test is only that `-m synthvdr.qa` still resolves to the QA CLI,
    # not the score CLI, and does so without a module-resolution traceback.
    assert "Traceback" not in result.stderr
    assert "QA check" in result.stdout
    assert "Scorecard" not in result.stdout


# --- adjudications: auto-loaded from the room, no flag ------------------------


def test_cli_reports_no_adjudications_file_distinctly_from_zero_applied(room, capsys):
    out_path = write_output(room, documents=[SRC])
    code = main(["score", str(out_path), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 0
    assert "Adjudications:" in captured.out
    assert "no " in captured.out and "adjudications.yaml" in captured.out


def test_cli_applies_adjudications_from_the_room_and_reports_the_count(room, capsys):
    write_adjudications(
        room,
        "adjudications:\n  - tool_index: 1\n    finding_id: EMP-2\n    reason: \"clear\"\n",
    )
    out_path = write_multi_output(
        room,
        "out.json",
        [
            {"title": "t1", "severity": "critical", "documents": [SRC], "summary": "s"},
            {"title": "t2", "severity": "medium", "documents": [], "summary": "misclassified contractors"},
        ],
    )
    code = main(["score", str(out_path), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 0
    assert "1 adjudication(s) applied" in captured.out
    assert "| EMP-2 | medium | hit |" in captured.out


def test_cli_refuses_an_adjudication_with_an_out_of_range_tool_index(room, capsys):
    write_adjudications(
        room,
        "adjudications:\n  - tool_index: 5\n    finding_id: EMP-2\n    reason: \"bad index\"\n",
    )
    out_path = write_output(room, documents=[SRC])
    code = main(["score", str(out_path), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 2
    assert "5" in captured.err
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_cli_refuses_an_adjudication_naming_an_unknown_finding_id(room, capsys):
    write_adjudications(
        room,
        "adjudications:\n  - tool_index: 0\n    finding_id: NOPE-9\n    reason: \"unknown\"\n",
    )
    out_path = write_output(room, documents=[SRC])
    code = main(["score", str(out_path), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 2
    assert "NOPE-9" in captured.err
    assert captured.out == ""


def test_cli_refuses_a_malformed_adjudications_file(room, capsys):
    write_adjudications(room, "not: [a, valid: shape\n")
    out_path = write_output(room, documents=[SRC])
    code = main(["score", str(out_path), "--room", str(room)])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "Traceback" not in captured.err
