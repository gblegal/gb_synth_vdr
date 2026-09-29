from pathlib import Path

import pytest

from synthvdr.domain import Archetype, DEFAULT_DOMAIN_ROOT, DomainPack, load_domain
from synthvdr.lengths import load_lengths
from synthvdr.slots import SIZE_PRESETS, build_slot_manifest
from synthvdr.qa.depth import (
    DepthLintError,
    classify_archetype,
    depth_problems,
    floor_for,
    part_counts,
    slot_floor,
    strip_annotation,
    wordcount,
)

PACK = load_domain(DEFAULT_DOMAIN_ROOT)
LENGTHS = load_lengths(DEFAULT_DOMAIN_ROOT, PACK)
CUSTOMER = "05_commercial/5.1_customer-contracts/5.1.9_customer-contracts-09.md"


def test_wordcount_counts_whitespace_tokens():
    assert wordcount("one two three") == 3


def test_wordcount_counts_cjk_at_half_weight():
    # ten CJK characters, no spaces -> five, not one
    assert wordcount("株式会社日本工業製品") == 5


def test_wordcount_counts_table_pipes_as_words():
    # documented caveat: this is why table-heavy documents read long
    assert wordcount("| a | b |") > 2


def test_strip_annotation_removes_the_trailing_block():
    text = "Body text.\n\n## Key diligence points\n\n- a point\n"
    assert strip_annotation(text, "Key diligence points").strip() == "Body text."


def test_classify_archetype_matches_on_filename():
    assert classify_archetype("11.2.1_phase-2-report.md", PACK) == "report"
    assert classify_archetype("5.1.1_supply-agreement.md", PACK) == "standard"
    assert classify_archetype("1.2.3_share-register.md", PACK) == "register"


def test_classify_archetype_falls_back_to_the_default():
    assert classify_archetype("9.9.9_untitled-thing.md", PACK) == PACK.default_archetype


def test_classify_archetype_resolves_facilities_to_longform():
    # Carry-forward from Task 3: "facility" (singular) is not a substring of
    # "facilities", so facility agreements — section 4's subsection, and
    # among the longest documents in a real room — silently fell through to
    # "standard" (floor 1200) instead of "longform" (floor 2500) until the
    # plural pattern was added to the domain pack. Pin it here so that gap
    # cannot silently reopen.
    assert classify_archetype("4.1.1_facilities-01.md", PACK) == "longform"


def test_classify_archetype_takes_the_longest_matching_pattern():
    # "13.1.1_trust-deed-01.md" (the real slug for pensions' trust-deed
    # subsection) matches both standard's "deed" and longform's
    # "trust-deed". The classifier must take the longest match, not the
    # first or last one found, or a longer, more specific pattern could
    # silently lose to a shorter one it was added to override.
    assert classify_archetype("13.1.1_trust-deed-01.md", PACK) == "longform"


def test_classify_archetype_equal_length_collision_resolves_to_the_higher_floor():
    # "letter" (shortform, floor 500) and "policy" (standard, floor 1200)
    # are both length-6 matches in this filename. The longest-match rule
    # says nothing about a tie, so the higher floor must win — the safe
    # direction — rather than whichever archetype happens to be declared
    # first in archetypes.yaml.
    assert classify_archetype("10.1.1_cover-letter-policy-note.md", PACK) == "standard"


def test_classify_archetype_equal_length_tie_break_is_order_independent():
    # Same collision as above, reproduced on a synthetic pack with the two
    # colliding archetypes declared in each order, to prove the rule is
    # "higher floor wins" and not an accident of dict iteration order.
    letter = Archetype(name="shortform", floor=500, filename_patterns=["letter"])
    policy = Archetype(name="standard", floor=1200, filename_patterns=["policy"])
    filename = "10.1.1_cover-letter-policy-note.md"

    def pack_with(archetypes):
        return DomainPack(
            sections=[],
            archetypes=archetypes,
            default_archetype="standard",
            tier_f_floor=350,
            finding_archetypes={},
        )

    forward = pack_with({"shortform": letter, "standard": policy})
    backward = pack_with({"standard": policy, "shortform": letter})
    assert classify_archetype(filename, forward) == classify_archetype(filename, backward) == "standard"


def test_tier_f_uses_the_flat_floor_regardless_of_archetype():
    assert floor_for("5.1.1", "5.1.1_supply-agreement.md", "F", PACK) == PACK.tier_f_floor


def test_tier_a_uses_the_archetype_floor():
    assert floor_for("5.1.1", "5.1.1_supply-agreement.md", "A", PACK) == PACK.archetypes["standard"].floor


@pytest.mark.parametrize("bad_tier", ["a", "f", "", "X", "AF", " A"])
def test_floor_for_rejects_any_tier_that_is_not_a_or_f(bad_tier):
    with pytest.raises(DepthLintError):
        floor_for("5.1.1", "5.1.1_supply-agreement.md", bad_tier, PACK)


def test_every_archetype_floor_is_at_least_the_tier_f_floor():
    # Belt and braces alongside load_domain's own runtime check (domain.py):
    # a correctly tagged tier-A anchor must never be held to a lower depth
    # standard than tier-F filler. Checked over every archetype, not just
    # register, so a future edit to any one floor can't reintroduce this.
    assert all(a.floor >= PACK.tier_f_floor for a in PACK.archetypes.values())


