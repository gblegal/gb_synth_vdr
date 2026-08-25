import pytest

from synthvdr.names import entity_tokens
from synthvdr.qa.leakage import (
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
