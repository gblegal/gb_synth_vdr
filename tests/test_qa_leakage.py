import re

import pytest

from synthvdr.names import cast_list, entity_tokens, mask_cast_names
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
# Review finding D (reopened three times) — the OPERATION was the problem,
# not the bound on it. Three rounds tried to extract a candidate from prose
# and then ask "is this covered?": a determiner stoplist (handled "The",
# missed "See", "Under", "Per", "Registered"); an unbounded trailing
# sub-phrase walk (killed the false positives, but let a cast entry of
# "Holdings Limited" cover the different, unchecked "Ashfell Trading
# Holdings Limited"); then a one-leading-word bound (only shrank both
# windows to two words). Separating "ordinary leading word" from "part of a
# name" is named-entity recognition — no word-count rule does it, so every
# bound carried both error classes at once.
#
# Round 4 inverts the operation: MASK every known cast name out of the text
# first, then scan the residue. A known name is gone before the regex ever
# runs, so no number of ordinary leading words can produce a candidate —
# the whole false-positive class goes structurally, with no stoplist and no
# bound. Anything the regex still finds carries a corporate suffix that no
# cast entry accounts for, and is genuinely unknown.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prose, expected",
    [
        # The room fixture's cast holds both "Kessler Werke GmbH" and
        # "Ashfell Holdings Limited", so each of these sentences names a
        # correctly-registered company. Any number of ordinary capitalised
        # words may precede it; none of them may fail the build.
        ("See Kessler Werke GmbH for detail.", "PASS"),
        ("As Noted Kessler Werke GmbH continued trading.", "PASS"),
        ("Related Parties Kessler Werke GmbH holds a stake.", "PASS"),
        ("Duly Registered Ashfell Holdings Limited filed.", "PASS"),
        ("The Ashfell Holdings Limited signed.", "PASS"),
        ("Ashfell Holdings Limited signed.", "PASS"),
        # ...and an entity nobody checked is still caught.
        ("A deed with Unlisted Trading Limited.", "FAIL"),
    ],
)
def test_gate_14_masks_the_cast_before_scanning_the_residue(room, prose, expected):
    blind_doc(room).write_text(f"# Articles\n\n{prose}\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == expected, result.detail


def test_gate_14_property_any_one_two_or_three_leading_words_leave_the_room_clean(room):
    # Property, not examples, and with no bound on the number of leading
    # words: for EVERY name on the room's own cast list, prefixing it with
    # one, two or three ordinary capitalised words must leave the room
    # clean. Masking removes the name itself, so what precedes it cannot
    # matter — which is the whole point of inverting the operation.
    cast = cast_list(room / "_key" / "name-check.md")
    assert cast, "the fixture must have a non-empty cast list for this to mean anything"
    for name in sorted(cast):
        for prefix in ("See", "As Noted", "Related Parties Under"):
            blind_doc(room).write_text(f"# Articles\n\n{prefix} {name} signed the deed.\n")
            result = gate_14_unchecked_names(ctx_for(room))
            assert result.status == "PASS", f"{prefix} {name}: {result.detail}"


def test_gate_14_flags_an_unchecked_entity_in_the_same_room_as_covered_ones(room):
    # Masking must not blunt the gate: a document that names two registered
    # companies AND one nobody checked still fails, naming only the third.
    blind_doc(room).write_text(
        "# Articles\n\n"
        "See Kessler Werke GmbH for detail. Duly Registered Ashfell Holdings "
        "Limited filed. A deed with Unlisted Trading Limited.\n"
    )
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Unlisted Trading Limited" in result.detail
    assert "Kessler" not in result.detail
    assert "Ashfell" not in result.detail


def test_gate_14_masks_cast_names_in_the_filename_too(room):
    # The filename sweep runs the same regex over the same kind of text, so
    # it needs the same masking — two ordinary leading words in a document
    # slug must not fail the build either.
    (room / "data-room" / "01_corporate" / "1.1_constitutional" /
     "Executed Deed Ashfell Holdings Limited.md").write_text("# Deed\n\nOrdinary content.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "PASS", result.detail


def test_mask_cast_names_removes_the_longest_entry_first():
    # A shorter cast entry must not pre-empt a longer one that contains it:
    # masking "Holdings Limited" first would strand "Ashfell" in the
    # residue, where it can fuse with whatever capitalised words follow and
    # be reported as part of a name that was never in the document.
    residue = mask_cast_names(
        "Ashfell Holdings Limited signed.",
        {"Ashfell Holdings Limited", "Holdings Limited"},
    )
    assert "Ashfell" not in residue
    assert entity_tokens(residue) == set()


def test_gate_14_reports_the_right_name_when_a_shorter_cast_entry_is_nested(room):
    # The user-visible consequence of the ordering above, end-to-end: with
    # shortest-first masking the leftover "Ashfell" fuses with the genuinely
    # unchecked entity beside it and the FAIL names a company that does not
    # exist.
    (room / "_key" / "name-check.md").write_text(
        "| Name | Kind | Verdict | Checked |\n|---|---|---|---|\n"
        "| Ashfell Holdings Limited | entity | clear | 2026-08-24 |\n"
        "| Holdings Limited | entity | clear | 2026-08-24 |\n"
    )
    blind_doc(room).write_text("# Articles\n\nAshfell Holdings Limited Rival Trading Limited.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert result.detail.startswith("Rival Trading Limited —")


# ---------------------------------------------------------------------------
# Cast-list hygiene — the one gap masking leaves. A degenerate cast row that
# is a bare corporate suffix blanket-masks every entity ending in it
# ("Ashfell Trading GmbH" would come back clean against a cast of {"GmbH"}).
# That is malformed input: a cast list is generated from the fact sheet by
# the name check, so a one-word row means that process went wrong. Gate 14
# rejects it loudly rather than silently degrading into a gate that cannot
# fail.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("row", ["GmbH", "Limited", "Ashfell"])
def test_gate_14_fails_on_a_malformed_cast_row(room, row):
    (room / "_key" / "name-check.md").write_text(
        "| Name | Kind | Verdict | Checked |\n|---|---|---|---|\n"
        f"| {row} | entity | clear | 2026-08-24 |\n"
        "| Ashfell Holdings Limited | entity | clear | 2026-08-24 |\n"
    )
    blind_doc(room).write_text("# Articles\n\nAshfell Trading GmbH signed.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert row in result.detail
    # The well-formed row alongside it is not the problem and must not be named.
    assert "Ashfell Holdings Limited" not in result.detail


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
