"""Tests for synthvdr.legora_import — a Legora review, made scoreable."""

from __future__ import annotations

import pytest

from synthvdr.legora_import import (
    LegoraImportError,
    citations_in,
    cut_documents,
    normalise_citations,
    normalise_path,
    read_report,
    split_report,
    tool_name,
)

SECTIONS = ["01_corporate", "05_commercial", "11_environmental-hs"]
DOC = "01_corporate/1.1_constitutional/1.1.1_constitutional-01.md"


@pytest.mark.parametrize(
    "raw",
    [
        DOC,
        f"data-room/{DOC}",
        f"subset/{DOC}",
        "data-room-pdf/01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf",
        "data-room-docx/01_corporate/1.1_constitutional/1.1.1_constitutional-01.docx",
        "/workspace/documents/projects/Quern subset 0.20.0/subset/"
        "01_corporate/1.1_constitutional/1.1.1_constitutional-01.PDF",
        f"./{DOC}",
        "01_corporate\\1.1_constitutional\\1.1.1_constitutional-01.md",
        f"  {DOC}  ",
    ],
)
def test_normalise_path_cuts_at_the_first_section_folder_and_maps_renders_back_to_md(raw):
    assert normalise_path(raw, SECTIONS) == DOC


def test_normalise_path_leaves_a_path_outside_every_section_folder_as_written():
    assert normalise_path("_review/review-quern.md", SECTIONS) == "_review/review-quern.md"


def test_normalise_citations_rewrites_backticked_and_bare_paths_and_leaves_other_code_alone():
    text = (
        "Severity: `high`\n"
        "- `/workspace/documents/projects/Quern pdf/data-room-pdf/"
        "01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf`\n"
        "- see 05_commercial/5.1_customer-contracts/5.1.1_customer-contracts-01.docx for the clause\n"
    )
    out = normalise_citations(text, SECTIONS)
    assert "Severity: `high`" in out
    assert f"- `{DOC}`" in out
    assert "see `05_commercial/5.1_customer-contracts/5.1.1_customer-contracts-01.md` for" in out


def test_a_bare_path_ending_a_sentence_is_still_a_citation():
    # Word drops backticks, and a path at the end of a sentence carries its full stop.
    text = "The consent is recorded at 01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf."
    assert citations_in(text, SECTIONS) == [DOC]
    assert normalise_citations(text, SECTIONS).endswith(f"`{DOC}`.")


def test_a_bare_file_name_in_prose_is_not_a_citation_but_a_backticked_one_is():
    # "README.md" in a sentence is prose; a backticked name is an attempt to cite,
    # and must reach the room check so a path without its folders is refused.
    text = "Saved beside README.md, citing `1.1.1_constitutional-01.md`."
    assert citations_in(text, SECTIONS) == ["1.1.1_constitutional-01.md"]
    assert "beside README.md," in normalise_citations(text, SECTIONS)


def test_citations_in_lists_every_document_in_order_normalised():
    text = (
        f"`{DOC}` then 11_environmental-hs/11.1_permits/11.1.1_permits-01.pdf "
        "and `not a path` and `_review/old.md`"
    )
    assert citations_in(text, SECTIONS) == [
        DOC,
        "11_environmental-hs/11.1_permits/11.1.1_permits-01.md",
        "_review/old.md",
    ]


def test_cut_documents_maps_renders_to_md_and_skips_dotfiles(tmp_path):
    cut = tmp_path / "data-room-pdf"
    folder = cut / "01_corporate" / "1.1_constitutional"
    folder.mkdir(parents=True)
    (folder / "1.1.1_constitutional-01.pdf").write_bytes(b"%PDF")
    (cut / ".synthvdr-subset").write_text("marker")
    (cut / "01_corporate" / ".DS_Store").write_bytes(b"")
    assert cut_documents(cut) == {DOC}


