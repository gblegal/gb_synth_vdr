import re

import pytest

from synthvdr.names import covered_by_cast, entity_tokens
from synthvdr.qa.leakage import (
    ANSWER_KEY_NOUNS,
    BUILD_VOCABULARY,
    _hits,
    finding_id_pattern,
    gate_03_flag_leakage,
    gate_04_vocabulary,
    gate_05_index_vocabulary,
    gate_12_key_containment,
    gate_14_unchecked_names,
)
from synthvdr.qa.runner import GateContext
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import FindingSet

CONF = '''ROOM_CODENAME="Project Testbed"
INDEX_TOTAL=2
BLIND_TOTAL=2
FLAGGED_TOTAL=2
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="CORP|ENV|FIN"
EXPECTED_KDP_CARRIERS=0
SECTION_DIRS="01_corporate"
'''


@pytest.fixture
def room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF)
    doc_dir = tmp_path / "data-room" / "01_corporate" / "1.1_constitutional"
    doc_dir.mkdir(parents=True)
    (doc_dir / "1.1.1_articles.md").write_text("# Articles\n\nOrdinary content.\n")
    (tmp_path / "index.md").write_text("- 1.1.1 Articles\n")
    (tmp_path / "_key").mkdir()
    (tmp_path / "_key" / "name-check.md").write_text(
        "| Name | Kind | Verdict | Checked |\n|---|---|---|---|\n"
        "| Ashfell Holdings Limited | entity | clear | 2026-08-24 |\n"
        "| Kessler Werke GmbH | entity | clear | 2026-08-24 |\n"
    )
    return tmp_path


def ctx_for(room, strict=False):
    conf = load_room_conf(room / "room.conf")
    return GateContext(room=room, conf=conf, findings=FindingSet([], ""), distractors=[], strict=strict)


def blind_doc(room):
    return room / "data-room" / "01_corporate" / "1.1_constitutional" / "1.1.1_articles.md"


def test_gate_03_passes_on_a_clean_room(room):
    assert gate_03_flag_leakage(ctx_for(room)).status == "PASS"


def test_gate_03_catches_an_annotation_heading_in_the_blind_tree(room):
    blind_doc(room).write_text("# Articles\n\n## Key diligence points\n\n- oops\n")
    result = gate_03_flag_leakage(ctx_for(room))
    assert result.status == "FAIL"
    assert "1.1.1_articles.md" in result.detail


def test_gate_04_catches_a_finding_id(room):
    blind_doc(room).write_text("# Articles\n\nSee ENV-1 for detail.\n")
    assert gate_04_vocabulary(ctx_for(room)).status == "FAIL"


def test_gate_04_catches_an_answer_key_noun(room):
    blind_doc(room).write_text("# Articles\n\nThis is a planted finding.\n")
    assert gate_04_vocabulary(ctx_for(room)).status == "FAIL"


def test_gate_04_does_not_trip_on_land_registry(room):
    blind_doc(room).write_text("# Articles\n\nRegistered at the Land Registry.\n")
    assert gate_04_vocabulary(ctx_for(room)).status == "PASS"


def test_gate_05_catches_build_vocabulary_that_gate_04_would_miss(room):
    (room / "index.md").write_text("- 1.1.1 Articles\n\nNever renumber these entries.\n")
    assert gate_04_vocabulary(ctx_for(room)).status == "PASS"
    assert gate_05_index_vocabulary(ctx_for(room)).status == "FAIL"


def test_gate_12_catches_a_key_path_referenced_from_the_blind_tree(room):
    blind_doc(room).write_text("# Articles\n\nSee _key/findings.yaml.\n")
    assert gate_12_key_containment(ctx_for(room)).status == "FAIL"


def test_entity_tokens_finds_corporate_names_only():
    text = "Ashfell Holdings Limited entered a contract with Kessler Werke GmbH in March."
    assert entity_tokens(text) == {"Ashfell Holdings Limited", "Kessler Werke GmbH"}


