import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from synthvdr.qa import ALL_GATES
from synthvdr.qa.runner import GateContext, run_gates
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import load_distractors, load_findings
from synthvdr.score import load_tool_output, score

REPO_ROOT = Path(__file__).resolve().parent.parent


def ctx_for(room, strict=False):
    conf = load_room_conf(room / "room.conf")
    return GateContext(
        room=room,
        conf=conf,
        findings=load_findings(room / "_key" / "findings.yaml"),
        distractors=load_distractors(room / "_key" / "distractors.yaml"),
        strict=strict,
    )


def test_a_freshly_built_fixture_room_passes_every_gate(xs_room, capsys):
    assert run_gates(ctx_for(xs_room), ALL_GATES) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out


def test_strict_mode_is_clean_except_for_optional_renders(xs_room, capsys):
    run_gates(ctx_for(xs_room, strict=True), ALL_GATES)
    out = capsys.readouterr().out
    skipped = [line for line in out.splitlines() if line.startswith("SKIP")]
    assert all("render" in line for line in skipped), skipped


def test_the_cli_runs_against_the_fixture(xs_room):
    result = subprocess.run(
        [sys.executable, "-m", "synthvdr.qa", "--room", str(xs_room)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_repeat_builds_are_byte_identical(xs_room, build_xs_room, tmp_path):
    second = build_xs_room(tmp_path / "second")
    for original in sorted((xs_room / "data-room").rglob("*.md")):
        rel = original.relative_to(xs_room / "data-room")
        assert original.read_bytes() == (second / "data-room" / rel).read_bytes()


def test_build_is_byte_identical_across_processes_with_different_hash_seeds(tmp_path):
    """A same-process comparison (the previous test) shares one interpreter's
    PYTHONHASHSEED for both builds, so it cannot see a set/dict-ordering
    dependence — Python salts hash() once per process, not once per call. This
    drives the build from two SEPARATE interpreter processes under two
    different, explicit seeds and diffs every file the build produces, which
    is the only way to actually observe that class of nondeterminism.
    """
    script = (
        "import sys; from pathlib import Path; "
        "from tests.conftest import build_fixture_room; "
        "build_fixture_room(Path(sys.argv[1]))"
    )
    dest_a = tmp_path / "seed-a"
    dest_b = tmp_path / "seed-b"
    for dest, seed in ((dest_a, "0"), (dest_b, "4294967295")):
        result = subprocess.run(
            [sys.executable, "-c", script, str(dest)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert result.returncode == 0, result.stdout + result.stderr

    files_a = sorted(p.relative_to(dest_a) for p in dest_a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(dest_b) for p in dest_b.rglob("*") if p.is_file())
    assert files_a == files_b, "the two hash-seeded builds produced different file sets"
    for rel in files_a:
        assert (dest_a / rel).read_bytes() == (dest_b / rel).read_bytes(), (
            f"{rel} differs between PYTHONHASHSEED=0 and PYTHONHASHSEED=4294967295"
        )


# The gates must be load-bearing. Each of these breaks the room in one specific
# way and asserts that the gate written to catch it does.

def test_a_leaked_annotation_string_fails_gate_3(xs_room):
    doc = sorted((xs_room / "data-room").rglob("*.md"))[0]
    conf = load_room_conf(xs_room / "room.conf")
    doc.write_text(doc.read_text() + f"\n## {conf.get('FLAG_STRING_1')}\n\n- leaked\n")
    assert run_gates(ctx_for(xs_room), ALL_GATES) == 1


def test_a_deleted_annotation_block_fails_gate_8_even_though_gate_7_passes(xs_room):
    from synthvdr.qa.structural import gate_07_twin_diff, gate_08_carrier_census

    conf = load_room_conf(xs_room / "room.conf")
    findings = load_findings(xs_room / "_key" / "findings.yaml")
    rel = findings.findings[0].source
    flagged = xs_room / conf.get("FLAGGED_TREE") / rel
    flagged.write_text((xs_room / conf.get("BLIND_TREE") / rel).read_text())
    ctx = ctx_for(xs_room)
    assert gate_07_twin_diff(ctx).status == "PASS"
    assert gate_08_carrier_census(ctx).status == "FAIL"


def test_a_hand_edited_index_fails_gate_1(xs_room):
    index = xs_room / "index.md"
    index.write_text(index.read_text() + "\n- 99.9.9 Sneaky extra entry\n")
    assert run_gates(ctx_for(xs_room), ALL_GATES) == 1


def test_a_superseded_figure_fails_gate_13(xs_room):
    from synthvdr.qa.integrity import gate_13_fact_sheet, parse_canonical_figures

    fact_sheet = (xs_room / "_key" / "fact-sheet.md").read_text()
    superseded = next(f.superseded[0] for f in parse_canonical_figures(fact_sheet) if f.superseded)
    doc = sorted((xs_room / "data-room").rglob("*.md"))[0]
    doc.write_text(doc.read_text() + f"\n\nThe figure was previously {superseded}.\n")
    assert gate_13_fact_sheet(ctx_for(xs_room)).status == "FAIL"


def test_a_dangling_cross_link_fails_gate_17(xs_room):
    # Task 20 fix round 1, D2: synthvdr.schema.validate() used to run nowhere
    # in the pipeline, only as a manual /vdr-findings step — a findings.yaml
    # with a cross_links entry pointing nowhere loaded and passed every other
    # gate cleanly. gate_17_answer_key_validation closes that gap.
    import yaml

    path = xs_room / "_key" / "findings.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["findings"][0]["cross_links"] = ["NO-SUCH-FINDING"]
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert run_gates(ctx_for(xs_room), ALL_GATES) == 1


def test_an_unaudited_finding_fails_gate_15(xs_room):
    import yaml

    path = xs_room / "_key" / "findings.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["findings"][0].pop("discoverable_from_blind", None)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert run_gates(ctx_for(xs_room), ALL_GATES) == 1


def test_scoring_the_sample_output_gives_the_expected_scorecard(xs_room):
    findings = load_findings(xs_room / "_key" / "findings.yaml")
    distractors = load_distractors(xs_room / "_key" / "distractors.yaml")
    output = load_tool_output(xs_room / "tool-output-sample.json")
    card = score(output, findings, distractors)
    assert card.recall == 0.75
    assert card.precision == 0.75
    assert card.false_alarms == ["DX-1"]
    assert len(card.misses) == 1
