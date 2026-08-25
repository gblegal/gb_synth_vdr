import pytest

from synthvdr.qa.runner import GateContext
from synthvdr.qa.structural import (
    gate_06_dir_canon,
    gate_07_twin_diff,
    gate_08_carrier_census,
    gate_09_xrefs,
)
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import Finding, FindingSet
from synthvdr.twin import MARKER_NAME, MARKER_TEXT

CONF = '''ROOM_CODENAME="Project Testbed"
INDEX_TOTAL=2
BLIND_TOTAL=2
FLAGGED_TOTAL=2
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="CORP|ENV|FIN"
EXPECTED_KDP_CARRIERS=1
SECTION_DIRS="01_corporate 02_financial"
'''

BLOCK = "\n## Key diligence points\n\n- **ENV-1 (critical)** — an issue.\n"


def finding():
    return Finding(
        id="ENV-1",
        title="An issue",
        severity="critical",
        workstream="environmental",
        multi_document=False,
        source="01_corporate/1.1_constitutional/1.1.1_articles.md",
        location="clause 4",
        substance="an issue",
    )


@pytest.fixture
def room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF)
    for tree in ("data-room", "_key/flagged"):
        for section, name in (("01_corporate", "1.1.1_articles"), ("02_financial", "2.1.1_accounts")):
            sub = "1.1_constitutional" if section == "01_corporate" else "2.1_statutory-accounts"
            d = tmp_path / tree / section / sub
            d.mkdir(parents=True)
            d.joinpath(f"{name}.md").write_text(f"# {name}\n\nBody.\n")
    carrier = tmp_path / "_key/flagged/01_corporate/1.1_constitutional/1.1.1_articles.md"
    carrier.write_text(carrier.read_text() + BLOCK)
    return tmp_path


def ctx_for(room):
    return GateContext(
        room=room,
        conf=load_room_conf(room / "room.conf"),
        findings=FindingSet([finding()], "Project Testbed"),
        distractors=[],
    )


def test_gate_06_passes_on_canonical_directories(room):
    assert gate_06_dir_canon(ctx_for(room)).status == "PASS"


def test_gate_06_catches_a_renamed_section_directory(room):
    (room / "data-room" / "02_financial").rename(room / "data-room" / "02_finance")
    result = gate_06_dir_canon(ctx_for(room))
    assert result.status == "FAIL"
    assert "02_finance" in result.detail


def test_gate_07_passes_when_twins_are_identical_or_appended(room):
    assert gate_07_twin_diff(ctx_for(room)).status == "PASS"


def test_gate_07_catches_a_modified_body(room):
    p = room / "_key/flagged/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    p.write_text("# 2.1.1_accounts\n\nTampered body.\n")
    assert gate_07_twin_diff(ctx_for(room)).status == "FAIL"


def test_gate_08_catches_a_deleted_annotation_block_that_gate_07_accepts(room):
    carrier = room / "_key/flagged/01_corporate/1.1_constitutional/1.1.1_articles.md"
    blind = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    carrier.write_text(blind.read_text())
    assert gate_07_twin_diff(ctx_for(room)).status == "PASS"
    result = gate_08_carrier_census(ctx_for(room))
    assert result.status == "FAIL"
    # The old count-based message ("0 carriers, expected 1") is gone —
    # gate 8 now reports content, so the meaningful assertion is that it
    # names the destroyed finding, not a bare number.
    assert "ENV-1" in result.detail


def test_gate_09_passes_when_every_reference_resolves(room):
    p = room / "data-room/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    p.write_text("# accounts\n\nSee 1.1.1 for the articles.\n")
    assert gate_09_xrefs(ctx_for(room)).status == "PASS"


def test_gate_09_catches_a_dangling_reference(room):
    # "1.9.9" — not "9.9.9" — because gate 9 now bounds a candidate
    # reference's first component to the room's real section numbers
    # (1 and 2, from SECTION_DIRS). Section 9 does not exist in this room,
    # so "9.9.9" would now be filtered out as shape-only noise before ever
    # reaching the dangling check; "1.9.9" names a real section (1) but no
    # real slot, so it stays genuinely dangling under the new bound.
    p = room / "data-room/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    p.write_text("# accounts\n\nSee 1.9.9 for detail.\n")
    result = gate_09_xrefs(ctx_for(room))
    assert result.status == "FAIL"
    assert "1.9.9" in result.detail


def test_gate_09_does_not_flag_a_date_shaped_token_in_prose(room):
    """A short-format date matches SLOT_REF's shape exactly, but "24" is not
    a section this two-section room could ever have — the bound introduced
    to fix this must filter it out before the dangling check ever runs,
    not merely make it easy to allowlist."""
    p = room / "data-room/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    p.write_text("# accounts\n\nFiled 24.08.26, pending review.\n")
    result = gate_09_xrefs(ctx_for(room))
    assert result.status == "PASS"


def test_gate_09_honours_the_gaps_allowlist(room):
    p = room / "data-room/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    p.write_text("# accounts\n\nSee 1.9.9 for detail.\n")
    (room / "_key" / "gaps.yaml").write_text(
        'gaps:\n  - ref: "1.9.9"\n    reason: "deliberate gap for RFI credit"\n'
    )
    assert gate_09_xrefs(ctx_for(room)).status == "PASS"