def test_gate_14_flags_an_entity_absent_from_the_cast_list(room):
    blind_doc(room).write_text("# Articles\n\nA deed with Unlisted Trading Limited.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Unlisted Trading Limited" in result.detail


def test_gate_14_passes_when_every_entity_is_on_the_cast_list(room):
    blind_doc(room).write_text("# Articles\n\nA deed with Ashfell Holdings Limited.\n")
    assert gate_14_unchecked_names(ctx_for(room)).status == "PASS"


def test_gate_14_skips_when_no_name_check_has_been_run(room):
    (room / "_key" / "name-check.md").unlink()
    assert gate_14_unchecked_names(ctx_for(room)).status == "SKIP"


# ---------------------------------------------------------------------------
# Review finding A — a file that fails strict UTF-8 decoding must still be
# read (with errors="replace"), never silently skipped and then folded into
# a "clean" count for files that were never actually swept.
# ---------------------------------------------------------------------------


def test_gate_04_catches_a_leak_hidden_by_an_invalid_utf8_byte(room):
    blind_doc(room).write_bytes(b"# Articles\n\nSee \xffENV-1 for detail.\n")
    result = gate_04_vocabulary(ctx_for(room))
    assert result.status == "FAIL"
    assert "ENV-1" in result.detail


def test_gate_04_reports_how_many_files_needed_lossy_decoding(room):
    blind_doc(room).write_bytes(b"# Articles\n\nOrdinary \xffcontent.\n")
    result = gate_04_vocabulary(ctx_for(room))
    assert result.status == "PASS"
    assert "1 file" in result.detail
    assert "lossy" in result.detail.lower()


# ---------------------------------------------------------------------------
# Review finding B — a path is part of what the tool under test receives, so
# a filename can carry a leak just as easily as a document's body.
# ---------------------------------------------------------------------------


def test_hits_sweeps_the_filename_as_well_as_the_content(tmp_path):
    path = tmp_path / "ENV-1_articles.md"
    path.write_text("# Articles\n\nOrdinary content.\n")
    hits, replaced = _hits([path], (), pattern=re.compile(r"ENV-\d+"))
    assert hits == [f"{path.name}: 'ENV-1'"]
    assert replaced == 0


def test_gate_04_catches_a_finding_id_that_appears_only_in_the_filename(room):
    # The real slug shape this project produces — a trailing underscore
    # right after the digit — now matches, thanks to finding_id_pattern's
    # boundary fix below (finding B, residual).
    (room / "data-room" / "01_corporate" / "1.1_constitutional" / "ENV-1_articles.md").write_text(
        "# Articles\n\nOrdinary content.\n"
    )
    result = gate_04_vocabulary(ctx_for(room))
    assert result.status == "FAIL"
    assert "ENV-1" in result.detail


def test_gate_14_flags_an_entity_named_only_in_the_filename(room):
    (room / "data-room" / "01_corporate" / "1.1_constitutional" / "Unlisted Trading Limited.md").write_text(
        "# Articles\n\nOrdinary content.\n"
    )
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Unlisted Trading Limited" in result.detail


# ---------------------------------------------------------------------------
# Review finding B (residual) — finding_id_pattern's trailing \b was
# defeated by a trailing underscore, exactly the shape this project's own
# slug convention produces ("1.1.1_articles.md").
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate, should_match",
    [
        ("ENV-1_articles", True),   # the slug shape that used to be invisible
        ("ENV-1a", False),          # a real alphanumeric continuation
        ("ENV-1.md", True),
        ("ENV-12", True),
        ("MENV-1", False),         # leading \b must still block a mid-word hit
    ],
)
def test_finding_id_pattern_rejects_only_a_real_alphanumeric_continuation(room, candidate, should_match):
    pattern = finding_id_pattern(ctx_for(room).conf)
    assert bool(pattern.search(candidate)) is should_match


# ---------------------------------------------------------------------------
# Review finding C — a corporate suffix must be recognised regardless of how
# it is cased; gate 14 can never flag what entity_tokens never sees.
# ---------------------------------------------------------------------------


def test_entity_tokens_matches_suffixes_case_insensitively():
    assert entity_tokens("Ashfell Trading limited") == {"Ashfell Trading limited"}
    assert entity_tokens("Kessler Werke GMBH") == {"Kessler Werke GMBH"}


# ---------------------------------------------------------------------------
# Review finding D (reopened twice) — round 2's fix enumerated leading words
# ("The") instead of stating a property. Its replacement property — cover a
# candidate if ANY trailing sub-phrase of it is on the cast list — was
# itself too weak: it also covers a genuinely different, unchecked entity
# whose name happens to end in a checked one's words ("Ashfell Trading
# Holdings Limited" against a cast entry of just "Holdings Limited"), and
# lets one stray one-word cast row ("GmbH") blanket-cover a whole suffix
# family. The tightened property: a candidate is covered only if it is
# itself on the cast list, or becomes a cast entry once exactly ONE leading
# word is dropped — bounded because the regex only ever absorbs a single
# sentence-initial or preposition-like word ahead of a genuine name.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate, cast, expected",
    [
        ("See Kessler Werke GmbH", {"Kessler Werke GmbH"}, True),
        ("The Ashfell Holdings Limited", {"Ashfell Holdings Limited"}, True),
        ("Registered Ashfell Holdings Limited", {"Ashfell Holdings Limited"}, True),
        ("Kessler Werke GmbH", {"Kessler Werke GmbH"}, True),
        # A different, unchecked entity whose name happens to end in a
        # checked one's words must still be flagged — not covered.
        ("Ashfell Trading Holdings Limited", {"Holdings Limited"}, False),
        # A bare one-word cast row must not blanket-cover a suffix family.
        ("Ashfell Trading GmbH", {"GmbH"}, False),
    ],
)
def test_covered_by_cast_drops_at_most_one_leading_word(candidate, cast, expected):
    assert covered_by_cast(candidate, cast) is expected


