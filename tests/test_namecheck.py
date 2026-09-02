import pathlib
import tempfile
import textwrap

import pytest

from synthvdr.namecheck import (
    KINDS,
    VERDICTS,
    CandidateName,
    NameCheckError,
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



# ---------------------------------------------------------------------------
# Fix round 1: a person declared with a non-person Kind, and an unrecognised
# or blank Kind, are both authoring errors that must stop /vdr-scope rather
# than resolve silently. Both raise NameCheckError.
# ---------------------------------------------------------------------------


def test_person_declared_as_non_person_kind_is_a_contradiction():
    # Declaring "entity" for a name that ## Cast says is a person is a
    # self-contradictory fact sheet. Silently preferring the declared kind
    # (the ordinary precedence rule) would mask this name out of the room
    # as a company and blind gate 14 to unchecked names beside it.
    fact_sheet = textwrap.dedent(
        """
        # Fact sheet

        ## Invented names

        | Name | Kind |
        |---|---|
        | Marta Vinceau | entity |

        ## Cast

        | Name | Role |
        |---|---|
        | Marta Vinceau | Chief executive |
        """
    ).strip()
    with pytest.raises(NameCheckError, match="Marta Vinceau"):
        extract_candidates(fact_sheet)


def test_person_declared_as_person_and_also_cast_is_not_a_contradiction():
    # This is the case the guard above must NOT overfire on: declaring the
    # row as Kind "person" is exactly how an author states the overlap
    # with ## Cast is deliberate, not an error.
    fact_sheet = textwrap.dedent(
        """
        # Fact sheet

        ## Invented names

        | Name | Kind |
        |---|---|
        | Marta Vinceau | person |

        ## Cast

        | Name | Role |
        |---|---|
        | Marta Vinceau | Chief executive |
        """
    ).strip()
    candidates = {c.text: c.kind for c in extract_candidates(fact_sheet)}
    assert candidates["Marta Vinceau"] == "person"


@pytest.mark.parametrize("bad_kind", ["prodcut", ""])
def test_unrecognised_or_blank_declared_kind_raises(bad_kind):
    fact_sheet = textwrap.dedent(
        f"""
        # Fact sheet

        ## Invented names

        | Name | Kind |
        |---|---|
        | Veltrix | {bad_kind} |
        """
    ).strip()
    with pytest.raises(NameCheckError, match="Veltrix"):
        extract_candidates(fact_sheet)


def test_all_recognised_kinds_are_accepted_without_raising():
    # Pins the valid set so a future edit to KINDS is deliberate, not an
    # accidental typo that starts rejecting a previously-valid Kind.
    assert set(KINDS) == {"entity", "brand", "product", "site", "domain", "person"}


def test_the_verdict_vocabulary_is_closed_and_pinned():
    # Same discipline as the KINDS pin above. `unchecked` was missing from
    # this tuple for as long as the tuple went unreferenced by anything —
    # nothing read it, so nothing could notice what it omitted.
    assert set(VERDICTS) == {"clear", "collision", "ambiguous", "unchecked"}



# ---------------------------------------------------------------------------
# Fix round 2: duplicate declared rows with a genuine kind conflict, a Name
# the pipe-table format cannot carry as a key (a literal '|', or text that
# would vanish as a separator row), a Note sanitised rather than rejected,
# and a pin proving names_needing_check is exact-match, not folded.
# ---------------------------------------------------------------------------


def test_duplicate_declared_rows_with_different_kinds_raise():
    fact_sheet = textwrap.dedent(
        """
        # Fact sheet

        ## Invented names

        | Name | Kind |
        |---|---|
        | Solmark | brand |
        | Solmark | product |
        """
    ).strip()
    # Tightened per re-review finding 5: matching only "Solmark" would still
    # pass under a mutation that raised for an unrelated reason. Pin that
    # the message names the offending text AND both conflicting kinds.
    with pytest.raises(NameCheckError) as excinfo:
        extract_candidates(fact_sheet)
    message = str(excinfo.value)
    assert "Solmark" in message
    assert "brand" in message
    assert "product" in message


def test_duplicate_declared_rows_with_the_same_kind_do_not_raise():
    # The negative case: repetition alone is not the defect, a genuine
    # kind conflict is.
    fact_sheet = textwrap.dedent(
        """
        # Fact sheet

        ## Invented names

        | Name | Kind |
        |---|---|
        | Solmark | brand |
        | Solmark | brand |
        """
    ).strip()
    candidates = {c.text: c.kind for c in extract_candidates(fact_sheet)}
    assert candidates["Solmark"] == "brand"


def test_render_raises_on_a_pipe_in_the_name():
    verdicts = [Verdict("Ashfell | Corp", "entity", "clear", "2026-08-24", "")]
    with pytest.raises(NameCheckError, match="literal"):
        render_name_check_md(verdicts, "Project Testbed")


def test_render_raises_on_a_name_that_would_vanish_as_a_separator_row():
    # Verdict("---", ...) must not silently round-trip to zero rows.
    verdicts = [Verdict("---", "entity", "clear", "2026-08-24", "")]
    with pytest.raises(NameCheckError, match="separator"):
        render_name_check_md(verdicts, "Project Testbed")


def test_render_raises_on_an_empty_name():
    verdicts = [Verdict("", "entity", "clear", "2026-08-24", "")]
    # Tightened per re-review finding 5: matching only "separator" is a
    # message a sibling test (the "---" case) shares, so this test did not
    # actually pin the empty-specific path. Anchor on the empty-string
    # repr too, so the test fails if the guard stops recognising blank
    # text as its own case.
    with pytest.raises(NameCheckError, match=r"''.*empty"):
        render_name_check_md(verdicts, "Project Testbed")


# ---------------------------------------------------------------------------
# Review item 04: VERDICTS was referenced nowhere AND omitted `unchecked`,
# the value /vdr-scope tells the author to record when WebSearch is
# unavailable. The vocabulary is enforced where the record is WRITTEN; the
# loader stays permissive because gate 14 is built to WARN on a verdict it
# does not recognise rather than crash on one.


def test_unchecked_is_in_the_vocabulary_and_renders():
    # /vdr-scope: "record every affected name's verdict as `unchecked` with a
    # note explaining why". Rendering it must not raise.
    assert "unchecked" in VERDICTS
    verdicts = [
        Verdict(
            "Ashfell Holdings Limited",
            "entity",
            "unchecked",
            "2026-08-24",
            "WebSearch unavailable in this session",
        )
    ]
    out = render_name_check_md(verdicts, "Project Testbed")
    assert "| Ashfell Holdings Limited | entity | unchecked | 2026-08-24 |" in out


def test_render_raises_on_a_verdict_outside_the_vocabulary():
    # A typo caught while the record is being written costs a retype. The
    # same typo reaching the file uncaught is read by gate 14 as a name that
    # is not cleared, which is indistinguishable from a real finding.
    verdicts = [Verdict("Ashfell Holdings Limited", "entity", "clera", "2026-08-24", "")]
    with pytest.raises(NameCheckError, match=r"'clera'"):
        render_name_check_md(verdicts, "Project Testbed")


def test_load_name_check_still_returns_an_unrecognised_verdict(tmp_path):
    # DELIBERATELY PERMISSIVE, and gate 14 depends on it: a hand-edited
    # typo must reach gate_14_unchecked_names as a non-clear verdict it can
    # WARN about, not as an exception out of the loader. Written by hand
    # rather than through render_name_check_md, because the renderer is
    # exactly what now refuses to produce this file.
    path = tmp_path / "name-check.md"
    path.write_text(
        "| Name | Kind | Verdict | Checked | Note |\n"
        "|---|---|---|---|---|\n"
        "| Ashfell Holdings Limited | entity | clera | 2026-08-24 | typo |\n",
        encoding="utf-8",
    )
    loaded = load_name_check(path)
    assert [v.verdict for v in loaded] == ["clera"]
    assert [v.text for v in unresolved(loaded)] == ["Ashfell Holdings Limited"]


def test_render_sanitises_a_pipe_in_the_note_without_truncating(tmp_path):
    long_note = "shares a name with a public figure | possible false positive"
    verdicts = [
        Verdict("Ashfell Holdings Limited", "entity", "ambiguous", "2026-08-24", long_note)
    ]
    path = tmp_path / "name-check.md"
    path.write_text(render_name_check_md(verdicts, "Project Testbed"))
    loaded = load_name_check(path)
    assert len(loaded) == 1
    # Sanitised, not truncated at the first pipe: the whole note survives.
    assert loaded[0].note == long_note.replace("|", "/")
    assert "possible false positive" in loaded[0].note


@pytest.mark.parametrize(
    "existing_text",
    ["ashfell holdings limited", " Ashfell Holdings Limited ", "ASHFELL HOLDINGS LIMITED"],
)
def test_names_needing_check_does_not_fold_case_or_whitespace(existing_text):
    # Pin for the exact-match behaviour already shipped: folding case or
    # whitespace here would create a false "already checked" when the
    # fact sheet's exact text differs from what was actually searched.
    candidates = [CandidateName("Ashfell Holdings Limited", "entity")]
    existing = [Verdict(existing_text, "entity", "clear", "2026-08-24", "")]
    todo = names_needing_check(candidates, existing)
    assert [c.text for c in todo] == ["Ashfell Holdings Limited"]



# ---------------------------------------------------------------------------
# Fix round 3: the guard is the round trip itself, not a checklist of
# forbidden shapes. Six bypasses the re-review found against round 2's
# shape-based guard, plus a positive list of plausible names that must
# render and load cleanly — a guard that overfires on any of those is a
# worse outcome than the bug it closes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "---",  # already caught by round 2; kept as the control case
        " - ",
        "   ---   ",
        "   ",
        " : ",
        "Corrupt\nName Corp Limited",
        "Carriage\rReturn Ltd",
    ],
)
def test_render_raises_on_names_that_do_not_survive_the_round_trip(bad_name):
    verdicts = [Verdict(bad_name, "entity", "clear", "2026-08-24", "")]
    with pytest.raises(NameCheckError):
        render_name_check_md(verdicts, "Project Testbed")


