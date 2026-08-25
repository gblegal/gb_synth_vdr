import pytest

from synthvdr.domain import Archetype, DEFAULT_DOMAIN_ROOT, DomainPack, load_domain
from synthvdr.qa.depth import DepthLintError, classify_archetype, floor_for, strip_annotation, wordcount

PACK = load_domain(DEFAULT_DOMAIN_ROOT)


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
