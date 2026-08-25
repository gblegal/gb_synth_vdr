import re

import pytest

from synthvdr.names import entity_tokens
from synthvdr.qa.leakage import (
    ANSWER_KEY_NOUNS,
    BUILD_VOCABULARY,
    _hits,
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
    # A trailing underscore ("ENV-1_articles.md") sits inside \w, so \b
    # would not fire right after the digit — this uses a boundary the
    # pattern actually recognises, to isolate "is the filename swept at
    # all" from finding_id_pattern's own boundary character set.
    (room / "data-room" / "01_corporate" / "1.1_constitutional" / "ENV-1.md").write_text(
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
# Review finding C — a corporate suffix must be recognised regardless of how
# it is cased; gate 14 can never flag what entity_tokens never sees.
# ---------------------------------------------------------------------------


def test_entity_tokens_matches_suffixes_case_insensitively():
    assert entity_tokens("Ashfell Trading limited") == {"Ashfell Trading limited"}
    assert entity_tokens("Kessler Werke GMBH") == {"Kessler Werke GMBH"}


# ---------------------------------------------------------------------------
# Review finding D — a leading determiner must not be absorbed into the
# captured name: that turns ordinary prose into an unrecognised "entity"
# that fails gate 14 even though the real name is on the cast list.
# ---------------------------------------------------------------------------


def test_entity_tokens_does_not_absorb_a_leading_determiner():
    assert entity_tokens("The Ashfell Holdings Limited was incorporated in 2019.") == {
        "Ashfell Holdings Limited"
    }
    # Sanity: positions and phrasing the fix must not disturb.
    assert entity_tokens("Ashfell Holdings Limited is a company.") == {"Ashfell Holdings Limited"}
    assert entity_tokens("Contracts were exchanged. Kessler Werke GmbH signed the deed.") == {
        "Kessler Werke GmbH"
    }
    assert entity_tokens(
        "Ashfell Holdings Limited entered a contract with Kessler Werke GmbH in March."
    ) == {"Ashfell Holdings Limited", "Kessler Werke GmbH"}


def test_gate_14_does_not_false_fail_on_a_leading_determiner(room):
    blind_doc(room).write_text("# Articles\n\nThe Ashfell Holdings Limited was incorporated in 2019.\n")
    assert gate_14_unchecked_names(ctx_for(room)).status == "PASS"


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
