from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.qa.depth import classify_archetype, floor_for, strip_annotation, wordcount

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


def test_tier_f_uses_the_flat_floor_regardless_of_archetype():
    assert floor_for("5.1.1", "5.1.1_supply-agreement.md", "F", PACK) == PACK.tier_f_floor


def test_tier_a_uses_the_archetype_floor():
    assert floor_for("5.1.1", "5.1.1_supply-agreement.md", "A", PACK) == PACK.archetypes["standard"].floor
