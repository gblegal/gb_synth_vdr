import re
from pathlib import Path

import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.index_build import _titleise, count_slots, render_index, write_index_sources
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


def test_render_is_byte_stable_across_independent_runs(tmp_path, monkeypatch):
    """Verify render_index produces byte-identical output even when glob returns paths in different order.

    Monkeypatches Path.glob to return paths in reversed order, then verifies that render_index
    still produces the same output. This tests that render_index sorts its inputs, which is the
    actual invariant we need to preserve.
    """
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    slots = build_slot_manifest(pack, SIZE_PRESETS["S"])

    # Generate the index sources
    src = tmp_path / "index-src"
    write_index_sources(slots, pack, src)

    # Get output with normal glob order
    output_normal = render_index(src)

    # Now monkeypatch glob to return paths in reversed order
    original_glob = Path.glob

    def reversed_glob(self, pattern):
        return reversed(list(original_glob(self, pattern)))

    monkeypatch.setattr(Path, "glob", reversed_glob)

    # Get output with reversed glob order
    output_reversed = render_index(src)

    # Both should be identical because render_index sorts
    assert output_normal == output_reversed


def test_render_is_byte_stable_fails_without_sorted(tmp_path, monkeypatch):
    """Verify the byte-stability test actually catches missing sorted() call.

    This is a regression test for the test itself: proves that removing sorted()
    from render_index would make test_render_is_byte_stable_across_independent_runs fail.
    """
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    slots = build_slot_manifest(pack, SIZE_PRESETS["S"])
    src = tmp_path / "index-src"
    write_index_sources(slots, pack, src)

    # Monkeypatch glob to return paths in reversed order
    original_glob = Path.glob

    def reversed_glob(self, pattern):
        return reversed(list(original_glob(self, pattern)))

    monkeypatch.setattr(Path, "glob", reversed_glob)

    # Without sorted(), the reversed order would produce different output
    # Simulate render_index without sorted() by directly concatenating in glob order
    preamble = (src / "00_preamble.txt").read_text(encoding="utf-8").rstrip("\n")
    parts = [preamble, ""]
    for path in (p for p in src.glob("*.md")):  # No sorted() call
        parts.append(path.read_text(encoding="utf-8").rstrip("\n"))
        parts.append("")
    output_no_sort = "\n".join(parts).rstrip("\n") + "\n"

    # This should be different from the properly sorted output
    output_with_sort = render_index(src)
    assert output_no_sort != output_with_sort


def test_sections_appear_in_numerical_order(tmp_path):
    pack, _, src = sources(tmp_path)
    text = render_index(src)
    positions = [text.index(f"## {s.number}. {s.title}") for s in pack.sections]
    assert positions == sorted(positions)


def test_preamble_is_in_world_and_free_of_build_vocabulary(tmp_path):
    _, _, src = sources(tmp_path)
    preamble = (src / "00_preamble.txt").read_text().lower()
    for token in ["blind", "flagged", "_key", "answer key", "renumber", "tier"]:
        assert token not in preamble


def test_no_subsection_heading_appears_more_than_once_per_section(tmp_path):
    """Verify Task 4's contiguity contract is honoured: no interleaved subsection headings."""
    pack, _, src = sources(tmp_path)
    text = render_index(src)

    section_pattern = re.compile(r"^## \d+\.")
    current_section = None

    for line in text.split("\n"):
        if section_pattern.match(line):
            current_section = line
            subsections_seen = set()
        elif line.startswith("###"):
            sub_match = re.match(r"^### ([\d.]+)", line)
            if sub_match:
                sub_id = sub_match.group(1)
                assert sub_id not in subsections_seen, (
                    f"Subsection {sub_id} appears more than once in {current_section}"
                )
                subsections_seen.add(sub_id)


@pytest.mark.parametrize(
    "input_text,expected",
    [
        ("statutory accounts", "Statutory accounts"),
        ("cpse replies", "CPSE replies"),
        ("hse notices", "HSE notices"),
        ("jv agreements", "JV agreements"),
        ("ncr capa", "NCR CAPA"),
        ("nda", "NDA"),
        ("ndas", "NDAs"),
        ("w and i", "W&I"),
    ],
)
def test_titleise_renders_acronyms_and_sentence_case(input_text, expected):
    """Verify _titleise correctly handles acronyms and sentence case.

    This table-driven test ensures:
    - Acronyms are uppercased
    - Plural acronyms preserve the 's' in lowercase: 'ndas' -> 'NDAs', not 'NDAS'
    - First word is capitalized, others are lowercase (except acronyms)
    - Special case 'w and i' becomes 'W&I'
    """
    assert _titleise(input_text) == expected
