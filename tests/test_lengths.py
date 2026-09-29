import shutil
from dataclasses import replace

import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, DomainError, load_domain
from synthvdr.houseforms import families, house_form_of
from synthvdr.lengths import (
    SHORT_DOC_WEIGHT,
    Band,
    LengthRow,
    Lengths,
    brief_for,
    check_against_pack,
    load_lengths,
)
from synthvdr.slots import SIZE_PRESETS, build_slot_manifest

PACK = load_domain(DEFAULT_DOMAIN_ROOT)
LENGTHS = load_lengths(DEFAULT_DOMAIN_ROOT, PACK)


def _lengths(**overrides):
    fields = dict(
        words_per_page=LENGTHS.words_per_page,
        bands=LENGTHS.bands,
        rows=LENGTHS.rows,
        families=LENGTHS.families,
    )
    fields.update(overrides)
    return Lengths(**fields)


def test_ships_the_four_bands_the_spec_names_at_500_words_per_page():
    assert LENGTHS.words_per_page == 500
    assert {n: (b.pages, b.floor, b.target) for n, b in LENGTHS.bands.items()} == {
        "heavy": ((35, 45), 17500, 20000),
        "principal": ((20, 30), 10000, 12000),
        "mid": ((10, 15), 5000, 6000),
        "short": ((4, 8), 2000, 2500),
    }


def test_ships_twenty_five_agreement_rows_and_twenty_families():
    assert len(LENGTHS.rows) == 25
    assert len(LENGTHS.families) == 20
    assert {r.anatomy for r in LENGTHS.rows} == set(LENGTHS.families)


def test_target_paper_is_exactly_the_seven_subfolders_the_spec_names():
    target = {(r.section, r.subsection) for r in LENGTHS.rows if r.paper == "target"}
    assert target == {
        ("05_commercial", "customer-contracts"),
        ("05_commercial", "distribution"),
        ("05_commercial", "framework-agreements"),
        ("05_commercial", "ndas"),
        ("06_intellectual-property", "licences-out"),
        ("09_employment", "contracts"),
        ("14_data-protection", "processor-agreements"),
    }


@pytest.mark.parametrize("size,expected", [("XS", 14), ("S", 20), ("M", 60), ("L", 235)])
def test_agreement_slot_counts_per_preset_match_the_spec(size, expected):
    slots = build_slot_manifest(PACK, SIZE_PRESETS[size])
    assert sum(1 for s in slots if LENGTHS.row_for_rel_path(s.rel_path)) == expected


def test_rows_are_keyed_by_section_as_well_as_subsection():
    # "policies" is 9.2, 12.2 and 19.1; only the insurance one is an agreement.
    assert LENGTHS.row_for("12_insurance", "policies").anatomy == "insurance-policy"
    assert LENGTHS.row_for("09_employment", "policies") is None
    assert LENGTHS.row_for("19_esg", "policies") is None


def test_row_for_rel_path_reads_the_last_three_path_components():
    rel = "05_commercial/5.1_customer-contracts/5.1.3_customer-contracts-03.md"
    assert (LENGTHS.row_for_rel_path(rel).band, LENGTHS.row_for_rel_path(rel).paper) == (
        "principal",
        "target",
    )
    assert LENGTHS.row_for_rel_path("/abs/room/data-room/" + rel).band == "principal"
    assert LENGTHS.row_for_rel_path("01_corporate/1.3_board-minutes/1.3.1_board-minutes-01.md") is None
    assert LENGTHS.row_for_rel_path("not-a-slot.md") is None


def test_weight_is_the_band_target_for_an_agreement_and_flat_otherwise():
    assert LENGTHS.weight_for("18_transaction/18.2_draft-spa/18.2.1_draft-spa-01.md") == 20000
    assert (
        LENGTHS.weight_for("01_corporate/1.3_board-minutes/1.3.1_board-minutes-01.md")
        == SHORT_DOC_WEIGHT
        == 1200
    )


