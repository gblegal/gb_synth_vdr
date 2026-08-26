import shutil

import pytest

from synthvdr.qa.integrity import (
    _cell_has_no_superseded_values,
    _isolated_contains,
    gate_13_fact_sheet,
    gate_15_discoverability,
    gate_17_answer_key_validation,
    parse_canonical_figures,
)
from synthvdr.qa.runner import GateContext
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import Distractor, Finding, FindingSet

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
EXPECTED_KDP_CARRIERS=1
SECTION_DIRS="01_corporate"
'''

FACT_SHEET = """# Fact sheet

## Canonical figures

| Key | Value | Superseded |
|---|---|---|
| ev_headline | GBP 725m | GBP 700m; GBP 710m |
| locked_box_date | 31 March 2026 | — |
"""

SELF_CONTRADICTORY_FACT_SHEET = """# Fact sheet

## Canonical figures

| Key | Value | Superseded |
|---|---|---|
| locked_box_date | 31 March 2026 | March 2026 |
"""


def finding(discoverable=True, id="ENV-1"):
    return Finding(
        id=id, title="a", severity="critical", workstream="environmental",
        multi_document=False, source="01_corporate/1.1_constitutional/1.1.1_articles.md",
        location="x", substance="s", discoverable_from_blind=discoverable,
        audit_note="reachable from 1.1.1",
    )


@pytest.fixture
def room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF)
    d = tmp_path / "data-room" / "01_corporate" / "1.1_constitutional"
    d.mkdir(parents=True)
    (d / "1.1.1_articles.md").write_text(
        "# Articles\n\nEnterprise value of GBP 725m, locked box at 31 March 2026.\n"
    )
    (tmp_path / "_key").mkdir()
    (tmp_path / "_key" / "fact-sheet.md").write_text(FACT_SHEET)
    return tmp_path


def ctx_for(room, findings=None):
    return GateContext(
        room=room,
        conf=load_room_conf(room / "room.conf"),
        findings=findings if findings is not None else FindingSet([finding()], "Project Testbed"),
        distractors=[],
    )


def test_parses_values_and_superseded_lists():
    figures = parse_canonical_figures(FACT_SHEET)
    assert figures[0].key == "ev_headline"
    assert figures[0].value == "GBP 725m"
    assert figures[0].superseded == ["GBP 700m", "GBP 710m"]
    assert figures[1].superseded == []


def test_gate_13_passes_when_canonical_figures_appear(room):
    assert gate_13_fact_sheet(ctx_for(room)).status == "PASS"


def test_gate_13_fails_when_a_canonical_figure_appears_nowhere(room):
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text("# Articles\n\nNo figures at all here.\n")
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "FAIL"
    assert "ev_headline" in result.detail


def test_gate_13_fails_when_a_superseded_figure_survives(room):
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text(
        "# Articles\n\nEnterprise value of GBP 725m (previously GBP 700m), 31 March 2026.\n"
    )
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "FAIL"
    assert "GBP 700m" in result.detail


def test_gate_13_parses_a_second_canonical_figures_table(room):
    """Final review, F3: the ORIGINAL bug had this parked as "authoring error
    only, no silent-PASS risk" — it is a silent PASS. A fact sheet that groups
    canonical figures under more than one '## Canonical figures' heading (a
    financial-figures table, then a commercial-figures table further down
    under its own heading) is a natural shape for an author to produce, and
    every figure after the FIRST heading used to go completely unchecked.

    Reproduced exactly: a superseded value ('GBP 6.2m') still present in the
    room, declared in a SECOND canonical-figures table. Before the fix this
    passed (rc 0, "2 canonical figures reconciled" counting only the first
    table); the fix must both catch the still-present superseded value AND
    report the true total figure count across both tables.
    """
    second_table_fact_sheet = """# Fact sheet

## Canonical figures

| Key | Value | Superseded |
|---|---|---|
| ev_headline | GBP 725m | GBP 700m |

## Deal narrative

Some prose that is not a canonical-figures table at all.

## Canonical figures

| Key | Value | Superseded |
|---|---|---|
| debt_headline | GBP 7.1m | GBP 6.2m |
"""
    (room / "_key" / "fact-sheet.md").write_text(second_table_fact_sheet)
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text(
        "# Articles\n\nEnterprise value of GBP 725m. Net debt of GBP 7.1m, "
        "previously reported as GBP 6.2m.\n"
    )
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "FAIL"
    assert "debt_headline" in result.detail
    assert "GBP 6.2m" in result.detail


def test_gate_13_counts_figures_from_every_canonical_figures_table_when_clean(room):
    second_table_fact_sheet = """# Fact sheet

## Canonical figures

| Key | Value | Superseded |
|---|---|---|
| ev_headline | GBP 725m | GBP 700m |

## Deal narrative

Some prose that is not a canonical-figures table at all.

## Canonical figures

| Key | Value | Superseded |
|---|---|---|
| debt_headline | GBP 7.1m | — |
"""
    (room / "_key" / "fact-sheet.md").write_text(second_table_fact_sheet)
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text("# Articles\n\nEnterprise value of GBP 725m. Net debt of GBP 7.1m.\n")
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "PASS"
    assert "2 canonical figures" in result.detail


def test_gate_13_skips_without_a_fact_sheet(room):
    (room / "_key" / "fact-sheet.md").unlink()
    assert gate_13_fact_sheet(ctx_for(room)).status == "SKIP"


def test_gate_13_skips_when_blind_tree_is_absent_or_empty(room):
    import shutil

    shutil.rmtree(room / "data-room")
    assert gate_13_fact_sheet(ctx_for(room)).status == "SKIP"


def test_gate_13_skips_when_fact_sheet_has_no_canonical_figures_table(room):
    (room / "_key" / "fact-sheet.md").write_text("# Fact sheet\n\nNothing here.\n")
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "SKIP"
    # Must not be misreported as the "malformed table present" case (Fix C):
    # there is no heading here at all, so the "no table" reason is the
    # honest one — checked explicitly so the two SKIP reasons can't collapse
    # into one message regardless of which one is actually true.
    assert "no '## canonical figures' table" in result.detail.lower()
    assert "malformed" not in result.detail.lower()


def test_gate_13_ignores_non_md_csv_files_under_blind_tree(room):
    # A .txt file containing the canonical figures must not count as evidence
    # that they "appear in the room" — only .md/.csv are in scope. The in-scope
    # .md file is rewritten to omit the figures; a .txt sibling carrying the
    # figures must not rescue the gate into a PASS.
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text("# Articles\n\nNo figures at all here.\n")
    (room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.txt").write_text(
        "Enterprise value of GBP 725m, locked box at 31 March 2026.\n"
    )
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "FAIL"


def test_gate_13_flags_self_contradictory_fact_sheet_as_authoring_error(room):
    # A superseded value that is itself a substring of the canonical value can
    # never pass: the canonical value's own presence in the room always
    # contains the superseded substring too. That is a defect in the fact
    # sheet, not the room, so the gate must name it as such rather than
    # reporting a permanently-unfixable "superseded value still present".
    (room / "_key" / "fact-sheet.md").write_text(SELF_CONTRADICTORY_FACT_SHEET)
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "FAIL"
    assert "locked_box_date" in result.detail
    assert "31 March 2026" in result.detail
    assert "March 2026" in result.detail
    assert "self-contradictory" in result.detail.lower()
    assert "fact sheet" in result.detail.lower()


def test_gate_15_passes_when_every_finding_is_audited_true(room):
    assert gate_15_discoverability(ctx_for(room)).status == "PASS"


def test_gate_15_fails_on_an_unreachable_finding(room):
    fs = FindingSet([finding(discoverable=False)], "Project Testbed")
    result = gate_15_discoverability(ctx_for(room, fs))
    assert result.status == "FAIL"
    assert "ENV-1" in result.detail


def test_gate_15_fails_on_an_unaudited_finding(room):
    fs = FindingSet([finding(discoverable=None)], "Project Testbed")
    result = gate_15_discoverability(ctx_for(room, fs))
    assert result.status == "FAIL"
    assert "not audited" in result.detail.lower()


def test_gate_15_distinguishes_unaudited_none_from_audited_true(room):
    # The entire point of the gate is that None (never audited) is not the
    # same verdict as True (audited and reachable). A finding set that mixes
    # one of each must still fail, and must name the unaudited one — proving
    # the gate does not treat None as a de-facto pass.
    fs = FindingSet(
        [finding(discoverable=True, id="ENV-1"), finding(discoverable=None, id="ENV-2")],
        "Project Testbed",
    )
    result = gate_15_discoverability(ctx_for(room, fs))
    assert result.status == "FAIL"
    assert "not audited" in result.detail.lower()
    assert "ENV-2" in result.detail


def test_gate_15_skips_when_there_are_no_findings(room):
    assert gate_15_discoverability(ctx_for(room, FindingSet([], ""))).status == "SKIP"


def test_gate_15_reports_both_categories_when_findings_are_mixed(room):
    # Regression: returning on the first non-empty category (or checking the
    # two conditions in the wrong order) would hide one behind the other,
    # costing the author a second gate run to learn what the gate already
    # knew on the first pass. Both must be named in a single FAIL.
    fs = FindingSet(
        [finding(discoverable=False, id="ENV-1"), finding(discoverable=None, id="ENV-2")],
        "Project Testbed",
    )
    result = gate_15_discoverability(ctx_for(room, fs))
    assert result.status == "FAIL"
    assert "ENV-1" in result.detail
    assert "ENV-2" in result.detail
    assert "not reachable" in result.detail.lower()
    assert "not audited" in result.detail.lower()


def test_gate_15_skips_when_the_blind_tree_is_absent(room):
    # Gate 15 states a conclusion ABOUT the blind room ("N findings reachable
    # from the blind room"), but reads only the answer key's audit flags. On a
    # room whose blind tree was never built, every other blind-tree gate SKIPs
    # with "absent or empty" and gate 15 alone reported a confident PASS —
    # a conclusion asserted against no oracle, which is this project's worst
    # defect class. The audit flags describe a tree that is not there.
    shutil.rmtree(room / "data-room")
    result = gate_15_discoverability(ctx_for(room))
    assert result.status == "SKIP"
    assert "absent or empty" in result.detail


def test_gate_15_skips_when_the_blind_tree_holds_no_documents(room):
    # Same conclusion-without-an-oracle problem, reached by the likelier
    # route: the directory exists (a build started, or the ownership marker
    # went down) but carries no .md or .csv document for a finding to be
    # reachable from. Matches gate 13's "absent or empty" predicate exactly.
    for f in (room / "data-room").rglob("*"):
        if f.is_file():
            f.unlink()
    result = gate_15_discoverability(ctx_for(room))
    assert result.status == "SKIP"
    assert "absent or empty" in result.detail


# --- Fix round 1: matching-layer defects (F1, F2, F6) and an honest SKIP (Fix C) ---


def test_gate_13_fails_when_a_later_superseded_value_survives_not_just_the_first(room):
    # Regression: checking only the first superseded value per figure would
    # miss this — ev_headline's superseded list (from FACT_SHEET) is
    # ["GBP 700m", "GBP 710m"]; only the SECOND one is present in the room.
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text(
        "# Articles\n\nEnterprise value of GBP 725m (originally GBP 710m), 31 March 2026.\n"
    )
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "FAIL"
    assert "GBP 710m" in result.detail


@pytest.mark.parametrize(
    "needle, haystack, want",
    [
        ("700m", "GBP 3700m", False),
        ("25m", "1725m", False),
        ("GBP 725m", "of GBP 725m, locked", True),
        ("£64.0m", "a £64.0m shortfall", True),
        ("31 March 2026", "at 31 March 2026.", True),
        ("725m", "725m", True),  # needle is the whole haystack: boundary at both ends
        ("GBP 725m", "GBP 725million", False),
        ("725", "７２５０ yen", False),  # full-width digits: still an embedding
        ("725", "٣725٤", False),  # Arabic-Indic digits: still an embedding
        # Findings 1 & 2 (fix round 2): a footnote superscript glued onto a
        # figure is an annotation, not more digits — it must not disqualify
        # the match in either direction.
        ("700m", "GBP 700m², restated", True),
        ("GBP 725m", "stated GBP 725m¹", True),
    ],
)
def test_isolated_contains_matches_word_boundaries_not_embedded_tokens(needle, haystack, want):
    assert _isolated_contains(needle, haystack) is want


@pytest.mark.parametrize(
    "cell",
    [
        "-",  # HYPHEN-MINUS
        "‐",  # HYPHEN
        "‑",  # NON-BREAKING HYPHEN
        "‒",  # FIGURE DASH
        "–",  # EN DASH
        "—",  # EM DASH
        "−",  # MINUS SIGN (category Sm, not Pd — named explicitly)
        "--",
        "- - -",
        "",
        "   ",
    ],
)
def test_cell_has_no_superseded_values_for_any_dash_character(cell):
    assert _cell_has_no_superseded_values(cell) is True


def test_cell_has_no_superseded_values_is_false_for_a_negative_figure():
    # A negative figure is a legitimate superseded value; it must not be
    # swallowed by the dash-only check just because it starts with one.
    assert _cell_has_no_superseded_values("-5m") is False


def test_gate_13_passes_when_a_dash_only_superseded_cell_shares_the_room_with_an_ordinary_table(room):
    # F1: a Superseded cell of "--" (two hyphens, not the canonical "—" em
    # dash) must still mean "no superseded values" — not a real value that
    # then gets found "surviving" inside the separator row ("|---|---|") of
    # any ordinary markdown table anywhere else in the room.
    (room / "_key" / "fact-sheet.md").write_text(
        "# Fact sheet\n\n## Canonical figures\n\n"
        "| Key | Value | Superseded |\n|---|---|---|\n"
        "| locked_box_date | 31 March 2026 | -- |\n"
    )
    other = room / "data-room/01_corporate/1.1_constitutional/1.1.2_other.md"
    other.write_text("# Some other document\n\n| Col A | Col B |\n|---|---|\n| x | y |\n")
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "PASS"


def test_gate_13_does_not_false_fail_when_a_superseded_value_is_embedded_in_another_figures_value(room):
    # F2: ev_headline's superseded "700m" is a plain substring of
    # debt_headline's own canonical "GBP 3700m" — both correctly stated in
    # the room. A boundary-blind substring check reports "700m" as still
    # present; it is not, "3700m" just happens to end the same way.
    (room / "_key" / "fact-sheet.md").write_text(
        "# Fact sheet\n\n## Canonical figures\n\n"
        "| Key | Value | Superseded |\n|---|---|---|\n"
        "| ev_headline | GBP 725m | 700m |\n"
        "| debt_headline | GBP 3700m | — |\n"
    )
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text(
        "# Articles\n\nEnterprise value of GBP 725m, net debt of GBP 3700m, "
        "locked box at 31 March 2026.\n"
    )
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "PASS"


def test_gate_13_fails_when_canonical_value_only_appears_embedded_in_a_longer_token(room):
    # F6: canonical "25m" against a room that only ever says "1725m" must
    # FAIL — this is the anti-thin-filler gate, so a false PASS here (the
    # figure "appearing" only because it's embedded in an unrelated number)
    # is the worse failure direction.
    (room / "_key" / "fact-sheet.md").write_text(
        "# Fact sheet\n\n## Canonical figures\n\n"
        "| Key | Value | Superseded |\n|---|---|---|\n"
        "| minority_stake | 25m | — |\n"
    )
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text("# Articles\n\nThe syndicate stake is worth GBP 1725m.\n")
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "FAIL"
    assert "minority_stake" in result.detail


def test_gate_13_self_contradiction_guard_respects_word_boundaries_too(room):
    # The self-contradiction guard must use the same boundary-aware check as
    # the corpus checks (Fix B), not a plain substring test: a superseded
    # value that is merely embedded inside the canonical value's digits —
    # not at a word boundary — is the F2/F6 substring-collision shape, not
    # a same-figure self-contradiction, and must not be misreported as one.
    (room / "_key" / "fact-sheet.md").write_text(
        "# Fact sheet\n\n## Canonical figures\n\n"
        "| Key | Value | Superseded |\n|---|---|---|\n"
        "| minority_stake | 1725m | 25m |\n"
    )
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text("# Articles\n\nThe syndicate stake is worth GBP 1725m.\n")
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "PASS"


def test_gate_13_skip_reason_names_a_malformed_table_not_a_missing_one(room):
    # Fix C: a '## Canonical figures' heading with a table that is missing
    # the Superseded column is a malformed table, not an absent one — the
    # SKIP reason must say so, not fall back to the "no table" message,
    # since that would send an author looking for the wrong problem.
    (room / "_key" / "fact-sheet.md").write_text(
        "# Fact sheet\n\n## Canonical figures\n\n"
        "| Key | Value |\n|---|---|\n"
        "| ev_headline | GBP 725m |\n"
    )
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "SKIP"
    assert "malformed" in result.detail.lower()
    assert "no '## canonical figures' table" not in result.detail.lower()


# --- Fix round 2: superscript footnote markers (Findings 1 & 2), the dash
# character set (Finding 3), and an honest Fix-D hint for a header-only table ---


def test_gate_13_fails_when_a_superseded_value_survives_with_a_footnote_marker_glued_on(room):
    # Finding 1: a superscript footnote marker directly after a surviving
    # superseded value must not hide it from the isolated-match check.
    # 'isalnum()' treats '²' as alphanumeric, which would wrongly disqualify
    # this as "embedded in a longer token" and let the gate PASS — the
    # false-PASS shape, on the anti-thin-filler gate, is the worse direction.
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text(
        "# Articles\n\nEnterprise value of GBP 725m (previously GBP 700m², "
        "restated), 31 March 2026.\n"
    )
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "FAIL"
    assert "GBP 700m" in result.detail


def test_gate_13_passes_when_canonical_value_has_a_footnote_marker_glued_on(room):
    # Finding 2: a correct room must not fail gate 13 just because a
    # footnote superscript sits directly against the canonical figure —
    # "GBP 725m¹" is GBP 725m at a real word boundary followed by an
    # annotation, not GBP 725m embedded inside some longer figure.
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text(
        "# Articles\n\nStated GBP 725m¹ in the accounts, locked box at "
        "31 March 2026.\n"
    )
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "PASS"


def test_gate_13_passes_when_superseded_cell_uses_a_non_hyphen_dash_near_a_matching_dash_in_prose(room):
    # Finding 3: NON-BREAKING HYPHEN (and FIGURE DASH, MINUS SIGN, HYPHEN)
    # must mean "no superseded values" too, not just ASCII hyphen-minus and
    # em/en dash. A fact sheet using one of those as its "none" sentinel
    # must PASS even when the room's own prose happens to use the very same
    # dash character elsewhere as ordinary stylistic punctuation — under the
    # old literal character set, that stray dash would be read as a
    # "surviving" superseded value and false-FAIL the gate.
    (room / "_key" / "fact-sheet.md").write_text(
        "# Fact sheet\n\n## Canonical figures\n\n"
        "| Key | Value | Superseded |\n|---|---|---|\n"
        "| locked_box_date | 31 March 2026 | ‑ |\n"
    )
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text(
        "# Articles\n\nLocked box at 31 March 2026, price range ‑ subject to "
        "review ‑ remains provisional.\n"
    )
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "PASS"


def test_gate_13_skip_reason_matches_a_valid_header_with_no_data_rows(room):
    # Fix D: a well-formed 3-column header with zero data rows underneath it
    # is a different problem from a missing column, and the SKIP reason must
    # say so rather than reusing the "missing column" hint regardless of
    # what's actually wrong.
    (room / "_key" / "fact-sheet.md").write_text(
        "# Fact sheet\n\n## Canonical figures\n\n"
        "| Key | Value | Superseded |\n|---|---|---|\n"
    )
    result = gate_13_fact_sheet(ctx_for(room))
    assert result.status == "SKIP"
    assert "no data rows" in result.detail.lower()
    assert "missing column" not in result.detail.lower()


# ---------------------------------------------------------------------------
# Task 20 fix round 1, D2 — synthvdr.schema.validate() was called nowhere in
# synthvdr/: it existed only as a manual step documented in the
# /vdr-findings skill, so a findings/distractors document that loaded
# cleanly but failed validate()'s own internal-consistency checks (a
# dangling cross_link, a multi_document/corroboration mismatch, a
# distractor whose location/resolution doubles as real evidence, ...) could
# ship through every other gate, /vdr-qa --strict and /vdr-package --strict
# without ever being caught. gate_17_answer_key_validation closes that gap.
# ---------------------------------------------------------------------------


def ctx_with(room, findings, distractors=()):
    return GateContext(
        room=room,
        conf=load_room_conf(room / "room.conf"),
        findings=findings,
        distractors=list(distractors),
    )


def test_gate_17_passes_on_a_consistent_answer_key(room):
    result = gate_17_answer_key_validation(ctx_with(room, FindingSet([finding()], "Project Testbed")))
    assert result.status == "PASS"
    assert "1 finding(s)" in result.detail


def test_gate_17_skips_when_there_is_nothing_to_validate(room):
    result = gate_17_answer_key_validation(ctx_with(room, FindingSet([], "")))
    assert result.status == "SKIP"


def test_gate_17_fails_on_a_dangling_cross_link_and_names_it(room):
    bad = Finding(
        id="ENV-1", title="a", severity="critical", workstream="environmental",
        multi_document=False, source="01_corporate/1.1_constitutional/1.1.1_articles.md",
        location="x", substance="s", discoverable_from_blind=True,
        audit_note="reachable from 1.1.1", cross_links=["NO-SUCH-ID"],
    )
    result = gate_17_answer_key_validation(ctx_with(room, FindingSet([bad], "Project Testbed")))
    assert result.status == "FAIL"
    # The failure output must name the SPECIFIC problem validate() returned,
    # not a generic "the answer key is invalid".
    assert "NO-SUCH-ID" in result.detail
    assert "cross_link" in result.detail


def test_gate_17_fails_when_a_distractors_location_is_also_real_evidence(room):
    f = finding()
    dx = Distractor(
        id="DX-1", title="d",
        location=f.source,  # same document as a finding's own evidence
        resolution="01_corporate/1.1_constitutional/1.1.1_other.md",
    )
    result = gate_17_answer_key_validation(
        ctx_with(room, FindingSet([f], "Project Testbed"), distractors=[dx])
    )
    assert result.status == "FAIL"
    assert "DX-1" in result.detail


def test_gate_15_says_how_many_findings_it_did_not_name(room):
    # The awkward shape in this sweep: the truncated list is followed by a
    # trailing instruction, so the overflow marker has to land between the
    # two. "ENV-5 — run the vdr-auditor subagent" reads as the whole story;
    # "ENV-5 (+2 more) — run the vdr-auditor subagent" does not.
    fs = FindingSet(
        [finding(discoverable=None, id=f"ENV-{n}") for n in range(1, 8)], "Project Testbed"
    )
    result = gate_15_discoverability(ctx_for(room, fs))
    assert result.status == "FAIL"
    assert "(+2 more)" in result.detail
    assert result.detail.rstrip().endswith("run the vdr-auditor subagent")


def test_gate_17_says_how_many_problems_it_did_not_name(room):
    fs = FindingSet(
        [
            Finding(
                id=f"ENV-{n}", title="a", severity="critical", workstream="environmental",
                multi_document=False,
                source="01_corporate/1.1_constitutional/1.1.1_articles.md",
                location="x", substance="s", discoverable_from_blind=True,
                audit_note="reachable", cross_links=["NOPE-9"],
            )
            for n in range(1, 8)
        ],
        "Project Testbed",
    )
    result = gate_17_answer_key_validation(ctx_for(room, fs))
    assert result.status == "FAIL"
    assert "(+2 more)" in result.detail
