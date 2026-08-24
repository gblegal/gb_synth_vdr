from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain


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
