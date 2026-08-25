import pathlib
import tempfile
import textwrap

from synthvdr.namecheck import (
    CandidateName,
    Verdict,
    extract_candidates,
    load_name_check,
    names_needing_check,
    render_name_check_md,
    unresolved,
)
from synthvdr.names import cast_list

FACT_SHEET = textwrap.dedent(
    """
    # Fact sheet

    The target is Ashfell Advanced Materials Limited, with a subsidiary
    Kessler Werke GmbH.

    ## Cast

    | Name | Role |
    |---|---|
    | Marta Vinceau | Chief executive |
    | Daniel Oyelaran | Finance director |
    """
).strip()


def test_extracts_entities_by_corporate_suffix():
    names = {c.text for c in extract_candidates(FACT_SHEET) if c.kind == "entity"}
    assert names == {"Ashfell Advanced Materials Limited", "Kessler Werke GmbH"}


def test_extracts_people_from_the_cast_table():
    names = {c.text for c in extract_candidates(FACT_SHEET) if c.kind == "person"}
    assert names == {"Marta Vinceau", "Daniel Oyelaran"}


def test_names_needing_check_excludes_already_checked():
    candidates = extract_candidates(FACT_SHEET)
    existing = [Verdict("Kessler Werke GmbH", "entity", "clear", "2026-08-24", "")]
    todo = {c.text for c in names_needing_check(candidates, existing)}
    assert "Kessler Werke GmbH" not in todo
    assert "Ashfell Advanced Materials Limited" in todo


def test_render_and_load_round_trip(tmp_path):
    verdicts = [
        Verdict("Ashfell Advanced Materials Limited", "entity", "clear", "2026-08-24", ""),
        Verdict("Marta Vinceau", "person", "ambiguous", "2026-08-24", "shares a name with an author"),
    ]
    path = tmp_path / "name-check.md"
    path.write_text(render_name_check_md(verdicts, "Project Testbed"))
    assert load_name_check(path) == verdicts


def test_unresolved_returns_anything_not_clear():
    verdicts = [
        Verdict("A Limited", "entity", "clear", "2026-08-24", ""),
        Verdict("B Limited", "entity", "collision", "2026-08-24", "real company"),
        Verdict("C Limited", "entity", "ambiguous", "2026-08-24", "unclear"),
    ]
    assert [v.text for v in unresolved(verdicts)] == ["B Limited", "C Limited"]


def test_rendered_table_is_parseable_by_the_gate_14_cast_reader():
    verdicts = [Verdict("Ashfell Advanced Materials Limited", "entity", "clear", "2026-08-24", "")]
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "name-check.md"
        path.write_text(render_name_check_md(verdicts, "Project Testbed"))
        assert "Ashfell Advanced Materials Limited" in cast_list(path)


# ---------------------------------------------------------------------------
# Beyond-the-floor coverage: the `## Invented names` declared source, the
# person/entity tagging discipline, dedup precedence, and a real round trip
# through the actual gate-14 reader (not a reimplementation of it).
# ---------------------------------------------------------------------------

DECLARED_FACT_SHEET = textwrap.dedent(
    """
    # Fact sheet

    The target group trades under the Solmark brand and sells the
    Vantiq Edge product line from its Harrowgate Fulfilment Site,
    reachable at solmark-edge.example.

    ## Invented names

    | Name | Kind |
    |---|---|
    | Ashfell Holdings Limited | entity |
    | Solmark | brand |
    | Vantiq Edge | product |
    | Harrowgate Fulfilment Site | site |
    | solmark-edge.example | domain |

    ## Cast

    | Name | Role |
    |---|---|
    | Marta Vinceau | Chief executive |
    """
).strip()


def test_declared_table_round_trips_through_render_and_load_for_every_kind():
    declared_kinds = {"entity": "Ashfell Holdings Limited",
                       "brand": "Solmark",
                       "product": "Vantiq Edge",
                       "site": "Harrowgate Fulfilment Site",
                       "domain": "solmark-edge.example"}
    candidates = {c.text: c.kind for c in extract_candidates(DECLARED_FACT_SHEET)}
    for kind, text in declared_kinds.items():
        assert candidates[text] == kind

    verdicts = [
        Verdict(text, kind, "clear", "2026-08-24", "")
        for kind, text in declared_kinds.items()
    ]
    path_dir = tempfile.mkdtemp()
    path = pathlib.Path(path_dir) / "name-check.md"
    path.write_text(render_name_check_md(verdicts, "Project Testbed"))
    assert load_name_check(path) == verdicts


def test_cast_person_is_not_returned_by_entity_scoped_cast_list(tmp_path):
    candidates = extract_candidates(DECLARED_FACT_SHEET)
    person_rows = [c for c in candidates if c.text == "Marta Vinceau"]
    assert [c.kind for c in person_rows] == ["person"]

    verdicts = [Verdict(c.text, c.kind, "clear", "2026-08-24", "") for c in candidates]
    path = tmp_path / "name-check.md"
    path.write_text(render_name_check_md(verdicts, "Project Testbed"))
    assert "Marta Vinceau" not in cast_list(path, kind="entity")
    # but is present under the unrestricted read
    assert "Marta Vinceau" in cast_list(path, kind=None)


def test_name_in_both_declared_table_and_prose_appears_once_with_declared_kind():
    fact_sheet = textwrap.dedent(
        """
        # Fact sheet

        The subsidiary Ashfell Holdings Limited is wholly owned.

        ## Invented names

        | Name | Kind |
        |---|---|
        | Ashfell Holdings Limited | entity |
        """
    ).strip()
    candidates = extract_candidates(fact_sheet)
    matches = [c for c in candidates if c.text == "Ashfell Holdings Limited"]
    assert len(matches) == 1
    assert matches[0].kind == "entity"


def test_entity_beats_person_when_no_declared_table_disambiguates():
    # The same text reaches both the entity_tokens net (corporate suffix in
    # the prose) and a ## Cast row. With nothing declared, "entity" must
    # win — masking and searching it as a company is the conservative
    # treatment for gate 14.
    fact_sheet = textwrap.dedent(
        """
        # Fact sheet

        Ashfell Holdings Limited is the buyer.

        ## Cast

        | Name | Role |
        |---|---|
        | Ashfell Holdings Limited | Oddly named signatory |
        """
    ).strip()
    candidates = {c.text: c.kind for c in extract_candidates(fact_sheet)}
    assert candidates["Ashfell Holdings Limited"] == "entity"


def test_declared_kind_wins_over_entity_token_suffix_match():
    # A declared kind other than "entity" must override the entity_tokens
    # net when the same text also happens to carry a corporate suffix.
    fact_sheet = textwrap.dedent(
        """
        # Fact sheet

        Solmark Trading Limited is the operating subsidiary.

        ## Invented names

        | Name | Kind |
        |---|---|
        | Solmark Trading Limited | brand |
        """
    ).strip()
    candidates = {c.text: c.kind for c in extract_candidates(fact_sheet)}
    assert candidates["Solmark Trading Limited"] == "brand"