# ---------------------------------------------------------------------------
# Review 2026-08-26, S2. `vdr-author` subagents have no Bash, so they cannot run
# wordcount() and every depth figure they report is a visual estimate — and every
# estimate in the build that surfaced this was HIGH (~1,450 for 1,190 against a
# 1,200 floor, ~3,050 for 2,447 against 2,500). Seven of 40 documents landed
# under floor and cost a whole remediation wave. /vdr-build measures the batch
# itself now, and it must measure it with gate 10's own code rather than a
# reimplementation in a fenced example that can drift from the gate.
# ---------------------------------------------------------------------------


def _pack():
    from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain

    return load_domain(DEFAULT_DOMAIN_ROOT)


def test_depth_problems_names_a_document_below_its_floor(tmp_path):
    pack = _pack()
    path = tmp_path / "1.1.1_constitutional-01.md"
    path.write_text("word " * 100, encoding="utf-8")

    problems = depth_problems([path], {"1.1.1": "A"}, pack, "Key diligence points")

    assert len(problems) == 1
    assert "1.1.1" in problems[0]
    assert "100 words" in problems[0]
    assert str(floor_for("1.1.1", path.name, "A", pack)) in problems[0]


def test_depth_problems_is_silent_for_a_document_above_its_floor(tmp_path):
    pack = _pack()
    path = tmp_path / "1.1.1_constitutional-01.md"
    path.write_text("word " * 5000, encoding="utf-8")
    assert depth_problems([path], {"1.1.1": "A"}, pack, "Key diligence points") == []


def test_depth_problems_reports_a_slot_missing_from_anchors(tmp_path):
    pack = _pack()
    path = tmp_path / "9.9.9_unknown-01.md"
    path.write_text("word " * 5000, encoding="utf-8")
    (problem,) = depth_problems([path], {}, pack, "Key diligence points")
    assert "9.9.9" in problem and "anchors.csv" in problem


def test_depth_problems_reports_a_placeholder_before_a_word_count(tmp_path):
    # A short document full of TODO is a placeholder problem, not a depth one —
    # telling the author to write more words would be the wrong instruction.
    pack = _pack()
    path = tmp_path / "1.1.1_constitutional-01.md"
    path.write_text("TODO finish this", encoding="utf-8")
    (problem,) = depth_problems([path], {"1.1.1": "A"}, pack, "Key diligence points")
    assert "placeholder" in problem


def test_depth_problems_ignores_an_annotation_block(tmp_path):
    # The flagged twin's annotation must not count toward the floor, or a
    # flagged document would clear a floor its blind twin does not.
    pack = _pack()
    path = tmp_path / "1.1.1_constitutional-01.md"
    path.write_text("word " * 100 + "\n## Key diligence points\n" + "word " * 5000)
    (problem,) = depth_problems([path], {"1.1.1": "A"}, pack, "Key diligence points")
    assert "100 words" in problem


@pytest.mark.parametrize("size", ["XS", "S", "M", "L"])
def test_slot_floor_without_lengths_is_exactly_floor_for(size):
    # The short-mode guarantee, slot by slot: no lengths, no change.
    for slot in build_slot_manifest(PACK, SIZE_PRESETS[size]):
        assert slot_floor(slot.slot_id, slot.rel_path, slot.tier, PACK) == floor_for(
            slot.slot_id, Path(slot.rel_path).name, slot.tier, PACK
        )


@pytest.mark.parametrize("size", ["XS", "M"])
def test_long_mode_holds_agreements_to_their_band_and_leaves_every_other_slot(size):
    for slot in build_slot_manifest(PACK, SIZE_PRESETS[size]):
        row = LENGTHS.row_for_rel_path(slot.rel_path)
        short = floor_for(slot.slot_id, Path(slot.rel_path).name, slot.tier, PACK)
        expected = LENGTHS.bands[row.band].floor if row else short
        assert slot_floor(slot.slot_id, slot.rel_path, slot.tier, PACK, LENGTHS) == expected


def test_long_mode_ignores_tier_for_an_agreement_slot():
    assert slot_floor("5.1.9", CUSTOMER, "A", PACK, LENGTHS) == 10000
    assert slot_floor("5.1.9", CUSTOMER, "F", PACK, LENGTHS) == 10000


def test_long_mode_still_refuses_an_invalid_tier():
    with pytest.raises(DepthLintError):
        slot_floor("5.1.9", CUSTOMER, "Z", PACK, LENGTHS)


def test_an_unfilled_skeleton_marker_fails_the_depth_lint(tmp_path):
    # The long-mode authoring protocol relies on this existing behaviour: a
    # part left as its "[draft part N ...]" marker is a placeholder.
    path = tmp_path / "05_commercial" / "5.1_customer-contracts" / "5.1.9_customer-contracts-09.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Agreement\n\n" + "word " * 12000 + "\n\n## Schedules\n\n[draft part 9 — Schedules, ~1,600 words]\n")
    problems = depth_problems([path], {"5.1.9": "A"}, PACK, "Key diligence points", LENGTHS)
    assert problems == ["5.1.9: placeholder token '[draft'"]


def test_part_counts_counts_words_under_each_level_two_heading():
    text = (
        "# Title\n\nIntro words here.\n\n## Definitions\n\none two three\n\n"
        "### 1.1 Sub\n\nfour five\n\n## Term\n\nsix\n"
    )
    assert part_counts(text) == [
        ("(before the first part)", 5),
        ("Definitions", 8),
        ("Term", 1),
    ]


def test_part_counts_omits_an_empty_preamble():
    assert part_counts("## Only\n\none two\n") == [("Only", 2)]
