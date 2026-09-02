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
# Task 20 fix round 1, D1 — "SpA" is the one suffix in ENTITY_SUFFIXES whose
# lower-case ("spa") and upper-case ("SPA") forms are ordinary English/business
# words in their own right (a leisure spa; the M&A abbreviation for a Share
# Purchase Agreement — this project's own domain pack has a subsection
# literally named "draft-spa"). Matching it case-insensitively, like every
# other suffix, false-flagged completely ordinary document headings and
# prose on essentially every fresh room. Fix: "SpA" is matched in its exact
# canonical case only; every other suffix stays case-insensitive (see the
# test above and `test_entity_tokens_matches_suffixes_case_insensitively`,
# which must keep passing unchanged).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The Draft spa was circulated on 3 March.",
        "The Draft SPA was circulated on 3 March.",
        "the spa was lovely",
        "This is an SPA amendment",
        "New SPA draft attached.",
    ],
)
def test_entity_tokens_does_not_read_the_spa_abbreviation_as_a_suffix(text):
    assert entity_tokens(text) == set()


def test_entity_tokens_still_detects_the_real_suffix_in_its_canonical_case():
    assert entity_tokens("Kessler Werke SpA supplies the group.") == {"Kessler Werke SpA"}


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
        # An execution block names the same registered company in capitals,
        # which is how deeds conventionally sign. Masking was case-sensitive
        # while the suffix match was not, so the cast entry failed to mask
        # and the caps rendering came back as an unchecked name.
        ("SIGNED for and on behalf of ASHFELL HOLDINGS LIMITED", "PASS"),
        ("EXECUTED as a deed by KESSLER WERKE GMBH", "PASS"),
        # The same asymmetry with only the suffix in capitals.
        ("The deed was sealed by Ashfell Holdings LIMITED.", "PASS"),
        # ...and an entity nobody checked is still caught.
        ("A deed with Unlisted Trading Limited.", "FAIL"),
        # Caps must not become a way to smuggle an unchecked entity past the
        # gate: the widened mask must still leave a genuinely unknown name.
        ("A DEED WITH UNLISTED TRADING LIMITED.", "FAIL"),
        # Markdown prose wraps, so a cast name arrives split across a line
        # break like any other phrase. The mask allowed spaces and tabs
        # between a name's words but not a newline, so the half after the
        # break kept its suffix and was reported as unchecked.
        ("The parties include Ashfell\nHoldings Limited and others.", "PASS"),
        ("Executed by Kessler\nWerke GmbH on the date shown.", "PASS"),
        ("A wrapped name indented on the\n    next line: Ashfell\n    Holdings Limited.", "PASS"),
        # ...and widening the separator must not open a blind spot: an
        # unknown entity split the same way is still caught.
        ("A deed with Unlisted\nTrading Limited.", "FAIL"),
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


def test_mask_cast_names_matches_a_cast_entry_whatever_its_case():
    # entity_tokens matches the SUFFIX case-insensitively by design
    # ("Kessler Werke GMBH" and "Ashfell Trading limited" must both be
    # detected), but the mask that runs first matched the cast entry
    # exactly. The two halves of one mechanism disagreed, so any rendering
    # of a registered name other than the cast list's own survived masking
    # and was reported as unchecked.
    for rendering in (
        "ASHFELL HOLDINGS LIMITED",
        "Ashfell Holdings LIMITED",
        "ashfell holdings limited",
    ):
        residue = mask_cast_names(rendering, {"Ashfell Holdings Limited"})
        assert entity_tokens(residue) == set(), rendering


def test_mask_cast_names_matches_a_name_wrapped_across_a_line_break():
    # Review 2026-08-26, B3. The separator was `[ \t]+`, so a cast name that
    # markdown wrapped mid-phrase never matched: the residue kept "Beauty
    # SAS" and gate 14 reported it as unchecked. Over one XS build this
    # produced nine false positives across three rounds, and hit the fact
    # sheet during /vdr-scope before a single document existed.
    for rendering in (
        "Ashfell\nHoldings Limited",
        "Ashfell Holdings\nLimited",
        "Ashfell\n    Holdings Limited",
        "Ashfell   \n\tHoldings Limited",
    ):
        residue = mask_cast_names(rendering, {"Ashfell Holdings Limited"})
        assert entity_tokens(residue) == set(), rendering


def test_mask_cast_names_does_not_reach_across_a_blank_line():
    # The reason the separator is `[ \t]*\n[ \t]*` and not `\s+`: one
    # newline is a wrap, two is a paragraph or table-row boundary, and a
    # mask that jumps one deletes text either side of a break that the cast
    # entry never spanned. Reporting is the safe direction here.
    residue = mask_cast_names(
        "Ashfell\n\nHoldings Limited", {"Ashfell Holdings Limited"}
    )
    assert entity_tokens(residue) != set()


