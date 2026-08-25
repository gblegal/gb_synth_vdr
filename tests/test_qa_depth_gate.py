import pytest

from synthvdr.qa.depth import gate_10_depth
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
SECTION_DIRS="01_corporate"
'''


@pytest.fixture
def room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF)
    d = tmp_path / "data-room" / "01_corporate" / "1.1_constitutional"
    d.mkdir(parents=True)
    (d / "1.1.1_articles.md").write_text("# Articles\n\n" + ("word " * 400))
    (tmp_path / "_key").mkdir()
    (tmp_path / "_key" / "anchors.csv").write_text(
        "slot_id,tier,rel_path\n1.1.1,F,01_corporate/1.1_constitutional/1.1.1_articles.md\n"
    )
    return tmp_path


def ctx_for(room):
    return GateContext(
        room=room, conf=load_room_conf(room / "room.conf"), findings=FindingSet([], ""), distractors=[]
    )


def test_passes_when_above_the_tier_f_floor(room):
    # The fixture body contains none of PLACEHOLDER_MARKERS below, so this
    # also stands as the "clean document still passes" check: the
    # placeholder mechanism has not been widened into always-failing.
    assert gate_10_depth(ctx_for(room)).status == "PASS"


def test_fails_below_the_floor(room):
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text("# Articles\n\n" + ("word " * 20))
    assert gate_10_depth(ctx_for(room)).status == "FAIL"


def test_fail_detail_also_names_the_metric(room):
    # PASS already named the counting metric; a reader who hits a FAIL and
    # wants to argue with the number needs to see it too.
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text("# Articles\n\n" + ("word " * 20))
    result = gate_10_depth(ctx_for(room))
    assert result.status == "FAIL"
    assert "metric" in result.detail.lower()


# The full table this gate must catch: the five original literals
# ("todo", "tbd", "[insert", "xxx", "lorem ipsum"), the two literals added
# for the enumeration fix ("fixme", "placeholder"), and two instances of
# the bracket-idiom property ("[DRAFT]", "[TBC]") that no literal list
# could name in advance. A previous round silently dropped the first five
# by rewriting the list instead of extending it — parametrized so a future
# edit that drops any one shape shows up as a single named failure rather
# than a silent gap.
PLACEHOLDER_MARKERS = [
    "TODO: write this",
    "tbd",
    "XXX",
    "[insert amount]",
    "[DRAFT]",
    "[TBC]",
    "fixme",
    "placeholder",
    "Lorem Ipsum dolor",
]


@pytest.mark.parametrize("marker", PLACEHOLDER_MARKERS)
def test_fails_on_a_placeholder_marker(room, marker):
    p = room / "data-room/01_corporate/1.1_constitutional/1.1.1_articles.md"
    p.write_text(f"# Articles\n\n{marker}\n" + ("word " * 400))
    result = gate_10_depth(ctx_for(room))
    assert result.status == "FAIL"
    assert "placeholder" in result.detail.lower()


@pytest.mark.parametrize("bad_tier", ["a", "f", "", "X"])
def test_an_invalid_tier_is_a_hard_failure_naming_the_slot_and_value(room, bad_tier):
    (room / "_key" / "anchors.csv").write_text(
        f"slot_id,tier,rel_path\n1.1.1,{bad_tier},01_corporate/1.1_constitutional/1.1.1_articles.md\n"
    )
    result = gate_10_depth(ctx_for(room))
    assert result.status == "FAIL"
    assert "1.1.1" in result.detail
    assert repr(bad_tier) in result.detail


def test_a_slot_missing_from_the_manifest_is_a_failure(room):
    (room / "_key" / "anchors.csv").write_text("slot_id,tier,rel_path\n")
    result = gate_10_depth(ctx_for(room))
    assert result.status == "FAIL"
    assert "1.1.1" in result.detail


def test_skips_when_there_is_no_anchors_manifest(room):
    (room / "_key" / "anchors.csv").unlink()
    assert gate_10_depth(ctx_for(room)).status == "SKIP"
