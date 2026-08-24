from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.index_build import count_slots, render_index, write_index_sources
from synthvdr.slots import SIZE_PRESETS, build_slot_manifest


def sources(tmp_path, preset="S"):
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    slots = build_slot_manifest(pack, SIZE_PRESETS[preset])
    src = tmp_path / "_key" / "index-src"
    write_index_sources(slots, pack, src)
    return pack, slots, src


def test_index_lists_every_slot_exactly_once(tmp_path):
    _, slots, src = sources(tmp_path)
    assert count_slots(render_index(src)) == len(slots)


def test_render_is_byte_stable_across_calls(tmp_path):
    _, _, src = sources(tmp_path)
    assert render_index(src) == render_index(src)


def test_sections_appear_in_numerical_order(tmp_path):
    pack, _, src = sources(tmp_path)
    text = render_index(src)
    positions = [text.index(s.title) for s in pack.sections]
    assert positions == sorted(positions)


def test_preamble_is_in_world_and_free_of_build_vocabulary(tmp_path):
    _, _, src = sources(tmp_path)
    preamble = (src / "00_preamble.txt").read_text().lower()
    for token in ["blind", "flagged", "_key", "answer key", "renumber", "tier"]:
        assert token not in preamble