def test_covered_by_cast_property_one_leading_word_covered_two_flagged():
    # Property, not examples: for EVERY name on the cast list, prefixing it
    # with exactly one capitalised word must register as covered, and
    # prefixing it with two must register as flagged — the bound is on the
    # NUMBER of leading words removed, not on which words they are.
    cast = {"Ashfell Holdings Limited", "Kessler Werke GmbH", "Vantage Underwriting PLC"}
    for name in cast:
        one_word = f"See {name}"
        two_words = f"We See {name}"
        assert covered_by_cast(one_word, cast), one_word
        assert not covered_by_cast(two_words, cast), two_words


@pytest.mark.parametrize(
    "prose",
    [
        "The Ashfell Holdings Limited signed.",
        "See Kessler Werke GmbH for detail.",
        "Under Kessler Werke GmbH the plant runs.",
        "Per Ashfell Holdings Limited, the deed.",
        "Registered Ashfell Holdings Limited today.",
    ],
)
def test_gate_14_does_not_false_fail_on_an_arbitrary_leading_word(room, prose):
    blind_doc(room).write_text(f"# Articles\n\n{prose}\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "PASS", result.detail


def test_gate_14_still_flags_an_unchecked_entity_even_with_a_leading_word(room):
    # The property must not have been blunted into never flagging anything:
    # a leading word only excuses a name that IS on the cast list.
    blind_doc(room).write_text("# Articles\n\nSee Unlisted Trading Limited for detail.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Unlisted Trading Limited" in result.detail


def test_gate_14_does_not_false_fail_on_a_name_at_the_start_of_a_file(room):
    blind_doc(room).write_text("Ashfell Holdings Limited is a company.\n")
    assert gate_14_unchecked_names(ctx_for(room)).status == "PASS"


def test_gate_14_does_not_false_fail_on_a_name_after_a_full_stop(room):
    blind_doc(room).write_text("Formation was complete. Ashfell Holdings Limited then traded.\n")
    assert gate_14_unchecked_names(ctx_for(room)).status == "PASS"


def test_entity_tokens_does_not_join_a_heading_and_body_across_a_blank_line():
    # An entity name does not span a paragraph: the inter-word separator is
    # spaces and tabs only, so a heading cannot be glued to the sentence
    # below it into one false candidate.
    text = "# Supply\n\nKessler Werke GmbH signed the deed.\n"
    assert entity_tokens(text) == {"Kessler Werke GmbH"}


def test_gate_14_does_not_false_fail_when_a_heading_precedes_a_covered_name(room):
    # Combines both halves of the reopened fix: the heading must not be
    # joined into the candidate, AND the leading word "See" must not stop
    # the (correctly isolated) candidate from being recognised as covered.
    blind_doc(room).write_text("# Supply\n\nSee Kessler Werke GmbH for detail.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "PASS", result.detail


# ---------------------------------------------------------------------------
# Review finding F — a truncated hit list must say so: six leaks and sixty
# must not read identically.
# ---------------------------------------------------------------------------


def test_gate_05_signals_when_hits_are_truncated(room):
    needles_to_plant = list(ANSWER_KEY_NOUNS[:3]) + list(BUILD_VOCABULARY[:4])
    body = "\n".join(needles_to_plant)
    (room / "index.md").write_text(f"- 1.1.1 Articles\n\n{body}\n")
    result = gate_05_index_vocabulary(ctx_for(room))
    assert result.status == "FAIL"
    assert "more" in result.detail


def test_gate_05_detail_is_not_marked_truncated_when_hits_fit(room):
    (room / "index.md").write_text("- 1.1.1 Articles\n\nNever renumber these entries.\n")
    result = gate_05_index_vocabulary(ctx_for(room))
    assert result.status == "FAIL"
    assert "more" not in result.detail
