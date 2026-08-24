from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.slots import (
    SIZE_PRESETS,
    build_slot_manifest,
    read_anchors_csv,
    write_anchors_csv,
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


def test_xs_preset_is_the_twenty_document_fixture_size():
    _, slots = manifest("XS")
    assert len(slots) == 20


def test_anchors_csv_round_trips(tmp_path):
    _, slots = manifest("S")
    path = tmp_path / "anchors.csv"
    write_anchors_csv(slots, path)
    tiers = read_anchors_csv(path)
    assert tiers == {s.slot_id: s.tier for s in slots}