def test_split_report_cuts_the_files_read_list_away_from_the_issues():
    report = (
        "# Review of Testbed\n\nModel: not known\n"
        "Skill: Built from synth-vdr 0.20.0 · domain pack ma\n\n"
        "## First issue\n\nSeverity: high\n\nDocuments:\n- `01_corporate/x.md`\n\n"
        "# Files read\n\n- `01_corporate/x.md`\n- `05_commercial/y.md`\n"
    )
    split = split_report(report)
    assert split.issues.startswith("## First issue")
    assert "Files read" not in split.issues and "05_commercial/y.md" not in split.issues
    assert "05_commercial/y.md" in split.files_read
    assert split.preamble.startswith("# Review of Testbed")
    assert split.model == "not known"
    assert split.skill == "Built from synth-vdr 0.20.0 · domain pack ma"


@pytest.mark.parametrize("heading", ["# Files read", "## Files read", "# Files Read:", "### files read"])
def test_split_report_finds_the_files_read_heading_at_any_level_and_case(heading):
    split = split_report(f"## Issue\n\nSeverity: low\n\n{heading}\n\n- `01_corporate/x.md`\n")
    assert split.files_read is not None and "01_corporate/x.md" in split.files_read
    assert "01_corporate/x.md" not in split.issues


def test_a_level_two_files_read_section_ends_at_the_next_issue():
    split = split_report("## Files read\n\n- `01_corporate/x.md`\n\n## Issue\n\nSeverity: low\n")
    assert split.issues.startswith("## Issue")
    assert "01_corporate/x.md" not in split.issues


def test_split_report_without_a_files_read_section_says_so():
    assert split_report("## Issue\n\nSeverity: low\n").files_read is None


def test_split_report_reads_bold_and_bulleted_field_lines():
    split = split_report(
        "# Review\n\n**Model:** claude-opus-5-5\n- **Skill**: Built from synth-vdr 0.20.0\n\n"
        "## Issue\n\nSeverity: low\n"
    )
    assert split.model == "claude-opus-5-5"
    assert split.skill == "Built from synth-vdr 0.20.0"


def test_read_report_strips_a_bom_and_windows_line_endings(tmp_path):
    path = tmp_path / "review.md"
    path.write_bytes(
        "\ufeff## Issue\r\n\r\nSeverity: high\r\n\r\n# Files read\r\n\r\n- `01_corporate/x.md`\r\n".encode(
            "utf-8"
        )
    )
    text, notes = read_report(path)
    assert text.startswith("## Issue\n") and "\r" not in text
    assert notes == []
    split = split_report(text)
    assert split.issues.startswith("## Issue") and split.files_read is not None


def test_read_report_turns_word_headings_and_lists_into_markdown(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Review of Testbed", level=0)
    document.add_paragraph("Model: not known")
    document.add_heading("Consent not obtained", level=2)
    document.add_paragraph("Severity: critical")
    document.add_paragraph(
        "data-room-pdf/01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf", style="List Bullet"
    )
    document.add_heading("Files read", level=1)
    document.add_paragraph(
        "01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf", style="List Bullet"
    )
    document.add_table(rows=1, cols=2)
    path = tmp_path / "review.docx"
    document.save(str(path))
    text, notes = read_report(path)
    lines = text.splitlines()
    assert "# Review of Testbed" in lines
    assert "## Consent not obtained" in lines
    assert "- data-room-pdf/01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf" in lines
    assert "# Files read" in lines
    assert len(notes) == 1 and "1 table" in notes[0]


def test_read_report_refuses_anything_but_markdown_or_word(tmp_path):
    path = tmp_path / "review.txt"
    path.write_text("## Issue\n")
    with pytest.raises(LegoraImportError, match="Markdown"):
        read_report(path)


@pytest.mark.parametrize(
    "model, skill, override, expected",
    [
        ("not known", "", None, "legora/model-not-stated"),
        ("", "", None, "legora/model-not-stated"),
        ("Not known.", "", None, "legora/model-not-stated"),
        ("claude-sonnet-5-5", "", None, "legora/claude-sonnet-5-5"),
        (
            "not known",
            "Built from synth-vdr 0.20.0 · domain pack ma",
            None,
            "legora/model-not-stated (Built from synth-vdr 0.20.0 · domain pack ma)",
        ),
        ("claude-sonnet-5-5", "Built from x", "legora/claude-opus-5-5", "legora/claude-opus-5-5"),
    ],
)
def test_tool_name(model, skill, override, expected):
    assert tool_name(model, skill, override) == expected
