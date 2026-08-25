import re
from pathlib import Path

import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.index_build import (
    INDEX_SRC_MARKER_NAME,
    IndexBuildError,
    _titleise,
    count_slots,
    render_index,
    write_index_sources,
)
from synthvdr.slots import SIZE_PRESETS, build_slot_manifest


def sources(tmp_path, preset="S"):
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    slots = build_slot_manifest(pack, SIZE_PRESETS[preset])
    src = tmp_path / "_key" / "index-src"
    write_index_sources(slots, pack, src)
    return pack, slots, src


def _pack_and_slots(preset="S"):
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    slots = build_slot_manifest(pack, SIZE_PRESETS[preset])
    return pack, slots


# ---------------------------------------------------------------------------
# Ownership guard (reused from synthvdr.ownership, not re-derived here)
# ---------------------------------------------------------------------------


def test_refuses_foreign_directory_and_leaves_victim_files_intact(tmp_path):
    """A non-empty directory without the marker must be refused, and refused
    BEFORE anything is deleted. Assert the victim file's content survives,
    not merely that an exception was raised — a test that only checks for
    the exception would still pass against code that deletes first and
    raises afterwards."""
    foreign = tmp_path / "notes"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("do not touch", encoding="utf-8")
    (foreign / "meeting-notes.md").write_text("agenda for Tuesday", encoding="utf-8")
    (foreign / "todo.md").write_text("buy milk", encoding="utf-8")

    pack, slots = _pack_and_slots()
    with pytest.raises(IndexBuildError):
        write_index_sources(slots, pack, foreign)

    assert (foreign / "keep.txt").read_text(encoding="utf-8") == "do not touch"
    assert (foreign / "meeting-notes.md").read_text(encoding="utf-8") == "agenda for Tuesday"
    assert (foreign / "todo.md").read_text(encoding="utf-8") == "buy milk"


def test_proceeds_on_empty_directory(tmp_path):
    empty = tmp_path / "index-src"
    empty.mkdir()
    pack, slots = _pack_and_slots()
    write_index_sources(slots, pack, empty)
    assert (empty / "00_preamble.txt").exists()


def test_proceeds_on_nonexistent_directory(tmp_path):
    missing = tmp_path / "does" / "not" / "exist"
    pack, slots = _pack_and_slots()
    write_index_sources(slots, pack, missing)
    assert (missing / "00_preamble.txt").exists()


def test_proceeds_on_directory_already_carrying_the_marker(tmp_path):
    marked = tmp_path / "index-src"
    marked.mkdir()
    (marked / INDEX_SRC_MARKER_NAME).write_text("anything", encoding="utf-8")
    (marked / "stale.md").write_text("old content", encoding="utf-8")
    pack, slots = _pack_and_slots()
    write_index_sources(slots, pack, marked)
    assert (marked / "00_preamble.txt").exists()
    assert not (marked / "stale.md").exists()


def test_two_consecutive_calls_succeed_without_lockout(tmp_path):
    """The marker written on the first call must not lock the second call
    out of the directory it created."""
    src = tmp_path / "index-src"
    pack, slots = _pack_and_slots()
    write_index_sources(slots, pack, src)
    write_index_sources(slots, pack, src)  # must not raise
    assert (src / INDEX_SRC_MARKER_NAME).exists()


def test_marker_is_invisible_to_render_index(tmp_path):
    """The marker is a dotfile: render_index() only reads 00_preamble.txt by
    name and globs *.md, so the marker must not appear in, or disturb, the
    generated index text."""
    _, _, src = sources(tmp_path)
    assert (src / INDEX_SRC_MARKER_NAME).exists()

    text_with_marker = render_index(src)
    (src / INDEX_SRC_MARKER_NAME).unlink()
    text_without_marker = render_index(src)

    assert INDEX_SRC_MARKER_NAME not in text_with_marker
    assert text_with_marker == text_without_marker


def test_stale_md_files_are_still_cleared_on_regeneration(tmp_path):
    """Regression guard for the reason the delete loop exists at all: a
    stale section file left over from a previous, larger build must not
    survive into the regenerated index-src."""
    src = tmp_path / "index-src"
    pack, slots = _pack_and_slots()
    write_index_sources(slots, pack, src)

    stale = src / "99_now_removed_section.md"
    stale.write_text("this section no longer exists", encoding="utf-8")

    write_index_sources(slots, pack, src)

    assert not stale.exists()


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
        ("qa log", "QA log"),
        ("qa log 03", "QA log 03"),
        ("w and i 01", "W&I 01"),
        ("its", "Its"),
    ],
)
def test_titleise_renders_acronyms_and_sentence_case(input_text, expected):
    """Verify _titleise correctly handles acronyms and sentence case.

    This table-driven test ensures:
    - Acronyms are uppercased
    - Plural acronyms preserve the 's' in lowercase: 'ndas' -> 'NDAs', not 'NDAS'
    - First word is capitalized, others are lowercase (except acronyms)
    - The 'w and i' token run becomes 'W&I', whether it is the whole string or
      a prefix of a longer, ordinal-suffixed title
    - Short words like 'its' are not mistaken for a pluralised acronym ('it' + 's')
    """
    assert _titleise(input_text) == expected


def test_titleise_agrees_on_bare_and_ordinal_suffixed_forms():
    """Close the whole bug class, not just nine examples.

    _titleise is called on bare subsection names (for the '### N.N Name' heading)
    and on the same words plus a trailing ordinal (for each document title in that
    subsection, e.g. 'qa log 01'). Any transform that behaves differently between
    the two — such as an exact-string special case that only matches the bare
    form — is a live bug, because the majority of real calls are the suffixed
    form. Assert the two agree, for every subsection in the domain pack, so this
    holds regardless of how the taxonomy grows.
    """
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    for section in pack.sections:
        for sub in section.subsections:
            words = sub.replace("-", " ")
            bare = _titleise(words)
            suffixed = _titleise(f"{words} 01")
            assert suffixed.removesuffix(" 01") == bare, (
                f"{words!r}: bare={bare!r} but suffixed={suffixed!r} "
                f"(with ' 01' stripped: {suffixed.removesuffix(' 01')!r})"
            )
