"""Tests for synthvdr.export_key and render_distractors_md — a room's key, for a
Legora project of its own."""

from __future__ import annotations

import pytest

from synthvdr.__main__ import main
from synthvdr.export_key import ExportKeyError, export_key
from synthvdr.schema import Distractor, load_distractors, load_findings, render_distractors_md


def test_render_distractors_md_names_each_location_resolution_and_imitated_finding():
    text = render_distractors_md(
        [
            Distractor("DX-1", "Closed VAT enquiry", "03_tax/a.md", "03_tax/b.md", "FIN-1"),
            Distractor("DX-2", "Old facility", "04_x/c.md", "04_x/d.md", None),
        ],
        "Project Testbed",
    )
    assert text.startswith("# Project Testbed — distractors\n")
    assert "Never fed to a tool under test" in text
    assert "## DX-1 — Closed VAT enquiry" in text
    assert "- **Location:** `03_tax/a.md`" in text
    assert "- **Resolution:** `03_tax/b.md`" in text
    assert "- **Imitates:** FIN-1" in text
    assert "- **Imitates:** —" in text
    assert text.endswith("\n")


def test_render_distractors_md_says_so_when_there_are_none():
    assert "No distractors are planted in this room." in render_distractors_md([], "Project Testbed")


def test_export_key_writes_the_key_and_only_the_flagged_documents_it_needs(xs_room, tmp_path):
    out = tmp_path / "testbed-key"
    report = export_key(xs_room, out)
    assert (out / "findings.md").read_text(encoding="utf-8").startswith("# Project Testbed — answer key")
    assert "## DX-1" in (out / "distractors.md").read_text(encoding="utf-8")
    flagged = sorted(
        p.relative_to(out / "flagged").as_posix() for p in (out / "flagged").rglob("*") if p.is_file()
    )
    findings = load_findings(xs_room / "_key" / "findings.yaml")
    distractors = load_distractors(xs_room / "_key" / "distractors.yaml")
    expected = sorted(
        findings.all_evidence_paths()
        | {d.location for d in distractors}
        | {d.resolution for d in distractors}
    )
    assert flagged == expected
    assert report.flagged == len(expected) == 10
    assert report.findings == 4 and report.distractors == 2
    carrier = out / "flagged" / "01_corporate/1.1_constitutional/1.1.1_constitutional-01.md"
    assert "Key diligence points" in carrier.read_text(encoding="utf-8")
    assert not (out / "flagged" / "09_employment").exists()


def test_export_key_refuses_a_folder_that_already_exists(xs_room, tmp_path):
    out = tmp_path / "existing"
    out.mkdir()
    with pytest.raises(ExportKeyError, match="already exists"):
        export_key(xs_room, out)


@pytest.mark.parametrize("inside", ["data-room/export", "_key/export", "_key/flagged/export"])
def test_export_key_refuses_a_folder_inside_a_room_tree(xs_room, inside):
    with pytest.raises(ExportKeyError, match="inside"):
        export_key(xs_room, xs_room / inside)
    assert not (xs_room / inside).exists()


def test_export_key_cli(xs_room, tmp_path, capsys):
    out = tmp_path / "key"
    assert main(["export-key", "--room", str(xs_room), "--out", str(out)]) == 0
    assert "only this folder" in capsys.readouterr().out
    assert main(["export-key", "--room", str(xs_room), "--out", str(out)]) == 2
    assert "already exists" in capsys.readouterr().err