@pytest.mark.parametrize(
    "good_name",
    [
        "Ashfell Ltd",
        "Ashfell-Brandt Limited",
        "Ashfell & Co",
        "Kessler Werke GmbH & Co. KG",
        "ashfell.example",
        "Ashfell 2 Limited",
        "Tab	Separated Ltd",
        "O’Ashfell Limited",  # curly apostrophe
        "Ashfell Inc.",  # trailing full stop
    ],
)
def test_render_round_trips_plausible_names_cleanly(good_name, tmp_path):
    verdicts = [Verdict(good_name, "entity", "clear", "2026-08-24", "a note")]
    path = tmp_path / "name-check.md"
    path.write_text(render_name_check_md(verdicts, "Project Testbed"))
    loaded = load_name_check(path)
    # Field for field, not just "some row came back" — the whole Verdict
    # must survive unchanged.
    assert loaded == verdicts


# --- every matching heading's table is read, not just the first -----------
#
# _table_rows used to `break` on the first unrelated `##` heading, silently
# ending the scan. integrity.parse_canonical_figures had the identical bug
# over the identical file and was fixed the identical way ("Final review,
# F3"). It matters more here: a brand, product, site or domain carries no
# corporate suffix, so entity_tokens cannot find it and `## Invented names`
# is its ONLY route into the check.

