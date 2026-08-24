import re
from pathlib import Path

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


def test_render_is_byte_stable_across_independent_runs(tmp_path):
    """Verify render_index produces byte-identical output even when files are created in different order.

    This tests the real invariant: that sorted() is honoured and output is stable across
    independent runs and filesystems, not just that two calls within one process happen
    to read the same glob() order.
    """
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    slots = build_slot_manifest(pack, SIZE_PRESETS["S"])

    # Build index sources in first temp directory
    src1 = tmp_path / "index-src-1"
    write_index_sources(slots, pack, src1)
    output1 = render_index(src1)

    # Build index sources in a second temp directory, creating files in different order
    # by writing them in reverse section order
    src2 = tmp_path / "index-src-2"
    src2.mkdir(parents=True, exist_ok=True)
    (src2 / "00_preamble.txt").write_text((src1 / "00_preamble.txt").read_text(encoding="utf-8"), encoding="utf-8")
    for md_file in reversed(sorted((src1).glob("*.md"))):
        (src2 / md_file.name).write_text(md_file.read_text(encoding="utf-8"), encoding="utf-8")

    output2 = render_index(src2)

    # Both outputs should be byte-identical despite different creation order
    assert output1 == output2


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
    """Verify Task 4's contiguity contract is honoured: no interleaved subsection headings.

    If slots are not sorted correctly, subsection headings can repeat within a section
    (e.g., ### 3.1, then ### 3.2, then ### 3.1 again), corrupting the index.
    This test protects the rendered artefact from Task 4 sort regressions.
    """
    pack, _, src = sources(tmp_path)
    text = render_index(src)

    # Split text by section headings
    section_pattern = re.compile(r"^## \d+\.")
    current_section = None

    for line in text.split("\n"):
        if section_pattern.match(line):
            current_section = line
            subsections_seen = set()
        elif line.startswith("###"):
            # Extract the subsection identifier (### N.N)
            sub_match = re.match(r"^### ([\d.]+)", line)
            if sub_match:
                sub_id = sub_match.group(1)
                assert sub_id not in subsections_seen, (
                    f"Subsection {sub_id} appears more than once in {current_section}"
                )
                subsections_seen.add(sub_id)


def test_acronyms_are_titleised_not_capitalized(tmp_path):
    """Verify acronyms appear in UPPERCASE, not Capitalized.

    'capitalize()' lowercases everything after the first character, which mangles
    acronyms like NDA -> Nda, CPSE -> Cpse. Verify the titleise() function fixes this.
    """
    _, _, src = sources(tmp_path)
    text = render_index(src)

    # Check for specific mangled acronyms that should be fixed
    mangled_acronyms = ["Vat ", "Nda", "Cpse", "Hse ", "Qms ", "Ncr ", "Dpias", "Jv "]
    for mangled in mangled_acronyms:
        assert mangled not in text, f"Found mangled acronym '{mangled}' in index"

    # Check that the corrected forms appear
    correct_acronyms = ["VAT", "NDA", "CPSE", "HSE", "QMS", "NCR", "DPIA", "JV"]
    for correct in correct_acronyms:
        assert correct in text, f"Expected '{correct}' not found in index"
