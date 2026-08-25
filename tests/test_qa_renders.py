import pytest

from synthvdr.qa.renders import gate_16_render_parity
from synthvdr.qa.runner import GateContext
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import FindingSet

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
SECTION_DIRS="01_corporate"
'''


@pytest.fixture
def room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF)
    d = tmp_path / "data-room" / "01_corporate"
    d.mkdir(parents=True)
    (d / "1.1.1_articles.md").write_text("# Articles\n")
    (tmp_path / "_key").mkdir()
    return tmp_path


def ctx_for(room, strict=False):
    return GateContext(
        room=room, conf=load_room_conf(room / "room.conf"),
        findings=FindingSet([], ""), distractors=[], strict=strict,
    )


def test_skips_loudly_when_no_render_tree_exists(room):
    result = gate_16_render_parity(ctx_for(room))
    assert result.status == "SKIP"
    assert "no render tree" in result.detail.lower()


def test_passes_when_the_render_tree_mirrors_the_blind_tree(room):
    out = room / "data-room-docx" / "01_corporate"
    out.mkdir(parents=True)
    (out / "1.1.1_articles.docx").write_bytes(b"stub")
    assert gate_16_render_parity(ctx_for(room)).status == "PASS"


def test_fails_when_a_render_is_missing(room):
    (room / "data-room-docx").mkdir()
    result = gate_16_render_parity(ctx_for(room))
    assert result.status == "FAIL"
    assert "1.1.1_articles" in result.detail


# --- Two-directional parity, reported distinctly (ruling 2) ---------------


def test_fails_and_names_missing_direction_distinctly(room):
    """A render tree with a document that has no render must report it as
    a MISSING render, distinct wording from an orphaned render."""
    (room / "data-room-docx" / "01_corporate").mkdir(parents=True)
    result = gate_16_render_parity(ctx_for(room))
    assert result.status == "FAIL"
    assert "no render" in result.detail.lower()
    assert "no source" not in result.detail.lower()


def test_fails_on_orphaned_render_with_no_missing_direction(room):
    """render_tree_docx never deletes, so a source document renamed or
    removed after rendering leaves an orphaned .docx behind. Every real
    source still has its render (no MISSING problem here) — only the
    ORPHANED direction should fire, and it must say so, not "missing"."""
    out = room / "data-room-docx" / "01_corporate"
    out.mkdir(parents=True)
    (out / "1.1.1_articles.docx").write_bytes(b"stub")
    (out / "9.9.9_deleted-source.docx").write_bytes(b"stale")

    result = gate_16_render_parity(ctx_for(room))

    assert result.status == "FAIL"
    assert "render(s) with no source" in result.detail
    assert "source(s) with no render" not in result.detail
    assert "9.9.9_deleted-source" in result.detail


def test_fails_on_both_directions_reported_distinctly(room):
    """A second source document with a render, plus one missing render and
    one orphaned render, elsewhere — both problems present at once must
    both be reported, each under its own label, not merged into one count."""
    d2 = room / "data-room" / "01_corporate"
    (d2 / "1.1.2_second.md").write_text("# Second\n")

    out = room / "data-room-docx" / "01_corporate"
    out.mkdir(parents=True)
    # 1.1.1_articles has NO render (missing direction).
    (out / "1.1.2_second.docx").write_bytes(b"stub")
    # An orphaned render with no matching source (orphaned direction).
    (out / "9.9.9_deleted-source.docx").write_bytes(b"stale")

    result = gate_16_render_parity(ctx_for(room))

    assert result.status == "FAIL"
    assert "source(s) with no render" in result.detail
    assert "render(s) with no source" in result.detail
    assert "1.1.1_articles" in result.detail
    assert "9.9.9_deleted-source" in result.detail


def test_passes_across_both_docx_and_pdf_trees(room):
    for suffix, ext in (("-docx", ".docx"), ("-pdf", ".pdf")):
        out = room / f"data-room{suffix}" / "01_corporate"
        out.mkdir(parents=True)
        (out / f"1.1.1_articles{ext}").write_bytes(b"stub")
    result = gate_16_render_parity(ctx_for(room))
    assert result.status == "PASS"
    assert "data-room-docx" in result.detail
    assert "data-room-pdf" in result.detail


def test_fails_naming_which_tree_has_the_problem(room):
    """With two render trees present, only one broken, the broken tree's
    name must appear so an author knows which renderer to re-run."""
    docx_out = room / "data-room-docx" / "01_corporate"
    docx_out.mkdir(parents=True)
    (docx_out / "1.1.1_articles.docx").write_bytes(b"stub")

    (room / "data-room-pdf").mkdir()  # present but empty: PDF render missing

    result = gate_16_render_parity(ctx_for(room))
    assert result.status == "FAIL"
    assert "data-room-pdf" in result.detail
    assert "data-room-docx" not in result.detail


# --- F4: a case-only mismatch must not contradict itself ------------------


def test_case_only_mismatch_is_consistent_across_both_directions(room):
    """A render whose filename differs from its source only in case must
    not pass one direction's check while failing the other's -- that
    combination previously told an author to delete their only render.
    Both directions now compare literal, case-sensitive relative paths
    built from directory listings (see the comment in gate_16_render_parity
    for why), so a case-only mismatch is reported consistently: as BOTH a
    missing render (no correctly-cased file exists) and an orphaned one
    (the wrongly-cased file matches no source) -- never one verdict from
    one direction and its opposite from the other."""
    out = room / "data-room-docx" / "01_corporate"
    out.mkdir(parents=True)
    (out / "1.1.1_ARTICLES.docx").write_bytes(b"stub")  # source is 1.1.1_articles.md

    result = gate_16_render_parity(ctx_for(room))

    assert result.status == "FAIL"
    assert "source(s) with no render" in result.detail
    assert "render(s) with no source" in result.detail


# --- F5: both directions must identify files the same way -----------------


def test_missing_direction_uses_full_relative_paths_not_bare_stems(room):
    """Two sources sharing a basename in different directories must not
    collapse to the same identifier in the message -- gate 16 must print
    full relative paths on the missing side, exactly as it already does
    on the orphaned side, so an author can tell which one is broken."""
    other = room / "data-room" / "02_other"
    other.mkdir(parents=True)
    (other / "1.1.1_articles.md").write_text("# Also Articles\n")

    (room / "data-room-docx").mkdir()  # both renders missing

    result = gate_16_render_parity(ctx_for(room))

    assert result.status == "FAIL"
    assert "01_corporate/1.1.1_articles.docx" in result.detail
    assert "02_other/1.1.1_articles.docx" in result.detail
