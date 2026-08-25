import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, DomainError, load_domain


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
