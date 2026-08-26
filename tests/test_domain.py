import shutil

import pytest

from synthvdr.domain import (
    DEFAULT_DOMAIN_ROOT,
    Archetype,
    DomainError,
    DomainPack,
    Section,
    load_domain,
)


def test_ships_twenty_sections_in_canonical_order():
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    assert len(pack.sections) == 20
    assert pack.sections[0].dir_name == "01_corporate"
    assert pack.sections[-1].dir_name == "20_jv-minority-interests"
    assert [s.number for s in pack.sections] == list(range(1, 21))


def test_section_dirs_match_dir_name_field():
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    assert pack.section_dirs() == [s.dir_name for s in pack.sections]


def test_weights_sum_to_one():
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    assert abs(sum(s.weight for s in pack.sections) - 1.0) < 1e-6


def test_every_archetype_declares_a_positive_floor():
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    assert pack.archetypes
    assert all(a.floor > 0 for a in pack.archetypes.values())


def test_shipped_domain_pack_pins_its_exact_depth_floors():
    """Final review, test-suite gap: `test_every_archetype_declares_a_positive_floor`
    above only checks `> 0` — quartering every floor in the shipped
    `domain/ma/archetypes.yaml` (tier_f_floor 350 -> 87, every archetype floor
    divided by 4) still satisfies ">0" and leaves the whole suite passing,
    silently producing rooms a quarter as deep with gate 10 reporting a
    confident PASS. These are the depth requirements the whole corpus's
    "no thin filler" guarantee rests on; pin the actual shipped numbers so a
    quartering (or any other silent rescaling) fails here, immediately, on
    the module that produced it — not three tests removed as a mysteriously
    thinner-than-expected room.
    """
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    assert pack.tier_f_floor == 350
    assert {name: a.floor for name, a in pack.archetypes.items()} == {
        "shortform": 500,
        "report": 1000,
        "standard": 1200,
        "longform": 2500,
        "register": 400,
    }


def test_finding_archetypes_cover_every_section_workstream():
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    workstreams = {s.workstream for s in pack.sections}
    assert workstreams <= set(pack.finding_archetypes)


def test_load_domain_rejects_an_archetype_floor_below_the_tier_f_floor(tmp_path):
    # A tier-A anchor must never be held to a lower depth standard than
    # tier-F filler. This is a data-independent property of load_domain
    # itself, checked here on a minimal synthetic pack rather than by
    # relying on the shipped archetypes.yaml staying correct by hand.
    (tmp_path / "sections.yaml").write_text(
        "sections:\n"
        "  - {number: 1, dir_name: 01_x, title: X, workstream: x, weight: 1.0, subsections: [a]}\n"
    )
    (tmp_path / "archetypes.yaml").write_text(
        "archetypes:\n"
        '  register: {floor: 300, filename_patterns: ["register"]}\n'
        "default_archetype: register\n"
        "tier_f_floor: 350\n"
    )
    (tmp_path / "finding-archetypes.yaml").write_text("finding_archetypes:\n  x: [a]\n")
    with pytest.raises(DomainError, match="register"):
        load_domain(tmp_path)


# ---------------------------------------------------------------------------
# Final review, F2: sections.yaml and finding-archetypes.yaml both declare
# the domain's workstream list, and synthvdr.schema.derive_prefix_for_
# workstream zips a caller-supplied workstream order positionally against
# room.conf's FINDING_PREFIXES. Nothing enforced the two files agreed before
# this check — swapping two rows in finding-archetypes.yaml (same
# workstreams, same length, wrong order) passed the whole suite and would
# silently mint new finding IDs under the wrong workstream's prefix. These
# tests hold that agreement as its own property, independent of whether any
# workstream happens to have an existing finding yet.
# ---------------------------------------------------------------------------


def _two_section_pack(tmp_path, archetype_order):
    (tmp_path / "sections.yaml").write_text(
        "sections:\n"
        "  - {number: 1, dir_name: 01_a, title: A, workstream: alpha, weight: 0.5, subsections: [x]}\n"
        "  - {number: 2, dir_name: 02_b, title: B, workstream: beta, weight: 0.5, subsections: [x]}\n"
    )
    (tmp_path / "archetypes.yaml").write_text(
        "archetypes:\n"
        '  register: {floor: 300, filename_patterns: ["register"]}\n'
        "default_archetype: register\n"
        "tier_f_floor: 100\n"
    )
    rows = "\n".join(f"  {w}: [an issue]" for w in archetype_order)
    (tmp_path / "finding-archetypes.yaml").write_text(f"finding_archetypes:\n{rows}\n")


def test_load_domain_accepts_matching_workstream_order(tmp_path):
    _two_section_pack(tmp_path, ["alpha", "beta"])
    pack = load_domain(tmp_path)
    assert pack.workstreams() == ["alpha", "beta"]


def test_load_domain_rejects_a_reordered_finding_archetypes(tmp_path):
    # Same two workstreams, same length — only the ORDER differs. A bare
    # zip() would accept this silently and misattribute every discovery.
    _two_section_pack(tmp_path, ["beta", "alpha"])
    with pytest.raises(DomainError, match="DIFFERENT ORDER"):
        load_domain(tmp_path)