# ---------------------------------------------------------------------------
# The flagged tree's marker file (synthvdr.twin.MARKER_NAME) has no blind
# counterpart. It is not part of either fixture tree above (that fixture
# builds the flagged tree by hand, without going through
# synthvdr.twin.build_flagged_tree), so none of the eight tests above prove
# a real marker is tolerated. These two do: they drop a genuine marker —
# same name, same content — at the flagged root and confirm gate 7 and
# gate 8 are unmoved by it. Gate 7 is unaffected structurally: it is driven
# by ctx.blind_files(), and the marker has no blind twin to be looked up
# from, so it is never visited regardless of what is sitting in the flagged
# tree. Gate 8 is unaffected because it walks the flagged tree with
# rglob("*.md"), and Path(MARKER_NAME).suffix == "" so the marker never
# matches that pattern — an incidental, suffix-based exclusion, the same
# shape gate 2 already relies on (see GateContext.blind_files' docstring).
# ---------------------------------------------------------------------------


def test_gate_07_ignores_the_flagged_tree_marker_file(room):
    (room / "_key/flagged" / MARKER_NAME).write_text(MARKER_TEXT)
    assert gate_07_twin_diff(ctx_for(room)).status == "PASS"


def test_gate_08_ignores_the_flagged_tree_marker_file(room):
    (room / "_key/flagged" / MARKER_NAME).write_text(MARKER_TEXT)
    result = gate_08_carrier_census(ctx_for(room))
    assert result.status == "PASS"
    assert "1" in result.detail


# ---------------------------------------------------------------------------
# Gate 8 originally only counted carriers against EXPECTED_KDP_CARRIERS, a
# scalar in room.conf — it never read ctx.findings at all. That counting
# scheme is content-blind: a block MOVED to an innocent document, GUTTED to
# a bare heading, or REWRITTEN to claim a fabricated finding ID all leave
# the carrier count unchanged, so a bare count check waves every one of
# them through gate 8 (and none of gates 2, 6, 7 or 9 catch them either —
# they check shape and counts, never which document claims which finding).
# These four tests reproduce those attacks against the answer key directly
# and confirm each is now caught; the fifth confirms the untouched healthy
# room still passes every one of these gates, and the sixth confirms a
# stale EXPECTED_KDP_CARRIERS is still reported once the key-derived checks
# are clean.
# ---------------------------------------------------------------------------


def test_all_four_structural_gates_pass_on_a_healthy_room(room):
    ctx = ctx_for(room)
    assert gate_06_dir_canon(ctx).status == "PASS"
    assert gate_07_twin_diff(ctx).status == "PASS"
    assert gate_08_carrier_census(ctx).status == "PASS"
    assert gate_09_xrefs(ctx).status == "PASS"


def test_gate_08_catches_a_block_moved_to_an_innocent_document(room):
    carrier = room / "_key/flagged/01_corporate/1.1_constitutional/1.1.1_articles.md"
    blind = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    destination = room / "_key/flagged/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    # Strip the block back off its real carrier, and graft the identical
    # block onto a document that is evidence for nothing — the finding's
    # content survives untouched, but attributed to the wrong document.
    carrier.write_text(blind.read_text())
    destination.write_text(destination.read_text() + BLOCK)
    result = gate_08_carrier_census(ctx_for(room))
    assert result.status == "FAIL"
    assert "1.1.1_articles.md" in result.detail
    assert "2.1.1_accounts.md" in result.detail


def test_gate_08_catches_a_gutted_annotation_block(room):
    carrier = room / "_key/flagged/01_corporate/1.1_constitutional/1.1.1_articles.md"
    blind = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    # A bare heading is one edit short of the full deletion the original
    # gate 8 already caught — gate 7 still sees an appended block (so it
    # PASSes) and the carrier count is untouched (one document, one block),
    # but the block itself names no finding at all.
    carrier.write_text(blind.read_text() + "\n## Key diligence points\n")
    assert gate_07_twin_diff(ctx_for(room)).status == "PASS"
    result = gate_08_carrier_census(ctx_for(room))
    assert result.status == "FAIL"
    assert "ENV-1" in result.detail


def test_gate_08_catches_a_fabricated_finding_id(room):
    carrier = room / "_key/flagged/01_corporate/1.1_constitutional/1.1.1_articles.md"
    blind = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    fabricated = "\n## Key diligence points\n\n- **FIN-99 (critical)** — never planted.\n"
    carrier.write_text(blind.read_text() + fabricated)
    result = gate_08_carrier_census(ctx_for(room))
    assert result.status == "FAIL"
    assert "FIN-99" in result.detail
    assert "not in the answer key" in result.detail


def test_gate_08_catches_an_unexpected_annotation_on_a_non_carrier(room):
    # The real carrier is untouched; a SECOND document — evidence for
    # nothing — gets an extra copy of the same block grafted on. Unlike the
    # "moved" test above, nothing is missing here: this isolates the
    # unexpected-carrier check on its own.
    destination = room / "_key/flagged/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    destination.write_text(destination.read_text() + BLOCK)
    result = gate_08_carrier_census(ctx_for(room))
    assert result.status == "FAIL"
    assert "2.1.1_accounts.md" in result.detail
    assert "not an evidence path" in result.detail


def test_gate_08_flags_a_stale_expected_kdp_carriers_scalar(room):
    # Content is untouched and correct; only the hand-maintained scalar in
    # room.conf disagrees with what the answer key implies. The key-derived
    # checks above find nothing wrong, so this only reaches the secondary
    # tripwire.
    (room / "room.conf").write_text(CONF.replace("EXPECTED_KDP_CARRIERS=1", "EXPECTED_KDP_CARRIERS=2"))
    result = gate_08_carrier_census(ctx_for(room))
    assert result.status == "FAIL"
    assert "EXPECTED_KDP_CARRIERS" in result.detail
