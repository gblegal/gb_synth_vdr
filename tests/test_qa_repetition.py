import pytest

from synthvdr.qa.repetition import (
    MIN_WORDS,
    THRESHOLD,
    duplicate_paragraph_share,
    gate_21_repetition,
    paragraphs,
)
from synthvdr.qa.runner import GateContext
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import FindingSet

CONF = '''ROOM_CODENAME="Project Testbed"
INDEX_TOTAL=1
BLIND_TOTAL=1
FLAGGED_TOTAL=1
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="ENV"
EXPECTED_KDP_CARRIERS=0
SECTION_DIRS="05_commercial"
'''


def para(i, words=40):
    """A distinct paragraph: every token carries its paragraph number."""
    return " ".join(f"w{i}x{j}" for j in range(words))


def test_distinct_paragraphs_score_zero():
    assert duplicate_paragraph_share("\n\n".join(f"{i}.1 {para(i)}" for i in range(60))) == 0.0


def test_half_the_document_repeated_scores_above_the_threshold():
    body = [para(i) for i in range(60)]
    assert duplicate_paragraph_share("\n\n".join(body + body[30:])) > THRESHOLD


def test_one_paragraph_pasted_five_times_scores_above_the_threshold():
    body = [para(i) for i in range(30)]
    assert duplicate_paragraph_share("\n\n".join(body + [body[3]] * 5)) > THRESHOLD


def test_repeats_that_differ_only_in_their_clause_number_still_match():
    p = para(1)
    text = "\n\n".join([f"14.2 {p}", f"15.3 {p}"] + [para(i) for i in range(2, 10)])
    assert duplicate_paragraph_share(text) > 0


def test_table_blocks_and_short_paragraphs_are_ignored():
    table = "| Name | Role |\n|---|---|\n| A | B |"
    signature = "Signed by the Director for and on behalf of the Company"
    text = "\n\n".join([para(i) for i in range(10)] + [table, table, signature, signature, signature])
    assert duplicate_paragraph_share(text) == 0.0


def test_a_repeated_long_table_block_is_ignored():
    # 36 alphanumeric tokens, so it clears the MIN_PARAGRAPH_TOKENS floor and would
    # count as a duplicate if the table-skip line were removed. Tests that table-block
    # skipping is not redundant with the token floor.
    rows = "\n".join(f"| Data{r}A | Data{r}B | Data{r}C | Data{r}D |" for r in range(8))
    table = "| Column1 | Column2 | Column3 | Column4 |\n|---|---|---|---|\n" + rows
    assert len([t for t in table.lower().replace("|", " ").replace("-", " ").split() if t]) >= 32
    text = "\n\n".join([para(i) for i in range(10)] + [table, table])
    # If table-skipping is active, share should be 0.0 (tables not counted at all)
    assert duplicate_paragraph_share(text) == 0.0


def test_a_compilation_of_short_form_paragraphs_is_not_padding():
    # Six conformed copies of one form: five boilerplate paragraphs of 24 tokens each, as
    # in ll_vdr_09's stock transfer form compilation. Under the 30-token floor they are
    # form text, not drafting the author could have written afresh.
    form = [para(100 + k, words=24) for k in range(5)]
    text = "\n\n".join(form * 6 + [para(i) for i in range(10)])
    assert duplicate_paragraph_share(text) == 0.0


def test_crlf_documents_split_into_the_same_paragraphs():
    text = "\n\n".join(para(i) for i in range(5))
    assert paragraphs(text.replace("\n", "\r\n")) == paragraphs(text)


@pytest.fixture
def room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF)
    (tmp_path / "data-room" / "05_commercial" / "5.1_customer-contracts").mkdir(parents=True)
    return tmp_path


def ctx_for(room):
    return GateContext(
        room=room, conf=load_room_conf(room / "room.conf"), findings=FindingSet([], ""), distractors=[]
    )


def write(room, name, text):
    (room / "data-room" / "05_commercial" / "5.1_customer-contracts" / name).write_text(text)


def test_gate_passes_a_room_with_no_long_document_and_says_so(room):
    write(room, "5.1.1_customer-contracts-01.md", "\n\n".join(para(i) for i in range(10)))
    result = gate_21_repetition(ctx_for(room))
    assert result.status == "PASS"
    assert f"no document reaches {MIN_WORDS:,} words" in result.detail


def test_gate_fails_a_padded_long_document_and_names_it(room):
    body = [para(i) for i in range(60)]
    write(room, "5.1.1_customer-contracts-01.md", "\n\n".join(body + body[:30]))
    result = gate_21_repetition(ctx_for(room))
    assert result.status == "FAIL"
    assert "5.1.1_customer-contracts-01.md" in result.detail


def test_gate_ignores_a_padded_short_document(room):
    body = [para(i) for i in range(20)]
    write(room, "5.1.1_customer-contracts-01.md", "\n\n".join(body + body))
    assert gate_21_repetition(ctx_for(room)).status == "PASS"


def test_gate_skips_when_the_blind_tree_is_empty(tmp_path):
    (tmp_path / "room.conf").write_text(CONF)
    assert gate_21_repetition(ctx_for(tmp_path)).status == "SKIP"