def test_load_domain_rejects_a_finding_archetypes_missing_a_workstream(tmp_path):
    _two_section_pack(tmp_path, ["alpha"])
    with pytest.raises(DomainError, match=r"missing \['beta'\]"):
        load_domain(tmp_path)


def test_load_domain_rejects_a_finding_archetypes_with_an_unknown_workstream(tmp_path):
    _two_section_pack(tmp_path, ["alpha", "beta", "gamma"])
    with pytest.raises(DomainError, match=r"unexpected \['gamma'\]"):
        load_domain(tmp_path)


def test_workstreams_matches_finding_archetypes_key_order_on_the_real_pack():
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    assert pack.workstreams() == list(pack.finding_archetypes)
    assert pack.workstreams() == [s.workstream for s in pack.sections]


def test_load_domain_catches_the_exact_final_review_reproduction(tmp_path):
    """The final review's own reproduction: swap the `tax:` and `financing:`
    rows in the SHIPPED finding-archetypes.yaml, two lines, nothing else
    touched. Confirmed by the review to leave 689 tests passing and mint
    `tax -> FING` / `financing -> TAX` with no error anywhere. Copies the
    real domain pack rather than a synthetic one, so this exercises the
    actual shipped file, not a stand-in for it.
    """
    shutil.copytree(DEFAULT_DOMAIN_ROOT, tmp_path, dirs_exist_ok=True)
    path = tmp_path / "finding-archetypes.yaml"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tax_idx = next(i for i, line in enumerate(lines) if line.startswith("  tax:"))
    financing_idx = next(i for i, line in enumerate(lines) if line.startswith("  financing:"))
    lines[tax_idx], lines[financing_idx] = lines[financing_idx], lines[tax_idx]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(DomainError, match="DIFFERENT ORDER"):
        load_domain(tmp_path)


def test_a_hand_built_pack_cannot_hold_a_tier_a_anchor_below_the_tier_f_floor():
    # The invariant belongs to the type, not to one way of building it.
    # Checked only inside load_domain, it bound YAML on disk and nothing
    # else - and DomainPack is constructed directly in the tests that cover
    # archetype classification, which is exactly where a pack encoding the
    # backwards rule (an anchor held to a LOWER depth standard than filler)
    # would be built by hand and quietly believed.
    with pytest.raises(DomainError, match="register"):
        DomainPack(
            sections=[],
            archetypes={"register": Archetype(
                name="register", floor=300, filename_patterns=["register"]
            )},
            default_archetype="register",
            tier_f_floor=350,
            finding_archetypes={},
        )


def test_a_hand_built_pack_at_exactly_the_tier_f_floor_is_allowed():
    # The boundary is "below", not "at or below": an archetype whose floor
    # equals the flat tier-F floor holds an anchor to the same standard as
    # filler, which is permitted. Off-by-one here would reject the shipped
    # pack the moment any archetype was tuned down to meet the floor.
    pack = DomainPack(
        sections=[],
        archetypes={"register": Archetype(
            name="register", floor=350, filename_patterns=["register"]
        )},
        default_archetype="register",
        tier_f_floor=350,
        finding_archetypes={},
    )
    assert pack.tier_f_floor == 350


def test_load_domain_still_names_the_pack_root_when_the_floor_check_fails(tmp_path):
    # Control on the move: the invariant's message loses the pack root when
    # it moves into __post_init__, which cannot know it. load_domain must
    # put it back, or a failure stops saying WHICH pack on disk is wrong.
    (tmp_path / "sections.yaml").write_text(
        "sections:\n"
        "  - {number: 1, dir_name: 01_x, title: X, workstream: x, weight: 1.0, subsections: [a]}\n"
    )
    (tmp_path / "archetypes.yaml").write_text(
        "archetypes:\n"
        '  register: {floor: 300, filename_patterns: ["register"]}\n'
        "default_archetype: register\n"
        "tier_f_floor: 350\n"
    )
    (tmp_path / "finding-archetypes.yaml").write_text("finding_archetypes:\n  x: [a]\n")
    with pytest.raises(DomainError) as excinfo:
        load_domain(tmp_path)
    assert str(tmp_path) in str(excinfo.value)
    assert "register" in str(excinfo.value)


def test_the_four_core_sections_are_marked_and_the_rest_are_not():
    # A core section is one no room may drop: every deal has corporate,
    # financial and commercial papers, and 18_transaction holds the draft SPA,
    # which is the only natural home for the enterprise-value and earn-out
    # figures gate 13 greps the room for. A room without it cannot answer
    # gate 13 at all.
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    core = {s.dir_name for s in pack.sections if s.core}
    assert core == {
        "01_corporate",
        "02_financial",
        "05_commercial",
        "18_transaction",
    }


def test_core_defaults_to_false_so_an_older_pack_still_loads():
    # sections.yaml rows are splatted straight into Section(**row), so a row
    # without the key must not raise — otherwise adding this field breaks every
    # domain pack that predates it.
    section = Section(
        number=1,
        dir_name="01_corporate",
        title="Corporate",
        workstream="corporate",
        weight=1.0,
        subsections=["constitutional"],
    )
    assert section.core is False
