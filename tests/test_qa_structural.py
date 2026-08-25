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


def ctx_for(room, findings=None):
    return GateContext(
        room=room,
        conf=load_room_conf(room / "room.conf"),
        findings=FindingSet(findings if findings is not None else [finding()], "Project Testbed"),
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
    # "1.9.9" — not "9.9.9" — because gate 9 bounds a candidate reference's
    # first component to the room's real section numbers (1 and 2, from
    # SECTION_DIRS). Section 9 does not exist in this room, so "9.9.9" is
    # now a WARN, not a hard FAIL (see the dedicated WARN test below);
    # "1.9.9" names a real section (1) but no real slot, so it stays a
    # genuinely dangling, hard-FAILing in-room reference.
    p = room / "data-room/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    p.write_text("# accounts\n\nSee 1.9.9 for detail.\n")
    result = gate_09_xrefs(ctx_for(room))
    assert result.status == "FAIL"
    assert "1.9.9" in result.detail


def test_gate_09_warns_on_an_out_of_range_slot_shaped_token(room):
    """Round 1 of this fix bounded SLOT_REF's first component to the room's
    real sections to kill a date false-positive, then silently dropped
    every out-of-range match — including a genuine typo like "9.9.9" in
    this two-section room, which used to (correctly) hard-FAIL before that
    bound existed. An out-of-range token is a date OR a gross typo, and
    either way is worth a human's attention: WARN, not silence and not a
    hard FAIL."""
    p = room / "data-room/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    p.write_text("# accounts\n\nSee 9.9.9 for detail.\n")
    result = gate_09_xrefs(ctx_for(room))
    assert result.status == "WARN"
    assert "9.9.9" in result.detail


def test_gate_09_fails_and_still_names_an_out_of_range_token_when_both_occur(room):
    """A hard FAIL (a genuinely dangling in-range reference) takes priority
    over a WARN (an out-of-range token) when a single document has both —
    but the out-of-range token must still be named in the FAIL detail, not
    silently dropped just because a stronger problem was found first."""
    p = room / "data-room/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    p.write_text("# accounts\n\nSee 1.9.9 and 9.9.9 for detail.\n")
    result = gate_09_xrefs(ctx_for(room))
    assert result.status == "FAIL"
    assert "1.9.9" in result.detail
    assert "9.9.9" in result.detail


def test_gate_09_does_not_fail_on_a_date_shaped_token_in_prose(room):
    """A short-format date matches SLOT_REF's shape exactly, but "24" is not
    a section this two-section room could ever have. Round 1 of this fix
    silently dropped it (a PASS); round 2 corrects that — silence throws
    away a signal worth having, since an out-of-range token is often a
    genuine error too — so it is now a WARN, which still does not fail the
    build (see synthvdr.qa.runner: WARN is counted but never turns the exit
    code non-zero)."""
    p = room / "data-room/02_financial/2.1_statutory-accounts/2.1.1_accounts.md"
    p.write_text("# accounts\n\nFiled 24.08.26, pending review.\n")
    result = gate_09_xrefs(ctx_for(room))
    assert result.status == "WARN"
    assert "24.08.26" in result.detail


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


# ---------------------------------------------------------------------------
# synthvdr.twin never annotates non-markdown evidence (a CSV register, say)
# — it is copied byte-for-byte, because there is nowhere in a CSV to append
# prose. Round 1 of gate 8's redesign built its expected-carrier set from
# EVERY evidence path regardless of suffix, so a legitimate CSV-evidenced
# finding in a correctly-built room was reported as "destroyed or moved" —
# a false FAIL on a healthy room. These two tests confirm gate 8 now
# expects a carrier only for markdown evidence, while still surfacing the
# non-markdown evidence informationally rather than dropping it silently.
# ---------------------------------------------------------------------------


def test_gate_08_passes_when_a_findings_evidence_is_a_csv_register(room):
    csv_rel = "02_financial/2.1_statutory-accounts/2.1.1_register.csv"
    for tree in ("data-room", "_key/flagged"):
        (room / tree / csv_rel).write_text("id,value\n1,10\n")
    csv_finding = Finding(
        id="FIN-1",
        title="A register issue",
        severity="high",
        workstream="financial",
        multi_document=False,
        source=csv_rel,
        location="row 1",
        substance="a register issue",
    )
    # ENV-1 (the room fixture's real, correctly-annotated markdown carrier)
    # is kept in play alongside the new CSV-evidenced finding, so this test
    # isolates "does a CSV finding alone break an otherwise healthy room".
    result = gate_08_carrier_census(ctx_for(room, findings=[finding(), csv_finding]))
    assert result.status == "PASS"
    assert "2.1.1_register.csv" in result.detail


def test_gate_08_expects_a_carrier_only_for_the_markdown_evidence_path(room):
    """A mixed finding — one markdown source, one CSV corroboration path.
    Only the markdown path should be an expected carrier; the CSV path is
    informational only, never expected to carry a block of its own."""
    csv_rel = "02_financial/2.1_statutory-accounts/2.1.1_register.csv"
    for tree in ("data-room", "_key/flagged"):
        (room / tree / csv_rel).write_text("id,value\n1,10\n")
    mixed = Finding(
        id="ENV-1",
        title="An issue",
        severity="critical",
        workstream="environmental",
        multi_document=True,
        source="01_corporate/1.1_constitutional/1.1.1_articles.md",
        corroboration=[csv_rel],
        location="clause 4",
        substance="an issue",
    )
    result = gate_08_carrier_census(ctx_for(room, findings=[mixed]))
    assert result.status == "PASS"
    assert "2.1.1_register.csv" in result.detail


# ---------------------------------------------------------------------------
# Round 2 exempted non-markdown evidence from the BLOCK requirement, and in
# doing so accidentally exempted it from the EXISTENCE requirement too — an
# evidence path naming no real file, of any suffix, used to fall out of
# expected_carriers silently once round 2 filtered by ".md" before ever
# recording it as expected. build_flagged_tree refuses to build a room with
# a nonexistent evidence path, but a room can be QA'd after findings.yaml is
# edited without a rebuild — exactly the key-versus-room drift this gate
# exists to catch. These tests hold the two obligations (must exist; if
# markdown, must carry a block) as separately testable properties.
# ---------------------------------------------------------------------------


def test_gate_08_fails_when_evidence_names_a_document_that_does_not_exist(room):
    ghost_md = "02_financial/2.1_statutory-accounts/9.9.9_ghost.md"
    ghost_csv = "02_financial/2.1_statutory-accounts/9.9.9_ghost.csv"
    findings = [
        finding(),  # a real, correctly-annotated carrier stays in the mix
        Finding(
            id="FIN-1",
            title="Ghost markdown evidence",
            severity="high",
            workstream="financial",
            multi_document=False,
            source=ghost_md,
            location="n/a",
            substance="evidence names a markdown document that was never created",
        ),
        Finding(
            id="FIN-2",
            title="Ghost CSV evidence",
            severity="high",
            workstream="financial",
            multi_document=False,
            source=ghost_csv,
            location="n/a",
            substance="evidence names a CSV register that was never created",
        ),
        Finding(
            id="FIN-3",
            title="Empty evidence path",
            severity="high",
            workstream="financial",
            multi_document=False,
            source="",
            location="n/a",
            substance="evidence path is empty",
        ),
    ]
    result = gate_08_carrier_census(ctx_for(room, findings=findings))
    assert result.status == "FAIL"
    assert "9.9.9_ghost.md" in result.detail
    assert "9.9.9_ghost.csv" in result.detail
    assert "does not exist" in result.detail


def test_gate_08_fails_on_existence_not_on_the_carrier_check_for_a_mixed_finding(room):
    """A mixed finding whose CSV evidence exists but whose markdown evidence
    does not: the ghost markdown path must be reported once, as a missing
    document, never a second time as a missing carrier — the two
    obligations must not double-report, or silently swap which one fires."""
    csv_rel = "02_financial/2.1_statutory-accounts/2.1.1_register.csv"
    for tree in ("data-room", "_key/flagged"):
        (room / tree / csv_rel).write_text("id,value\n1,10\n")
    ghost_md = "02_financial/2.1_statutory-accounts/9.9.9_ghost.md"
    mixed = Finding(
        id="FIN-3",
        title="Real CSV, ghost markdown",
        severity="high",
        workstream="financial",
        multi_document=True,
        source=ghost_md,
        corroboration=[csv_rel],
        location="n/a",
        substance="the markdown source was never written",
    )
    # ENV-1 (the fixture's real, correctly-annotated carrier) is kept in
    # play so the ONLY problem gate 8 has to report is the ghost markdown
    # path — otherwise articles.md would also flag as an unexpected
    # carrier (evidence for nothing in a findings list that omitted ENV-1),
    # which would muddy "fails on existence, not on the carrier check".
    result = gate_08_carrier_census(ctx_for(room, findings=[finding(), mixed]))
    assert result.status == "FAIL"
    assert "does not exist" in result.detail
    assert result.detail.count("9.9.9_ghost.md") == 1
    assert "2.1.1_register.csv" in result.detail
