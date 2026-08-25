import subprocess
import sys

import pytest

from synthvdr.render.docx import RenderUnavailable, render_tree_docx, rotation_for, scanned_slots
from synthvdr.schema import Finding, FindingSet

docx_module = pytest.importorskip("docx", reason="python-docx not installed")


def findings():
    return FindingSet(
        [
            Finding(
                id="ENV-1", title="a", severity="critical", workstream="environmental",
                multi_document=False, source="11_env/11.1_x/11.1.1_report.md",
                location="x", substance="s",
            )
        ],
        "Project Testbed",
    )


def two_findings():
    return [
        Finding(
            id="ENV-1", title="a", severity="critical", workstream="environmental",
            multi_document=False, source="11_env/11.1_x/11.1.1_report.md",
            location="x", substance="s",
        ),
        Finding(
            id="CORP-1", title="b", severity="high", workstream="corporate",
            multi_document=False, source="01_corp/1.1_x/1.1.1_other.md",
            location="y", substance="t",
        ),
    ]


def test_rotation_is_deterministic_and_in_range():
    first = rotation_for("11.1.1", 1)
    assert first == rotation_for("11.1.1", 1)
    assert 0.4 <= abs(first) <= 1.1


def test_rotation_varies_by_page():
    assert rotation_for("11.1.1", 1) != rotation_for("11.1.1", 2)


def test_rotation_varies_by_slot():
    """A constant-angle implementation would be deterministic but useless —
    it must actually depend on which slot is being rotated, not just page."""
    assert rotation_for("11.1.1", 1) != rotation_for("2.2.2", 1)


def test_rotation_is_never_zero_and_stays_in_band():
    for slot, page in [("11.1.1", 1), ("2.2.2", 3), ("9.9.9", 42), ("a", 1), ("b", 1)]:
        angle = rotation_for(slot, page)
        assert 0.4 <= abs(angle) <= 1.1


def test_rotation_deterministic_across_processes_with_different_hash_seed():
    """sha256 does not depend on PYTHONHASHSEED, but Python's built-in
    hash() of strings does — the failure mode this guards against is a
    same-process test passing while a bare hash()-based implementation
    would silently vary from run to run. Exercise it across two real
    subprocesses with different seeds to actually catch that."""
    script = (
        "from synthvdr.render.docx import rotation_for; "
        "print(rotation_for('11.1.1', 1), rotation_for('2.2.2', 7))"
    )
    results = set()
    for seed in ("0", "1", "12345"):
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": seed, "PATH": __import__("os").environ.get("PATH", "")},
        )
        results.add(proc.stdout.strip())
    assert len(results) == 1


def test_scanned_slots_prefer_evidence_documents():
    slots = scanned_slots(findings(), count=1)
    assert slots == ["11_env/11.1_x/11.1.1_report.md"]


def test_scanned_slots_are_deterministic():
    assert scanned_slots(findings(), count=1) == scanned_slots(findings(), count=1)


def test_scanned_slots_stable_across_reordering():
    """The set of findings, not the order they were listed in, determines
    the chosen slots — a findings.yaml re-save that shuffles entries must
    not change which documents get scanned."""
    rows = two_findings()
    forward = FindingSet(rows, "Project Testbed")
    backward = FindingSet(list(reversed(rows)), "Project Testbed")
    assert scanned_slots(forward, count=2) == scanned_slots(backward, count=2)
    assert scanned_slots(forward, count=1) == scanned_slots(backward, count=1)


def test_scanned_slots_deterministic_across_processes_with_different_hash_seed():
    script = (
        "from synthvdr.schema import Finding, FindingSet\n"
        "from synthvdr.render.docx import scanned_slots\n"
        "fs = FindingSet([\n"
        "    Finding(id='ENV-1', title='a', severity='critical', workstream='environmental',\n"
        "            multi_document=False, source='11_env/11.1_x/11.1.1_report.md',\n"
        "            location='x', substance='s'),\n"
        "    Finding(id='CORP-1', title='b', severity='high', workstream='corporate',\n"
        "            multi_document=False, source='01_corp/1.1_x/1.1.1_other.md',\n"
        "            location='y', substance='t'),\n"
        "], 'Project Testbed')\n"
        "print(scanned_slots(fs, count=2))\n"
    )
    results = set()
    for seed in ("0", "1", "999"):
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": seed, "PATH": __import__("os").environ.get("PATH", "")},
        )
        results.add(proc.stdout.strip())
    assert len(results) == 1


def test_renders_every_markdown_file(tmp_path):
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_articles.md").write_text("# Articles\n\nA paragraph.\n")
    out = tmp_path / "data-room-docx"
    assert render_tree_docx(tmp_path / "data-room", out) == 1
    assert (out / "01_corporate" / "1.1.1_articles.docx").is_file()


def test_render_tree_docx_is_non_destructive(tmp_path):
    """render_tree_docx must never delete anything — not even files it did
    not write. Stale renders are gate 16's problem, not this writer's."""
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_articles.md").write_text("# Articles\n")
    out = tmp_path / "data-room-docx"
    out.mkdir(parents=True)
    unrelated = out / "01_corporate"
    unrelated.mkdir(parents=True)
    sentinel = unrelated / "not_mine.txt"
    sentinel.write_text("do not touch")

    render_tree_docx(tmp_path / "data-room", out)

    assert sentinel.is_file()
    assert sentinel.read_text() == "do not touch"


def test_render_tree_docx_is_idempotent(tmp_path):
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_articles.md").write_text("# Articles\n")
    out = tmp_path / "data-room-docx"
    first = render_tree_docx(tmp_path / "data-room", out)
    second = render_tree_docx(tmp_path / "data-room", out)
    assert first == second == 1
    assert (out / "01_corporate" / "1.1.1_articles.docx").is_file()


def test_render_unavailable_when_docx_import_fails(tmp_path, monkeypatch):
    """python-docx IS installed in this environment, so the RenderUnavailable
    branch can only be exercised by simulating its absence — patch it out
    of sys.modules so `from docx import Document` raises ImportError, the
    same failure mode a machine without python-docx installed would hit."""
    monkeypatch.setitem(sys.modules, "docx", None)
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_articles.md").write_text("# Articles\n")
    with pytest.raises(RenderUnavailable):
        render_tree_docx(tmp_path / "data-room", tmp_path / "data-room-docx")