def test_refuses_a_floor_that_is_not_the_bottom_of_its_page_range():
    bands = dict(LENGTHS.bands, principal=Band("principal", (20, 30), 9000, 12000))
    with pytest.raises(DomainError, match="principal.*floor"):
        _lengths(bands=bands)


def test_refuses_a_target_above_the_top_of_its_page_range():
    bands = dict(LENGTHS.bands, short=Band("short", (4, 8), 2000, 4500))
    with pytest.raises(DomainError, match="short.*target"):
        _lengths(bands=bands)


@pytest.mark.parametrize("field,value", [("band", "enormous"), ("paper", "ours"), ("anatomy", "novella")])
def test_refuses_a_row_naming_something_that_does_not_exist(field, value):
    bad = replace(LENGTHS.rows[0], **{field: value})
    with pytest.raises(DomainError, match=repr(value)):
        _lengths(rows=(bad,) + LENGTHS.rows[1:])


def test_refuses_the_same_subfolder_twice():
    with pytest.raises(DomainError, match="twice"):
        _lengths(rows=LENGTHS.rows + (LENGTHS.rows[0],))


def test_refuses_family_shares_that_do_not_sum_to_one_hundred():
    nda = LENGTHS.families["nda"]
    with pytest.raises(DomainError, match="nda.*100"):
        _lengths(families=dict(LENGTHS.families, nda=replace(nda, parts=nda.parts[:-1])))


def test_refuses_a_row_for_a_subsection_the_pack_does_not_have():
    extra = LengthRow("05_commercial", "side-letters", "short", "target", "nda", "Side letter")
    with pytest.raises(DomainError, match="side-letters"):
        check_against_pack(_lengths(rows=LENGTHS.rows + (extra,)), PACK)


def test_refuses_a_long_floor_below_what_the_same_slot_gets_in_short_mode():
    # draft-spa's short-mode floor is the longform archetype's 2,500; the short band is 2,000.
    rows = tuple(replace(r, band="short") if r.subsection == "draft-spa" else r for r in LENGTHS.rows)
    with pytest.raises(DomainError, match="draft-spa"):
        check_against_pack(_lengths(rows=rows), PACK)


def test_load_lengths_names_the_pack_root_when_it_refuses(tmp_path):
    root = tmp_path / "pack"
    shutil.copytree(DEFAULT_DOMAIN_ROOT, root)
    lengths_yaml = root / "lengths.yaml"
    lengths_yaml.write_text(lengths_yaml.read_text().replace("floor: 10000", "floor: 9000"))
    with pytest.raises(DomainError, match=str(root)):
        load_lengths(root, PACK)


PARENTS = house_form_of(families(build_slot_manifest(PACK, SIZE_PRESETS["M"]), LENGTHS))


def test_brief_names_band_target_parts_and_clauses_for_an_agreement():
    brief = brief_for("05_commercial/5.2_supplier-contracts/5.2.1_supplier-contracts-01.md", LENGTHS, PARENTS)
    assert "about 12,000 words; the floor is 10,000" in brief
    assert "  - Definitions and interpretation: ~1,400 words" in brief
    assert "Change of control" in brief
    assert "House form" not in brief and "Derived contract" not in brief


def test_brief_is_none_for_a_slot_that_is_not_an_agreement():
    assert brief_for("01_corporate/1.3_board-minutes/1.3.1_board-minutes-01.md", LENGTHS, PARENTS) is None


def test_brief_tells_a_house_form_and_a_derived_contract_apart():
    derived, house = next(iter(sorted(PARENTS.items())))
    assert "House form" in brief_for(house, LENGTHS, PARENTS)
    derived_brief = brief_for(derived, LENGTHS, PARENTS)
    assert "Derived contract" in derived_brief and house in derived_brief
    # Final review Important 3: gate 22 counts an edit inside a blank-carrying paragraph as a
    # fill, so the brief must say where the negotiated changes go.
    assert "at least two negotiated changes to clauses that carry no blank in the house form" in derived_brief
