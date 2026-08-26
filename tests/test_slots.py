import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.slots import (
    SIZE_PRESETS,
    authoring_order,
    build_slot_manifest,
    read_anchors_csv,
    read_slot_manifest,
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


# ---------------------------------------------------------------------------
# Review 2026-08-26, B2. `/vdr-build`'s ordering rule said "sort by tier, `A`
# (anchor — carries a finding, a distractor, or is otherwise load-bearing)
# before `F`". Tier means nothing of the sort: `build_slot_manifest` assigns it
# POSITIONALLY, at /vdr-scope time, before a single finding exists — the first
# ~35% of each section's slots are `A`. In the XS build that surfaced this, 6 of
# the 10 registry evidence paths were tier `F`, so following the rule literally
# deferred half the findings' evidence behind every filler-tier document and
# defeated the invariant it was written to protect.
#
# The rule now lives in code: load-bearing first, tier order after.
# ---------------------------------------------------------------------------


def test_authoring_order_puts_load_bearing_slots_before_every_other_slot():
    _, slots = manifest("XS")
    # Deliberately pick load-bearing paths from the tail of the manifest, which
    # is where positional tiering puts filler — the exact case the old rule got
    # backwards.
    load_bearing = {slots[-1].rel_path, slots[-3].rel_path}

    ordered = authoring_order(slots, load_bearing)

    assert {s.rel_path for s in ordered[:2]} == load_bearing
    assert len(ordered) == len(slots)
    assert {s.rel_path for s in ordered} == {s.rel_path for s in slots}


def test_authoring_order_puts_tier_a_before_tier_f_among_the_rest():
    _, slots = manifest("XS")
    ordered = authoring_order(slots, set())
    tiers = [s.tier for s in ordered]
    assert tiers == sorted(tiers), "tier A must still lead tier F once nothing is load-bearing"


def test_authoring_order_keeps_manifest_order_within_a_group():
    # Stability matters: slots the sort cannot separate must stay in manifest
    # order, so a resumed build picks up where the manifest says. The groups are
    # the sort key's own — (load-bearing, tier) — NOT load-bearing alone: tier
    # still applies as the second key inside the load-bearing block, so a
    # tier-`A` evidence slot leads a tier-`F` one there too.
    _, slots = manifest("XS")
    load_bearing = {s.rel_path for s in (slots[2], slots[3], slots[-1], slots[-4])}
    assert {s.tier for s in slots if s.rel_path in load_bearing} == {"A", "F"}, (
        "this test is only meaningful if the load-bearing set spans both tiers"
    )

    ordered = authoring_order(slots, load_bearing)
    manifest_position = {s.rel_path: i for i, s in enumerate(slots)}

    for bearing in (True, False):
        for tier in ("A", "F"):
            members = [
                s for s in ordered
                if (s.rel_path in load_bearing) is bearing and s.tier == tier
            ]
            positions = [manifest_position[s.rel_path] for s in members]
            assert positions == sorted(positions), (
                f"group (load_bearing={bearing}, tier={tier}) is out of manifest order"
            )


def test_authoring_order_orders_by_tier_inside_the_load_bearing_block_too():
    # Pinning the consequence the test above documents, so it is a decision on
    # the record rather than a side effect nobody chose: within the block, the
    # deeper documents are written first.
    _, slots = manifest("XS")
    load_bearing = {s.rel_path for s in (slots[2], slots[3], slots[-1], slots[-4])}
    block = [
        s for s in authoring_order(slots, load_bearing) if s.rel_path in load_bearing
    ]
    assert [s.tier for s in block] == sorted(s.tier for s in block)


def test_authoring_order_ignores_a_load_bearing_path_that_is_not_a_slot():
    # An evidence path that matches no slot is a findings.yaml defect, caught by
    # gate 12 and by build_flagged_tree — not something this ordering helper
    # should raise on or silently invent a slot for.
    _, slots = manifest("XS")
    ordered = authoring_order(slots, {"99_nowhere/99.1_nothing/99.1.1_absent-01.md"})
    assert [s.rel_path for s in ordered] == [
        s.rel_path for s in authoring_order(slots, set())
    ]


def test_read_slot_manifest_round_trips_every_field_authoring_order_needs(tmp_path):
    # authoring_order matches slots against evidence paths, so reading
    # anchors.csv back must recover rel_path — not just the tier map gate 10
    # needs.
    _, slots = manifest("S")
    path = tmp_path / "anchors.csv"
    write_anchors_csv(slots, path)
    assert read_slot_manifest(path) == slots