SPLIT_FACT_SHEET = """# Project Testbed — fact sheet

## Invented names

| Name | Kind |
|---|---|
| Ashfell Holdings Limited | entity |

## Deal summary

Some prose that ends the first table.

## Invented names

| Name | Kind |
|---|---|
| Loomwright | brand |
| ashfell.example | domain |

## Cast

| Name | Role |
|---|---|
| Priya Nandan | Finance Director |

## Notes

More prose.

## Cast

| Name | Role |
|---|---|
| Owen Kasprzak | General Counsel |
"""


def test_declared_names_under_a_second_invented_names_heading_are_still_read():
    found = {c.text: c.kind for c in extract_candidates(SPLIT_FACT_SHEET)}
    assert found["Ashfell Holdings Limited"] == "entity", "first table must still be read"
    assert found["Loomwright"] == "brand", "a second ## Invented names table must be read too"
    assert found["ashfell.example"] == "domain"


def test_cast_rows_under_a_second_cast_heading_are_still_read():
    found = {c.text: c.kind for c in extract_candidates(SPLIT_FACT_SHEET)}
    assert found["Priya Nandan"] == "person"
    assert found["Owen Kasprzak"] == "person", "a second ## Cast table must be read too"


def test_a_contradiction_in_a_second_table_is_still_caught():
    """The scan reaching further must not be a way to smuggle a
    contradiction past _check_declared_person_consistency — the person/entity
    conflict is exactly the masking hazard that check exists for."""
    text = (
        "## Cast\n\n| Name | Role |\n|---|---|\n| Priya Nandan | FD |\n\n"
        "## Notes\n\nprose\n\n"
        "## Invented names\n\n| Name | Kind |\n|---|---|\n| Priya Nandan | entity |\n"
    )
    with pytest.raises(NameCheckError, match="Priya Nandan"):
        extract_candidates(text)


def test_a_name_wrapped_across_a_line_break_in_prose_is_one_candidate():
    # Defect 2, in the shape it was found: fact-sheet prose wraps, and the
    # belt-and-braces suffix net used to stop at the line break — the name
    # was not extracted at all, and nothing downstream ever checked it. The
    # workaround was keeping entity names on one line, a constraint on the
    # author that the tool should absorb.
    fact_sheet = textwrap.dedent(
        """
        # Fact sheet

        The group's operating subsidiary is The Helmswick Imaging
        Group Limited, which trades from twelve centres.
        """
    ).strip()
    names = {c.text for c in extract_candidates(fact_sheet) if c.kind == "entity"}
    assert names == {"Helmswick Imaging Group Limited"}, (
        "the name must be extracted whole, spelled the way the name check "
        "will record it — no line break, and no leading determiner that "
        "would make it a different name from the one the author declares"
    )


def test_the_participle_does_not_ride_along_on_a_fact_sheet_name():
    # Where this defect actually landed: `extract_candidates` runs the
    # suffix net over fact-sheet prose UNMASKED, so /vdr-scope recorded
    # "... Limited incorporated" as a name to go and search.
    fact_sheet = textwrap.dedent(
        """
        # Fact sheet

        The target is Ashfell Advanced Materials Limited incorporated in
        England on 4 June 2004.
        """
    ).strip()
    names = {c.text for c in extract_candidates(fact_sheet) if c.kind == "entity"}
    assert names == {"Ashfell Advanced Materials Limited"}
