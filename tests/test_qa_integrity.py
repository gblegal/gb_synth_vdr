import pytest

from synthvdr.qa.integrity import (
    gate_13_fact_sheet,
    gate_15_discoverability,
    parse_canonical_figures,
)
from synthvdr.qa.runner import GateContext
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import Finding, FindingSet

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


def test_gate_13_skips_without_a_fact_sheet(room):
    (room / "_key" / "fact-sheet.md").unlink()
    assert gate_13_fact_sheet(ctx_for(room)).status == "SKIP"


def test_gate_13_skips_when_blind_tree_is_absent_or_empty(room):
    import shutil

    shutil.rmtree(room / "data-room")
    assert gate_13_fact_sheet(ctx_for(room)).status == "SKIP"


def test_gate_13_skips_when_fact_sheet_has_no_canonical_figures_table(room):
    (room / "_key" / "fact-sheet.md").write_text("# Fact sheet\n\nNothing here.\n")
    assert gate_13_fact_sheet(ctx_for(room)).status == "SKIP"


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
