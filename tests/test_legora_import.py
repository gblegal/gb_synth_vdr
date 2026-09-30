"""Tests for synthvdr.legora_import — a Legora review, made scoreable."""

from __future__ import annotations

import pytest

from synthvdr.legora_import import (
    citations_in,
    cut_documents,
    normalise_citations,
    normalise_path,
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