def test_mask_cast_names_still_requires_whitespace_between_a_names_words():
    # And why it is not `[ \t]*\n?[ \t]*`: that alternative can match the
    # empty string, so a cast entry would mask a run-together word it has
    # no business touching.
    residue = mask_cast_names("AshfellHoldings Limited", {"Ashfell Holdings Limited"})
    assert "AshfellHoldings" in residue


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


# ---------------------------------------------------------------------------
# Round 5 — the cast must be scoped by Kind. Under the round-3 comparison
# these rows were inert; under masking they are active deletions from the
# document text, so a person row silently removes the capitalised words in
# front of an unchecked entity's suffix. Person rows never needed masking
# in the first place: entity_tokens cannot emit a suffix-less name, so a
# person's name was never going to become a candidate. Scoping also fixes
# hygiene, which was diagnosing a legitimate mononym person as malformed
# and offering a remedy (regenerate) that reproduces the row. The direction
# of risk is deliberate: an entity row miscategorised as a person is no
# longer masked, which produces a false positive rather than a silent miss.
# ---------------------------------------------------------------------------


def write_name_check(room, *rows):
    """Write _key/name-check.md from (name, kind) pairs."""
    body = "".join(f"| {name} | {kind} | clear | 2026-08-24 |\n" for name, kind in rows)
    (room / "_key" / "name-check.md").write_text(
        "| Name | Kind | Verdict | Checked |\n|---|---|---|---|\n" + body
    )


