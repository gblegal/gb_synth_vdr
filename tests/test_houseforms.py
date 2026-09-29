import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.houseforms import (
    authored_paths,
    blanks,
    derivation_problems,
    families,
    house_form_of,
    house_form_paths,
    ready_slots,
    seed,
)
from synthvdr.lengths import load_lengths
from synthvdr.slots import SIZE_PRESETS, build_slot_manifest

PACK = load_domain(DEFAULT_DOMAIN_ROOT)
LENGTHS = load_lengths(DEFAULT_DOMAIN_ROOT, PACK)

HOUSE = """# Standard Terms of Supply

[Template — not for signature]

This agreement is made between [Supplier name] and [Customer name] on [Commencement Date].

1.1 The Supplier shall supply the Goods in accordance with the Specification and the Service Levels set out in Schedule 2, and shall use all reasonable endeavours to meet each delivery date.

1.2 The Customer shall pay each undisputed invoice within thirty days of receipt, and interest shall accrue on late payment at four per cent above the base rate from time to time.

1.3 Either party may terminate this agreement on six months' written notice to the other, and termination shall not affect any rights accrued before the date on which it takes effect.

1.4 Neither party shall assign, transfer or subcontract any of its rights or obligations under this agreement without the prior written consent of the other party.
"""


def filled(text=HOUSE):
    return (
        text.replace("[Template — not for signature]\n\n", "")
        .replace("[Supplier name]", "Ashfell Components Limited")
        .replace("[Customer name]", "Brindlecote Retail Limited")
        .replace("[Commencement Date]", "1 March 2026")
    )


def slots_for(size):
    return build_slot_manifest(PACK, SIZE_PRESETS[size])


@pytest.mark.parametrize("size,count,derived", [("XS", 0, 0), ("S", 0, 0), ("M", 5, 15), ("L", 7, 85)])
def test_family_counts_per_preset_match_the_spec(size, count, derived):
    fams = families(slots_for(size), LENGTHS)
    assert len(fams) == count
    assert sum(len(f.derived) for f in fams) == derived


def test_the_house_form_is_the_ordinal_one_slot_of_a_target_paper_subfolder():
    for fam in families(slots_for("L"), LENGTHS):
        assert fam.house_form.slot_id.endswith(".1")
        assert LENGTHS.row_for_rel_path(fam.house_form.rel_path).paper == "target"
        assert all(d.subsection == fam.house_form.subsection for d in fam.derived)


def test_families_do_not_depend_on_the_order_slots_arrive_in():
    slots = slots_for("M")
    assert families(slots, LENGTHS) == families(list(reversed(slots)), LENGTHS)


def test_blanks_are_bracketed_labels_starting_with_a_capital():
    assert blanks(HOUSE) == {
        "[Template — not for signature]",
        "[Supplier name]",
        "[Customer name]",
        "[Commencement Date]",
    }
    assert blanks("[signature page not scanned] and [a] and [BUYER]") == {"[BUYER]"}


def test_an_unedited_copy_fails_on_its_blanks_and_its_sameness():
    problems = derivation_problems(HOUSE, HOUSE)
    assert len(problems) == 2
    assert "blank" in problems[0] and "differs" in problems[1]


def test_filling_the_blanks_alone_is_not_enough():
    assert derivation_problems(HOUSE, filled()) == [
        "differs from its house form in 0 paragraph(s) beyond the blanks; "
        "at least 2 negotiated changes are required"
    ]


def test_filled_blanks_plus_two_negotiated_changes_pass():
    derived = filled().replace("within thirty days", "within sixty days").replace(
        "prior written consent", "prior written consent (not to be unreasonably withheld)"
    )
    assert derivation_problems(HOUSE, derived) == []


def test_a_negotiated_insertion_counts_as_a_deviation():
    derived = filled().replace("within thirty days", "within forty-five days") + (
        "\n1.5 The Customer may audit the Supplier's compliance with the Service Levels once in "
        "each contract year on not less than twenty business days' written notice.\n"
    )
    assert derivation_problems(HOUSE, derived) == []


def test_crlf_derived_contract_is_compared_by_paragraph_not_line_ending():
    derived = filled().replace("within thirty days", "within sixty days").replace(
        "six months'", "three months'"
    )
    assert derivation_problems(HOUSE, derived.replace("\n", "\r\n")) == []


def _write_house_form(blind, fam, text=HOUSE):
    path = blind / fam.house_form.rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_seed_copies_the_house_form_and_never_overwrites(tmp_path):
    fams = families(slots_for("M"), LENGTHS)
    fam = fams[0]
    blind = tmp_path / "data-room"
    _write_house_form(blind, fam)
    existing = blind / fam.derived[0].rel_path
    existing.write_text("already authored")

    created = seed(blind, fams)

    assert fam.derived[0].rel_path not in created
    assert existing.read_text() == "already authored"
    for slot in fam.derived[1:]:
        assert (blind / slot.rel_path).read_text() == HOUSE
    assert seed(blind, fams) == []


def test_seed_skips_a_family_whose_house_form_is_not_written(tmp_path):
    assert seed(tmp_path / "data-room", families(slots_for("M"), LENGTHS)) == []


def test_seed_limits_itself_to_the_slots_it_is_given(tmp_path):
    fams = families(slots_for("M"), LENGTHS)
    blind = tmp_path / "data-room"
    _write_house_form(blind, fams[0])
    only = {fams[0].derived[0].rel_path}
    assert seed(blind, fams, only=only) == [fams[0].derived[0].rel_path]


def test_seed_does_not_write_through_a_dangling_symlink(tmp_path):
    fams = families(slots_for("M"), LENGTHS)
    blind = tmp_path / "data-room"
    _write_house_form(blind, fams[0])
    target = blind / fams[0].derived[0].rel_path
    outside = tmp_path / "outside.md"
    target.symlink_to(outside)
    seed(blind, fams)
    assert not outside.exists()


def test_authored_paths_excludes_a_seeded_but_unedited_derived_contract(tmp_path):
    # Review Focus 2: an interrupted wave leaves seeded copies behind; a resumed
    # build must re-dispatch them, not skip them.
    slots = slots_for("M")
    fams = families(slots, LENGTHS)
    blind = tmp_path / "data-room"
    _write_house_form(blind, fams[0])
    seed(blind, fams, only={fams[0].derived[0].rel_path})
    done = authored_paths(blind, slots, fams)
    assert fams[0].house_form.rel_path in done
    assert fams[0].derived[0].rel_path not in done

    (blind / fams[0].derived[0].rel_path).write_text(
        filled().replace("within thirty days", "within sixty days").replace("six months'", "three months'")
    )
    assert fams[0].derived[0].rel_path in authored_paths(blind, slots, fams)


def test_ready_slots_defers_a_derived_slot_until_its_house_form_is_authored():
    slots = slots_for("M")
    fams = families(slots, LENGTHS)
    derived = fams[0].derived[0]
    assert derived not in ready_slots(slots, set(), fams)
    assert derived in ready_slots(slots, {fams[0].house_form.rel_path}, fams)
    assert fams[0].house_form not in ready_slots(slots, {fams[0].house_form.rel_path}, fams)


def test_house_form_of_maps_every_derived_slot_to_its_house_form():
    fams = families(slots_for("M"), LENGTHS)
    mapping = house_form_of(fams)
    assert len(mapping) == 15
    assert set(mapping.values()) == house_form_paths(fams)
