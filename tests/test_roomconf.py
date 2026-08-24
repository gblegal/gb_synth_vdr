import pytest

from synthvdr.roomconf import RoomConfError, load_room_conf

SAMPLE = '''# a comment
ROOM_CODENAME="Project Testbed"
INDEX_TOTAL=20
BLIND_TOTAL=20
FLAGGED_TOTAL=20
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="CORP|ENV|FIN"
EXPECTED_KDP_CARRIERS=4
SECTION_DIRS="01_corporate 11_environmental-hs"
'''


def write(tmp_path, text=SAMPLE):
    p = tmp_path / "room.conf"
    p.write_text(text)
    return p


def test_reads_strings_ints_and_lists(tmp_path):
    conf = load_room_conf(write(tmp_path))
    assert conf.get("ROOM_CODENAME") == "Project Testbed"
    assert conf.get_int("INDEX_TOTAL") == 20
    assert conf.get_list("SECTION_DIRS") == ["01_corporate", "11_environmental-hs"]


def test_ignores_comments_and_blank_lines(tmp_path):
    conf = load_room_conf(write(tmp_path, "# only a comment\n\n" + SAMPLE))
    assert conf.get("BLIND_TREE") == "data-room"


def test_missing_required_key_raises(tmp_path):
    stripped = "\n".join(
        line for line in SAMPLE.splitlines() if not line.startswith("KEY_ROOT")
    )
    with pytest.raises(RoomConfError, match="KEY_ROOT"):
        load_room_conf(write(tmp_path, stripped))


def test_unknown_key_is_kept_not_rejected(tmp_path):
    conf = load_room_conf(write(tmp_path, SAMPLE + 'CUSTOM_THING="hello"\n'))
    assert conf.get("CUSTOM_THING") == "hello"
