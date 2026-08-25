import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from synthvdr.render.docx import RenderUnavailable, render_tree_docx, rotation_for, scanned_slots
from synthvdr.schema import Finding, FindingSet

PDF_MJS = Path(__file__).resolve().parent.parent / "synthvdr" / "render" / "pdf.mjs"

# 22 (slot_id, page) pairs chosen to exercise both signs and enough distinct
# slots/pages to catch a sign-bit or index error, not just agreement on one
# lucky value. rotation_for(...) over this set yields 11 positive, 11
# negative, 20 distinct values (two ties) — see
# test_pdf_mjs_rotation_matches_python_exactly below.
ROTATION_PAIRS = [
    (slot, page)
    for slot in (
        "11.1.1", "2.2.2", "9.9.9", "a", "b",
        "01_corp/1.1_x/1.1.1_other", "11_env/11.1_x/11.1.1_report",
        "3.3.3", "4.4.4", "zulu", "alpha",
    )
    for page in (1, 2)
]

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


def _extract_rotation_for_source(mjs_text: str) -> str:
    """Pull rotationFor's actual function body out of pdf.mjs, rather than
    letting the test carry its own copy of the formula. A test with its own
    copy proves the two authors agree, not that the shipped file matches —
    this one breaks the moment pdf.mjs's implementation drifts from
    synthvdr.render.docx.rotation_for, because it runs the real source."""
    match = re.search(r"function rotationFor\([^)]*\)\s*\{.*?\n\}", mjs_text, re.DOTALL)
    if not match:
        raise AssertionError(
            "could not find `function rotationFor(...)` in synthvdr/render/pdf.mjs "
            "— has it been renamed or restructured? update the extraction regex"
        )
    return match.group(0)


def _extract_crypto_import(mjs_text: str) -> str:
    match = re.search(r"^import .*createHash.*$", mjs_text, re.MULTILINE)
    if not match:
        raise AssertionError(
            "could not find the createHash import in synthvdr/render/pdf.mjs"
        )
    return match.group(0)


def _run_node_rotation(node: str, pairs):
    mjs_text = PDF_MJS.read_text(encoding="utf-8")
    import_line = _extract_crypto_import(mjs_text)
    fn_source = _extract_rotation_for_source(mjs_text)
    script = (
        f"{import_line}\n"
        f"{fn_source}\n"
        f"const pairs = {json.dumps(pairs)};\n"
        "console.log(JSON.stringify(pairs.map(([slot, page]) => rotationFor(slot, page))));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's rotationFor: {proc.stderr}")
    return json.loads(proc.stdout.strip())


def test_pdf_mjs_rotation_matches_python_exactly():
    """pdf.mjs's rotationFor is a hand-written JS port of
    synthvdr.render.docx.rotation_for, kept as two separate implementations
    because one is Python (DOCX path) and one is Node (PDF path). Nothing
    else in the harness compares them, so a scanned PDF and a DOCX render of
    the same slot could silently rotate differently if the port ever
    drifts — this is the only check that would catch it.

    python-docx being installed doesn't help here: this needs `node`, not
    `docx`, so it SKIPs (never silently passes) if node is unavailable,
    same SKIP discipline as every gate in this project.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — cross-language rotation parity unverified")

    js_values = _run_node_rotation(node, ROTATION_PAIRS)
    py_values = [rotation_for(slot, page) for slot, page in ROTATION_PAIRS]

    assert js_values == py_values
    assert any(v > 0 for v in py_values), "fixture pairs must cover the positive case"
    assert any(v < 0 for v in py_values), "fixture pairs must cover the negative case"