def test_gate_14_flags_an_entity_whose_words_a_person_row_would_have_masked(room):
    # The person rows spell every capitalised word in front of this
    # entity's suffix. Masked, "Daniel Oyelaran Ltd" loses the two words
    # that make it entity-shaped and the gate goes blind to it.
    write_name_check(
        room,
        ("Marta Vinceau", "person"),
        ("Daniel Oyelaran", "person"),
        ("Ashfell Holdings Limited", "entity"),
    )
    blind_doc(room).write_text("# Articles\n\nA deed with Daniel Oyelaran Ltd.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL", result.detail
    assert "Daniel Oyelaran Ltd" in result.detail


def test_cast_list_returns_only_entity_rows(room):
    write_name_check(
        room,
        ("Marta Vinceau", "person"),
        ("Ashfell Holdings Limited", "entity"),
    )
    assert cast_list(room / "_key" / "name-check.md") == {"Ashfell Holdings Limited"}


@pytest.mark.parametrize(
    "kind, expected",
    [
        # A mononym is an ordinary person, not a defect, and "regenerate the
        # name check" would only reproduce the row.
        ("person", "PASS"),
        # A one-word ENTITY row is still genuinely malformed: it would
        # blanket-mask every name ending in that word.
        ("entity", "FAIL"),
    ],
)
def test_gate_14_hygiene_is_scoped_to_entity_rows(room, kind, expected):
    write_name_check(room, ("Cher", kind), ("Ashfell Holdings Limited", "entity"))
    blind_doc(room).write_text("# Articles\n\nA deed with Ashfell Holdings Limited.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == expected, result.detail


@pytest.mark.parametrize("separator", ["|---|---|---|---|", "| --- | --- | --- | --- |"])
def test_cast_list_ignores_a_separator_row_in_any_spacing(room, separator):
    (room / "_key" / "name-check.md").write_text(
        "| Name | Kind | Verdict | Checked |\n"
        f"{separator}\n"
        "| Ashfell Holdings Limited | entity | clear | 2026-08-24 |\n"
    )
    path = room / "_key" / "name-check.md"
    assert cast_list(path) == {"Ashfell Holdings Limited"}
    # kind=None is what isolates the separator guard. With the Kind filter
    # on, a separator row is dropped anyway because its second cell does
    # not read "entity" — so the entity-scoped assertion above would keep
    # passing with no separator guard at all, and would not be a pin.
    assert cast_list(path, kind=None) == {"Ashfell Holdings Limited"}


def test_gate_14_does_not_report_a_separator_row_as_malformed(room):
    # The spaced form is the one a formatter produces and the one the old
    # startswith("|---") guard missed; read as a name it is a one-word row,
    # so it surfaced as a hygiene FAIL naming '---'.
    (room / "_key" / "name-check.md").write_text(
        "| Name | Kind | Verdict | Checked |\n"
        "| --- | --- | --- | --- |\n"
        "| Ashfell Holdings Limited | entity | clear | 2026-08-24 |\n"
    )
    blind_doc(room).write_text("# Articles\n\nA deed with Ashfell Holdings Limited.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "PASS", result.detail


# --- gate 14 reads the Verdict column, not only Name and Kind -------------
#
# Before these, nothing in synthvdr/ ever looked at a recorded verdict:
# cast_list takes columns 1 and 2 and stops, so a name the check had found to
# COLLIDE with a real company was masked out exactly like a cleared one, and
# passed /vdr-qa --strict and /vdr-package --strict. namecheck.unresolved()
# existed to catch precisely that and had no caller outside its own tests.


def _record(room, *rows):
    (room / "_key" / "name-check.md").write_text(
        "| Name | Kind | Verdict | Checked | Note |\n|---|---|---|---|---|\n"
        + "".join(f"| {name} | entity | {verdict} | 2026-08-24 | {note} |\n"
                 for name, verdict, note in rows)
    )


def test_gate_14_fails_on_a_recorded_collision(room):
    """/vdr-scope: "Do not close Gate A while any name's verdict is
    `collision` — that is a hard block ... there is no sign-off that waives
    a collision." A room built past that must not then pass QA."""
    _record(room, ("Ashfell Holdings Limited", "collision", "Real company, same sector."))
    blind_doc(room).write_text("# Articles\n\nA deed with Ashfell Holdings Limited.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Ashfell Holdings Limited" in result.detail
    assert "collision" in result.detail


def test_gate_14_reports_a_collision_even_with_no_documents_yet(room):
    """The verdict read runs BEFORE the blind-tree guard. A collision is a
    defect in the record itself, true from the moment /vdr-scope writes it —
    gating it on the corpus existing would hide it for all of /vdr-findings,
    which is the whole window in which regenerating the name is cheap."""
    _record(room, ("Ashfell Holdings Limited", "collision", "Real company."))
    blind_doc(room).unlink()
    assert gate_14_unchecked_names(ctx_for(room)).status == "FAIL"


@pytest.mark.parametrize("verdict", ["ambiguous", "unchecked", "clera"])
def test_gate_14_warns_but_does_not_fail_on_a_non_clear_non_collision_verdict(room, verdict):
    """Gate A does not block automatically on these — it requires the user's
    explicit acknowledgement — so the gate surfaces them without failing a
    build over a risk the user is entitled to accept. `unchecked` is what
    /vdr-scope writes when WebSearch was unavailable; 'clera' stands for any
    typo, which must land here rather than be read as cleared."""
    _record(room, ("Ashfell Holdings Limited", verdict, "See note."))
    blind_doc(room).write_text("# Articles\n\nA deed with Ashfell Holdings Limited.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "WARN", result.detail
    assert verdict in result.detail and "Ashfell Holdings Limited" in result.detail


def test_gate_14_prefers_the_collision_when_both_kinds_are_outstanding(room):
    _record(
        room,
        ("Ashfell Holdings Limited", "collision", "Real company."),
        ("Kessler Werke GmbH", "ambiguous", "Unclear."),
    )
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Ashfell Holdings Limited" in result.detail


def test_gate_14_still_reports_an_unchecked_name_alongside_an_outstanding_verdict(room):
    """An unchecked name in the corpus is the more fundamental problem and
    speaks first, but the outstanding verdict must not be dropped to make
    room for it — a reader fixing one should see both."""
    _record(room, ("Ashfell Holdings Limited", "ambiguous", "Unclear."))
    blind_doc(room).write_text("# Articles\n\nA deed with Northgate Trading Limited.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Northgate Trading Limited" in result.detail
    assert "ambiguous" in result.detail


def test_gate_14_passes_when_every_verdict_is_clear(room):
    """The control: the verdict read must not turn an ordinary clean room
    into a warning, or the WARN above carries no information."""
    _record(room, ("Ashfell Holdings Limited", "clear", "No collision found."))
    blind_doc(room).write_text("# Articles\n\nA deed with Ashfell Holdings Limited.\n")
    assert gate_14_unchecked_names(ctx_for(room)).status == "PASS"


# ---------------------------------------------------------------------------
# Defect 2 (found in the ll_vdr_06 findings session) — `entity_tokens` could
# not span a line break, so an entity name wrapped across two lines of
# fact-sheet prose was missed entirely; and it took a leading "The" into the
# token, so "The Helmswick Group Limited" was checked as a different name from
# "Helmswick Group Limited". `mask_cast_names` had already learned the first
# half of this lesson — markdown prose wraps, so a long name arrives split
# mid-phrase as a matter of course — and the two halves of one mechanism must
# agree about what a name looks like. The workaround until now was keeping
# entity names on one line in fact-sheet prose: a constraint on the author
# that the tool should be absorbing.
# ---------------------------------------------------------------------------


def test_entity_tokens_spans_a_single_line_break():
    text = "The parties include Unlisted\nTrading Limited and others.\n"
    assert entity_tokens(text) == {"Unlisted Trading Limited"}


def test_entity_tokens_normalises_the_whitespace_it_spans():
    # The token is a key: it is compared against the cast list and written
    # into the name check, both of which spell a name with single spaces.
    text = "Executed by Unlisted\n    Trading   Limited on the date shown.\n"
    assert entity_tokens(text) == {"Unlisted Trading Limited"}


def test_entity_tokens_drops_a_leading_the():
    assert entity_tokens("The Unlisted Trading Limited signed.") == {
        "Unlisted Trading Limited"
    }
    assert entity_tokens("THE UNLISTED TRADING LIMITED signed.") == {
        "UNLISTED TRADING LIMITED"
    }


def test_entity_tokens_keeps_the_when_dropping_it_would_leave_a_bare_suffix():
    # "The Limited" is not a name with a leading determiner; stripping it
    # would leave a bare corporate suffix, which is not a candidate at all.
    assert entity_tokens("A reference to The Limited here.") == {"The Limited"}


def test_gate_14_passes_on_a_cast_name_wrapped_across_a_line_break(room):
    blind_doc(room).write_text("# Articles\n\nThe parties include Ashfell\nHoldings Limited.\n")
    assert gate_14_unchecked_names(ctx_for(room)).status == "PASS"


def test_gate_14_still_flags_an_unchecked_name_wrapped_across_a_line_break(room):
    blind_doc(room).write_text("# Articles\n\nA deed with Unlisted\nTrading Limited.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Unlisted Trading Limited" in result.detail, (
        "the reported name must be spelled the way the cast list spells it, "
        "not carrying the line break it was found across"
    )


# ---------------------------------------------------------------------------
# Defect 3 (found during the ll_vdr_06 build; cost several remediation edits)
# — the gate fired wherever a capitalised word preceded a corporate suffix,
# including in ordinary English where no company was being named. Two shapes,
# two different fixes, and the bias stays where it was: a real uncoined
# counterparty slipping through is much worse than a false positive.
#
#   (a) "Limited" is the one suffix on the list that is also an ordinary
#       English adjective. Read as a suffix it ends a legal name; read as an
#       adjective it qualifies the noun after it — "a Private Limited
#       Company", "Independent Limited Assurance Report", "Private Company
#       Limited by Shares" (which is Companies House's own boilerplate). The
#       exclusion is a CLOSED list of the words that follow the adjective, not
#       a rule about capitalisation after the suffix: an incomplete list
#       leaves a false positive, which is the safe direction, whereas
#       "anything capitalised after Limited" would have silently swallowed
#       "<Unchecked Name> Limited Retirement Benefits Scheme".
#
#   (b) An org chart abbreviates: a box too narrow for "Helmswick Imaging
#       Limited" holds "Imaging Ltd". A candidate that is a trailing
#       sub-phrase of a name already on the cast list (with Ltd and Limited
#       read as the same suffix) is that name abbreviated. Note the direction
#       — this is the OPPOSITE containment to the trailing-sub-phrase walk
#       synthvdr/names.py's docstring records as rejected: there, a SHORT cast
#       entry excused a LONGER unchecked name; here, only a candidate SHORTER
#       than the cast entry is excused, and a longer one still fails.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prose",
    [
        "The Company is a Private Limited Company under the Companies Act 2006.",
        "Independent Limited Assurance Report of the auditors.",
        "Certificate of incorporation of a Private Company Limited by Shares.",
        "Registered as a Private Limited Company on 1 April 1998.",
    ],
)
def test_gate_14_does_not_fire_on_limited_used_as_an_adjective(room, prose):
    blind_doc(room).write_text(f"# Articles\n\n{prose}\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "PASS", result.detail


def test_entity_tokens_still_reads_limited_before_an_ordinary_capitalised_word():
    # The exclusion is a closed list of what follows the ADJECTIVE, not a
    # rule about capitalisation: a genuine name followed by a capitalised
    # word must still be found, or a counterparty named only inside a scheme
    # or document title would slip through.
    text = "The Unlisted Trading Limited Retirement Benefits Scheme was closed."
    assert entity_tokens(text) == {"Unlisted Trading Limited"}


def test_gate_14_accepts_an_org_chart_abbreviation_of_a_cast_name(room):
    # The room's cast holds "Ashfell Holdings Limited"; the structure chart's
    # boxes are too narrow for it.
    blind_doc(room).write_text(
        "# Group structure\n\n"
        "```\n"
        "│ Ashfell Holdings Limited │\n"
        "│ Holdings Ltd │ │ Holdings Limited │\n"
        "```\n"
    )
    assert gate_14_unchecked_names(ctx_for(room)).status == "PASS"


def test_gate_14_still_flags_a_longer_name_ending_in_a_cast_name(room):
    # The rejected direction, pinned: a cast entry must never excuse a
    # DIFFERENT, longer name that merely ends with it.
    blind_doc(room).write_text("# Articles\n\nA deed with Ashfell Trading Holdings Limited.\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Ashfell Trading Holdings Limited" in result.detail


def test_gate_14_still_flags_an_abbreviation_of_nothing_on_the_cast_list(room):
    blind_doc(room).write_text("# Group structure\n\n│ Trading Ltd │\n")
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Trading Ltd" in result.detail


# ---------------------------------------------------------------------------
# The fourth instance of the same class, and the last suffix on the list whose
# own casing collides with an ordinary English word. "Incorporated" was
# matched case-insensitively like every other suffix, and "incorporated" is
# one of the commonest words in a data room ("a private limited company
# incorporated in England on 17 April 1998"). Because `entity_tokens` needs
# capitalised words immediately before the suffix, the participle was read as
# a suffix wherever a capitalised run ran into it — and the damage is not a
# spurious extra candidate but a CORRUPTED one: the real name plus a trailing
# word, which is then searched, recorded and reported under a name that
# appears nowhere in the document.
#
# Fix, one register narrower than the SpA remedy: the initial letter must be
# capitalised, the rest is still matched in any case. A legal name's suffix is
# capitalised; the ordinary participle is not, and the one place it is —
# sentence-initial "Incorporated in 2004, the company ..." — has no
# capitalised word in front of it and so could never match anyway. Unlike a
# post-filter over the matches, this makes the regex BACK OFF to the shorter
# match and return the correct name rather than dropping it, which would turn
# a false positive into a miss.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        # The name is recovered, not merely the false positive dropped.
        (
            "Ashfell Advanced Materials Limited incorporated in 2004.",
            {"Ashfell Advanced Materials Limited"},
        ),
        (
            "Helmswick Imaging Limited incorporated on 17 April 1998.",
            {"Helmswick Imaging Limited"},
        ),
        (
            "A Private Company incorporated under the Companies Act 2006.",
            set(),
        ),
        # ...and the suffix itself still reads, in a capitalised name and in
        # the all-caps rendering a deed's execution block uses.
        ("Kessler Werke Incorporated supplies the group.", {"Kessler Werke Incorporated"}),
        ("EXECUTED as a deed by KESSLER WERKE INCORPORATED", {"KESSLER WERKE INCORPORATED"}),
        # The short form is untouched: "inc" is not an English word.
        ("Kessler Werke inc supplies the group.", {"Kessler Werke inc"}),
    ],
)
def test_entity_tokens_reads_incorporated_only_with_a_capital(text, expected):
    assert entity_tokens(text) == expected


def test_entity_tokens_gives_up_the_all_lowercase_incorporated():
    # The accepted cost, pinned so it is a decision rather than a surprise. A
    # genuine name whose suffix is written all in lower case is missed. That
    # is a typo shape, not a document shape — and it is the safe direction,
    # since the alternative is reading ordinary prose as a company name on
    # every room. `Ltd`, `limited` and the rest keep their case-insensitivity
    # (see test_entity_tokens_matches_suffixes_case_insensitively).
    assert entity_tokens("Ashfell Trading incorporated") == set()
    assert entity_tokens("Ashfell Trading limited") == {"Ashfell Trading limited"}


def test_gate_14_does_not_fire_on_the_participle(room):
    blind_doc(room).write_text(
        "# Articles\n\nAshfell Holdings Limited was incorporated in England on "
        "17 April 1998 and Kessler Werke GmbH incorporated in 2004.\n"
    )
    assert gate_14_unchecked_names(ctx_for(room)).status == "PASS"


def test_gate_14_names_an_unchecked_entity_the_way_the_document_spells_it(room):
    # The other half of the damage: a genuine hit used to be reported as
    # "Unlisted Trading Limited incorporated", sending an author to look for
    # a name the document does not contain.
    blind_doc(room).write_text(
        "# Articles\n\nUnlisted Trading Limited incorporated in Jersey in 2019.\n"
    )
    result = gate_14_unchecked_names(ctx_for(room))
    assert result.status == "FAIL"
    assert "Unlisted Trading Limited" in result.detail
    assert "incorporated" not in result.detail
