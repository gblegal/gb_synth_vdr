import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.slots import (
    SIZE_PRESETS,
    build_slot_manifest,
    read_anchors_csv,
    write_anchors_csv,
    _allocate,
)


def manifest(preset_name="M"):
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    return pack, build_slot_manifest(pack, SIZE_PRESETS[preset_name])


def test_manifest_has_exactly_the_preset_document_count():
    _, slots = manifest("M")
    assert len(slots) == SIZE_PRESETS["M"].docs == 200


def test_slot_ids_are_unique_and_paths_are_unique():
    _, slots = manifest("M")
    assert len({s.slot_id for s in slots}) == len(slots)
    assert len({s.rel_path for s in slots}) == len(slots)


def test_every_section_receives_at_least_one_slot():
    pack, slots = manifest("M")
    assert {s.section_dir for s in slots} == set(pack.section_dirs())


def test_build_is_deterministic():
    _, first = manifest("M")
    _, second = manifest("M")
    assert [s.rel_path for s in first] == [s.rel_path for s in second]


def test_anchor_share_is_between_a_quarter_and_a_half():
    _, slots = manifest("M")
    anchors = [s for s in slots if s.tier == "A"]
    assert 0.25 <= len(anchors) / len(slots) <= 0.5


def test_xs_preset_is_the_forty_document_fixture_size():
    _, slots = manifest("XS")
    assert len(slots) == 40


def test_anchors_csv_round_trips(tmp_path):
    _, slots = manifest("S")
    path = tmp_path / "anchors.csv"
    write_anchors_csv(slots, path)
    tiers = read_anchors_csv(path)
    assert tiers == {s.slot_id: s.tier for s in slots}


def test_subsections_are_contiguous_per_section():
    """For each section, subsections must appear in runs, never interleaved."""
    _, slots = manifest("M")
    for section_dir in {s.section_dir for s in slots}:
        section_slots = [s for s in slots if s.section_dir == section_dir]
        seen: list[str] = []
        for slot in section_slots:
            # When subsection changes, assert it has not been seen before (contiguity)
            if not seen or slot.subsection != seen[-1]:
                assert slot.subsection not in seen, (
                    f"{slot.subsection} reappears after a different subsection in {section_dir}"
                )
                seen.append(slot.subsection)


def test_all_presets_exact_count_and_uniqueness():
    """Verify all five presets produce exact document count with unique IDs and paths."""
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    for preset_name, preset in SIZE_PRESETS.items():
        slots = build_slot_manifest(pack, preset)
        assert len(slots) == preset.docs, f"{preset_name}: count mismatch"
        assert len({s.slot_id for s in slots}) == len(slots), f"{preset_name}: non-unique slot_id"
        assert len({s.rel_path for s in slots}) == len(slots), f"{preset_name}: non-unique rel_path"


def test_allocate_raises_when_total_below_section_count():
    """_allocate should raise ValueError when total < section count."""
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    with pytest.raises(ValueError, match="below the section count"):
        _allocate(pack, len(pack.sections) - 1)
