"""Tests for synthvdr.legora_import — a Legora review, made scoreable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synthvdr.__main__ import main
from synthvdr.legora_import import (
    LegoraImportError,
    citations_in,
    cut_documents,
    import_review,
    normalise_citations,
    normalise_path,
    read_report,
    render_summary,
    split_report,
    tool_name,
    write_output,
)
from synthvdr.manifest import MANIFEST_NAME, build_room_manifest, compute_content_hash, write_manifest
from synthvdr.schema import load_distractors, load_findings
from synthvdr.score import check_provenance, load_tool_output, score

ROOT = Path(__file__).resolve().parent.parent

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


# Paths in the XS room `xs_room` (tests/conftest.py) builds: 40 documents, one per
# subsection folder, and a 10-document subset holding every finding's evidence.
CORP = "01_corporate/1.1_constitutional/1.1.1_constitutional-01.md"        # CORP-1's source
COMM = "05_commercial/5.1_customer-contracts/5.1.1_customer-contracts-01.md"  # COMM-1's source
COMM_2 = "05_commercial/5.2_supplier-contracts/5.2.1_supplier-contracts-01.md"  # its corroboration
TAX = "03_tax/3.1_corporation-tax/3.1.1_corporation-tax-01.md"             # DX-1's location; not in the subset
MOUNT = "/workspace/documents/projects/Testbed pdf/data-room-pdf"


def pdf(rel: str) -> str:
    return f"{MOUNT}/{rel[:-3]}.pdf"


def add_pdf_cut(room: Path) -> None:
    blind = room / "data-room"
    for md in blind.rglob("*.md"):
        target = room / "data-room-pdf" / md.relative_to(blind).with_suffix(".pdf")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4 stand-in")


def add_manifest(room: Path) -> str:
    content_hash, documents = compute_content_hash(room / "data-room")
    manifest = build_room_manifest("Project Testbed", content_hash, documents, 4, "2026-09-30")
    write_manifest(room / "_key" / MANIFEST_NAME, manifest)
    return content_hash


def report(*issues, files_read=None, preamble="# Review of Project Testbed\n\nModel: not known\n"):
    parts = [preamble]
    for title, severity, documents in issues:
        parts.append(
            f"## {title}\n\nSeverity: {severity}\n\nThe issue.\n\nDocuments:\n"
            + "".join(f"- `{d}`\n" for d in documents)
        )
    if files_read is not None:
        parts.append("# Files read\n\n" + "".join(f"- `{d}`\n" for d in files_read))
    return "\n".join(parts)


def write_report(room: Path, text: str, name: str = "review.md") -> Path:
    path = room.parent / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_pdf_run_imports_onto_the_room_and_scores_as_if_it_cited_the_md_paths(xs_room, tmp_path):
    add_pdf_cut(xs_room)
    content_hash = add_manifest(xs_room)
    text = report(
        ("Consent gap", "critical", [pdf(CORP)]),
        ("Pricing term not mirrored", "medium", [pdf(COMM), pdf(COMM_2)]),
        files_read=[pdf(CORP), pdf(COMM), pdf(COMM_2)],
    )
    result = import_review(write_report(xs_room, text), xs_room, cut="data-room-pdf")
    assert result.output["room_hash"] == content_hash
    assert [f["documents"] for f in result.output["findings"]] == [[CORP], [COMM, COMM_2]]

    out = tmp_path / "legora.json"
    write_output(result, out)
    output = load_tool_output(out)
    assert check_provenance(xs_room, output).verified
    card = score(
        output,
        load_findings(xs_room / "_key" / "findings.yaml"),
        load_distractors(xs_room / "_key" / "distractors.yaml"),
    )
    assert card.recall == 0.5
    assert card.misses == ["ENV-1", "FIN-1"]


def test_the_files_read_list_never_becomes_a_finding(xs_room):
    everything = sorted(cut_documents(xs_room / "data-room"))
    text = report(("Consent gap", "critical", [CORP]), files_read=everything)
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.findings == 1
    assert result.output["findings"][0]["documents"] == [CORP]
    assert result.files_read == 40 and result.not_read == 0


def test_a_path_the_cut_does_not_hold_stops_the_import_and_every_one_is_named(xs_room):
    text = report(
        ("Gap", "high", [CORP, "01_corporate/1.1_constitutional/1.1.9_constitutional-09.md"]),
        files_read=[CORP, "05_commercial/5.9_nowhere/5.9.1_nowhere-01.md"],
    )
    with pytest.raises(LegoraImportError) as caught:
        import_review(write_report(xs_room, text), xs_room)
    message = str(caught.value)
    assert "1.1.9_constitutional-09.md" in message and "5.9.1_nowhere-01.md" in message
    assert "--cut" in message and "--drop-unknown" in message


def test_a_subset_run_citing_a_document_outside_the_subset_is_refused(xs_room):
    text = report(("Closed enquiry", "low", [TAX]))
    with pytest.raises(LegoraImportError, match="3.1.1_corporation-tax-01.md"):
        import_review(write_report(xs_room, text), xs_room, cut="subset")
    full = import_review(write_report(xs_room, text), xs_room)
    assert full.output["findings"][0]["documents"] == [TAX]


def test_drop_unknown_imports_the_rest_lists_what_it_dropped_and_withholds_the_stamp(xs_room):
    add_manifest(xs_room)
    bad = "01_corporate/1.1_constitutional/1.1.9_constitutional-09.md"
    result = import_review(
        write_report(xs_room, report(("Gap", "high", [CORP, bad]))), xs_room, drop_unknown=True
    )
    assert result.output["findings"][0]["documents"] == [CORP]
    assert result.dropped == [bad]
    assert result.output["room_hash"] == ""
    assert "not stamped" in result.provenance


def test_room_hash_is_left_empty_when_the_room_has_no_manifest(xs_room):
    result = import_review(write_report(xs_room, report(("Gap", "high", [CORP]))), xs_room)
    assert result.output["room_hash"] == ""
    assert "manifest.json" in result.provenance


def test_unread_documents_are_counted_and_the_ones_beside_read_ones_are_named(xs_room):
    extra = "01_corporate/1.1_constitutional/1.1.2_constitutional-02.md"
    (xs_room / "data-room" / extra).write_text("# Constitutional\n\nA second document.\n")
    text = report(("Gap", "high", [CORP, COMM]), files_read=[CORP])
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.files_read == 1
    assert result.not_read == 40
    assert result.unexplained == [extra]
    assert result.cited_not_read == [COMM]


def test_files_read_entries_outside_the_section_folders_are_ignored_not_refused(xs_room):
    text = report(("Gap", "high", [CORP]), files_read=[CORP, "_review/review-earlier.md"])
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.ignored == ["_review/review-earlier.md"]
    assert result.files_read == 1


def test_a_document_cited_twice_in_two_forms_is_cited_once(xs_room):
    text = report(("Gap", "high", [CORP, f"data-room/{CORP}", CORP[:-3] + ".pdf"]))
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.output["findings"][0]["documents"] == [CORP]


def test_a_report_without_a_files_read_section_is_imported_and_says_so(xs_room):
    result = import_review(write_report(xs_room, report(("Gap", "high", [CORP]))), xs_room)
    assert result.files_read is None
    assert "no '# Files read' section" in render_summary(result, xs_room.parent / "out.json")


def test_a_report_with_no_issue_headings_is_refused_in_the_hand_backs_terms(xs_room):
    with pytest.raises(LegoraImportError, match="'##' headings"):
        import_review(write_report(xs_room, "# Review\n\nNothing found.\n"), xs_room)


def test_bold_severity_lines_are_read(xs_room):
    text = f"## Gap\n\n**Severity:** Critical\n\nDocuments:\n- `{CORP}`\n"
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.output["findings"][0]["severity"] == "critical"


def test_a_missing_cut_folder_is_refused(xs_room):
    with pytest.raises(LegoraImportError, match="data-room-pdf"):
        import_review(
            write_report(xs_room, report(("Gap", "high", [CORP]))), xs_room, cut="data-room-pdf"
        )


def test_the_output_has_the_tool_output_schemas_shape(xs_room):
    schema = json.loads((ROOT / "schemas" / "tool-output.schema.json").read_text(encoding="utf-8"))
    text = report(("Gap", "high", [CORP]), ("Other", "low", [COMM]))
    output = import_review(write_report(xs_room, text), xs_room).output
    assert set(schema["required"]) <= set(output) <= set(schema["properties"])
    finding_schema = schema["definitions"]["finding"]
    for finding in output["findings"]:
        assert set(finding_schema["required"]) <= set(finding) <= set(finding_schema["properties"])
        assert finding["severity"] in finding_schema["properties"]["severity"]["enum"]
    assert output["tool"] == "legora/model-not-stated"


def test_the_summary_ends_with_the_score_command(xs_room, tmp_path):
    result = import_review(write_report(xs_room, report(("Gap", "high", [CORP]))), xs_room)
    out = tmp_path / "legora.json"
    summary = render_summary(result, out)
    assert summary.splitlines()[-1] == f"  python3 -m synthvdr score {out} --room {xs_room}"
    assert "critical 0, high 1, medium 0, low 0" in summary


def run_cli(*args):
    return main(["import-legora-review", *map(str, args)])


def test_cli_writes_the_tool_output_and_prints_the_score_command(xs_room, tmp_path, capsys):
    report_path = write_report(xs_room, report(("Gap", "high", [CORP]), files_read=[CORP]))
    out = tmp_path / "runs" / "legora.json"
    assert run_cli(report_path, "--room", xs_room, "--out", out) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["findings"][0]["documents"] == [CORP]
    assert "python3 -m synthvdr score" in capsys.readouterr().out


def test_cli_names_the_tool_when_told(xs_room, tmp_path):
    out = tmp_path / "legora.json"
    report_path = write_report(xs_room, report(("Gap", "high", [CORP])))
    assert run_cli(report_path, "--room", xs_room, "--out", out, "--tool", "legora/claude-opus-5-5") == 0
    assert json.loads(out.read_text(encoding="utf-8"))["tool"] == "legora/claude-opus-5-5"


def test_cli_refuses_an_unknown_path_writes_nothing_and_lists_it_on_its_own_line(xs_room, tmp_path, capsys):
    bad = "01_corporate/1.1_constitutional/1.1.9_constitutional-09.md"
    out = tmp_path / "legora.json"
    report_path = write_report(xs_room, report(("Gap", "high", [bad])))
    assert run_cli(report_path, "--room", xs_room, "--out", out) == 2
    assert not out.exists()
    assert f"  {bad}" in capsys.readouterr().err.splitlines()


@pytest.mark.parametrize("inside, cut", [("data-room", None), ("subset", "subset"), ("_key", None)])
def test_cli_refuses_an_out_path_inside_a_room_tree(xs_room, capsys, inside, cut):
    out = xs_room / inside / "legora.json"
    report_path = write_report(xs_room, report(("Gap", "high", [CORP])))
    args = [report_path, "--room", xs_room, "--out", out] + (["--cut", cut] if cut else [])
    assert run_cli(*args) == 2
    assert not out.exists()
    assert "inside" in capsys.readouterr().err


def test_cli_refuses_a_text_report(xs_room, tmp_path, capsys):
    path = xs_room.parent / "review.txt"
    path.write_text("## Gap\n\nSeverity: high\n")
    assert run_cli(path, "--room", xs_room, "--out", tmp_path / "o.json") == 2
    assert "Markdown" in capsys.readouterr().err


def test_cli_requires_out(xs_room, capsys):
    report_path = write_report(xs_room, report(("Gap", "high", [CORP])))
    with pytest.raises(SystemExit) as exited:
        run_cli(report_path, "--room", xs_room)
    assert exited.value.code == 2
    assert "required: --out" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Final-review fixes. Each test reproduces a finding shown on a real room.
# ---------------------------------------------------------------------------

def everything_in(room: Path):
    return sorted(cut_documents(room / "data-room"))


@pytest.mark.parametrize(
    "heading",
    [
        "# Files read (40 documents)",
        "# Files Reviewed",
        "# Documents read",
        "**Files read**",
        "# Appendix: Files read",
        "Files read:",
    ],
)
def test_a_files_read_list_under_a_variant_heading_never_reaches_the_last_issue(xs_room, heading):
    # Final review C1: any of these used to glue 1,000 paths to the last issue.
    text = report(("Consent gap", "critical", [CORP])) + f"\n{heading}\n\n" + "".join(
        f"- `{d}`\n" for d in everything_in(xs_room)
    )
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.findings == 1
    assert result.output["findings"][0]["documents"] == [CORP]
    assert result.files_read == 40


def test_two_files_read_lists_are_both_read_and_neither_joins_an_issue(xs_room):
    half = len(everything_in(xs_room)) // 2
    text = (
        report(("Consent gap", "critical", [CORP]))
        + "\n# Files read\n\n" + "".join(f"- `{d}`\n" for d in everything_in(xs_room)[:half])
        + "\n# Files read\n\n" + "".join(f"- `{d}`\n" for d in everything_in(xs_room)[half:])
    )
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.output["findings"][0]["documents"] == [CORP]
    assert result.files_read == 40


def test_a_level_three_files_read_list_is_not_an_issue(xs_room):
    text = report(("Consent gap", "critical", [CORP])) + "\n### Files read (corporate)\n\n" + f"- `{COMM}`\n"
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.findings == 1
    assert result.output["findings"][0]["documents"] == [CORP]


def test_a_trailing_level_one_section_is_set_aside_not_credited_to_the_last_issue(xs_room):
    text = report(("Consent gap", "critical", [CORP])) + f"\n# Limitations\n\nWe did not open `{COMM}`.\n"
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.output["findings"][0]["documents"] == [CORP]
    assert any("# Limitations" in note for note in result.notes)


def test_an_issue_citing_more_documents_than_any_evidence_chain_is_refused(xs_room):
    # Final review C1's backstop: whatever shape leaks a list into an issue, an
    # issue citing more than MAX_CITATIONS documents is refused unless allowed.
    many = everything_in(xs_room)[:21]
    text = report(("Everything", "high", many))
    with pytest.raises(LegoraImportError, match="21 documents"):
        import_review(write_report(xs_room, text), xs_room)
    result = import_review(write_report(xs_room, text), xs_room, allow_wide=True)
    assert len(result.output["findings"][0]["documents"]) == 21


def test_provenance_is_not_stamped_when_the_report_does_not_name_the_room(xs_room):
    # Final review I1: path resolution cannot tell rooms apart, so the title must name the room.
    add_manifest(xs_room)
    text = report(("Gap", "high", [CORP]), preamble="# Review of Quern subset\n\nModel: not known\n")
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.output["room_hash"] == ""
    assert "Testbed" in result.provenance


def test_provenance_is_stamped_when_the_title_names_the_room_in_any_case(xs_room):
    content_hash = add_manifest(xs_room)
    text = report(("Gap", "high", [CORP]), preamble="# Review of TESTBED data-room\n\nModel: not known\n")
    assert import_review(write_report(xs_room, text), xs_room).output["room_hash"] == content_hash


@pytest.mark.parametrize(
    "prefix",
    [
        "/workspace/documents/projects/Testbed/_key/flagged",
        "/workspace/documents/projects/Testbed/data-room-subset-flagged",
    ],
)
def test_a_report_citing_the_answer_key_side_is_refused(xs_room, prefix):
    # Final review I3: a run that read the flagged tree is not a blind review.
    text = report(("Gap", "high", [f"{prefix}/{CORP}"]))
    with pytest.raises(LegoraImportError, match="answer-key side"):
        import_review(write_report(xs_room, text), xs_room, drop_unknown=True)


def test_a_files_read_entry_from_the_answer_key_side_is_refused(xs_room):
    text = report(("Gap", "high", [CORP]), files_read=[f"_key/flagged/{CORP}"])
    with pytest.raises(LegoraImportError, match="answer-key side"):
        import_review(write_report(xs_room, text), xs_room)


@pytest.mark.parametrize(
    "written",
    [
        "`01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf#page=3`",
        "`01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf (p. 3)`",
        "`01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf, clause 4.2`",
        "`01_corporate/1.1_constitutional/1.1.1_constitutional-01.md:12`",
        "`01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf.`",
        "_01_corporate/1.1_constitutional/1.1.1_constitutional-01.pdf_",
        "01\\_corporate/1.1\\_constitutional/1.1.1\\_constitutional-01.pdf",
    ],
)
def test_a_citation_with_trailing_text_emphasis_or_escapes_is_still_read(written):
    # Final review I4: each of these used to vanish, silently understating recall.
    assert citations_in(f"See {written} for the consent.", SECTIONS) == [DOC]


def test_issues_citing_no_document_in_the_cut_are_named_in_the_summary(xs_room):
    text = report(("Gap", "high", [CORP])) + "\n## Unsourced worry\n\nSeverity: low\n\nNo document.\n"
    result = import_review(write_report(xs_room, text), xs_room)
    assert result.uncited == ["Unsourced worry"]
    assert "Unsourced worry" in render_summary(result, xs_room.parent / "o.json")


def test_cli_allow_wide_issues_imports_a_wide_issue(xs_room, tmp_path):
    out = tmp_path / "wide.json"
    report_path = write_report(xs_room, report(("Everything", "high", everything_in(xs_room)[:21])))
    assert run_cli(report_path, "--room", xs_room, "--out", out) == 2
    assert run_cli(report_path, "--room", xs_room, "--out", out, "--allow-wide-issues") == 0
