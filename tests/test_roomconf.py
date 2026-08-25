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


def test_get_int_on_non_integer_raises(tmp_path):
    conf = load_room_conf(write(tmp_path))
    with pytest.raises(RoomConfError, match="not an integer"):
        conf.get_int("ROOM_CODENAME")


def test_load_missing_file_raises(tmp_path):
    missing = tmp_path / "nonexistent.conf"
    with pytest.raises(RoomConfError, match="no room.conf"):
        load_room_conf(missing)


def test_get_absent_non_required_key_raises(tmp_path):
    conf = load_room_conf(write(tmp_path))
    with pytest.raises(RoomConfError, match="missing key"):
        conf.get("NONEXISTENT_KEY")


def test_get_pattern_returns_raw_value(tmp_path):
    conf = load_room_conf(write(tmp_path))
    assert conf.get_pattern("FINDING_PREFIXES") == "CORP|ENV|FIN"


def test_trailing_comment_on_bare_value(tmp_path):
    conf = load_room_conf(write(tmp_path, SAMPLE.replace("INDEX_TOTAL=20", "INDEX_TOTAL=20  # total docs")))
    assert conf.get_int("INDEX_TOTAL") == 20


def test_trailing_comment_on_quoted_value(tmp_path):
    conf = load_room_conf(
        write(tmp_path, SAMPLE.replace('BLIND_TREE="data-room"', 'BLIND_TREE="data-room"  # root docs'))
    )
    assert conf.get("BLIND_TREE") == "data-room"


def test_hash_inside_quoted_value_is_preserved(tmp_path):
    conf = load_room_conf(write(tmp_path, SAMPLE.replace('FLAG_STRING_1="Key diligence points"', 'FLAG_STRING_1="Key # diligence"')))
    assert conf.get("FLAG_STRING_1") == "Key # diligence"


def test_unterminated_quote_raises(tmp_path):
    conf_text = SAMPLE.replace('ROOM_CODENAME="Project Testbed"', 'ROOM_CODENAME="Project Testbed')
    with pytest.raises(RoomConfError, match="unterminated"):
        load_room_conf(write(tmp_path, conf_text))


def test_malformed_line_raises(tmp_path):
    conf_text = SAMPLE + "export KEY=VALUE\n"
    with pytest.raises(RoomConfError, match="malformed"):
        load_room_conf(write(tmp_path, conf_text))


def test_trailing_character_after_quoted_value_raises(tmp_path):
    conf_text = SAMPLE.replace('BLIND_TREE="data-room"', 'BLIND_TREE="data-room"x')
    with pytest.raises(RoomConfError, match="unexpected trailing text"):
        load_room_conf(write(tmp_path, conf_text))


def test_hash_without_space_after_quoted_value_raises(tmp_path):
    conf_text = SAMPLE.replace('FLAG_STRING_2="DD flag"', 'FLAG_STRING_2="DD flag"#nospace')
    with pytest.raises(RoomConfError, match="unexpected trailing text"):
        load_room_conf(write(tmp_path, conf_text))


# ---------------------------------------------------------------------------
# Path hygiene: BLIND_TREE, FLAGGED_TREE and KEY_ROOT are turned into real
# filesystem paths and, for FLAGGED_TREE, fed to shutil.rmtree — a bad value
# here is a destructive-path risk. Each must be non-empty, relative, and
# free of a '..' segment, checked at load time so every consumer is covered.
# ---------------------------------------------------------------------------

_ORIGINAL_PATH_LINE = {
    "BLIND_TREE": 'BLIND_TREE="data-room"',
    "FLAGGED_TREE": 'FLAGGED_TREE="_key/flagged"',
    "KEY_ROOT": 'KEY_ROOT="_key"',
}


@pytest.mark.parametrize("key", ["BLIND_TREE", "FLAGGED_TREE", "KEY_ROOT"])
@pytest.mark.parametrize(
    "bad_value", ["", "/", "/tmp/x", "../../escape", ".", "./", "./."]
)
def test_path_valued_key_rejects_unsafe_value(tmp_path, key, bad_value):
    conf_text = SAMPLE.replace(_ORIGINAL_PATH_LINE[key], f'{key}="{bad_value}"')
    assert conf_text != SAMPLE
    with pytest.raises(RoomConfError, match=key):
        load_room_conf(write(tmp_path, conf_text))


@pytest.mark.parametrize("key", ["BLIND_TREE", "FLAGGED_TREE", "KEY_ROOT"])
def test_path_valued_key_accepts_a_nested_relative_value(tmp_path, key):
    conf_text = SAMPLE.replace(_ORIGINAL_PATH_LINE[key], f'{key}="a/b/c"')
    conf = load_room_conf(write(tmp_path, conf_text))
    assert conf.get(key) == "a/b/c"


@pytest.mark.parametrize("key", ["BLIND_TREE", "FLAGGED_TREE", "KEY_ROOT"])
def test_path_valued_key_does_not_false_positive_on_a_dotted_segment(tmp_path, key):
    # Subsection directories legitimately contain dots (e.g. a section
    # numbered '11.2'); the '..' check must key on whole path segments, not
    # on the substring '..' appearing anywhere in the value.
    conf_text = SAMPLE.replace(_ORIGINAL_PATH_LINE[key], f'{key}="11.2_site-reports/x"')
    conf = load_room_conf(write(tmp_path, conf_text))
    assert conf.get(key) == "11.2_site-reports/x"


# ---------------------------------------------------------------------------
# Tree layout: containment alone isn't enough — the room root itself is
# trivially "inside the room", and BLIND_TREE, FLAGGED_TREE and KEY_ROOT
# must additionally be genuinely separate trees, or build_flagged_tree's
# delete-and-rebuild of FLAGGED_TREE can destroy or leak into whichever one
# it overlaps. FLAGGED_TREE nested *inside* KEY_ROOT is the one exception:
# that's the intended layout (the flagged twin is answer-key material, and
# answer-key material lives under KEY_ROOT), so only the reverse direction
# (KEY_ROOT at or under FLAGGED_TREE) is rejected for that pair.
# ---------------------------------------------------------------------------


def _conf_text_with_trees(blind, flagged, key_root):
    text = SAMPLE
    text = text.replace('BLIND_TREE="data-room"', f'BLIND_TREE="{blind}"')
    text = text.replace('FLAGGED_TREE="_key/flagged"', f'FLAGGED_TREE="{flagged}"')
    text = text.replace('KEY_ROOT="_key"', f'KEY_ROOT="{key_root}"')
    return text


@pytest.mark.parametrize(
    "blind, flagged, key_root, expected_match",
    [
        ("data-room", "data-room", "_key", "BLIND_TREE"),
        ("data-room", "data-room/sub", "_key", "BLIND_TREE"),
        ("data-room/sub", "data-room", "_key", "BLIND_TREE"),
        ("data-room", "_key/flagged", "data-room/sub", "BLIND_TREE"),
        ("data-room/sub", "_key/flagged", "data-room", "BLIND_TREE"),
        ("data-room", "_key", "_key/flagged", "KEY_ROOT"),
    ],
    ids=[
        "flagged==blind",
        "flagged-inside-blind",
        "blind-inside-flagged",
        "key-inside-blind",
        "blind-inside-key",
        "key-inside-flagged",
    ],
)
def test_pairwise_tree_overlap_is_rejected(tmp_path, blind, flagged, key_root, expected_match):
    conf_text = _conf_text_with_trees(blind, flagged, key_root)
    with pytest.raises(RoomConfError, match=expected_match):
        load_room_conf(write(tmp_path, conf_text))


def test_flagged_nested_inside_key_root_is_accepted(tmp_path):
    # The one legitimate overlap: FLAGGED_TREE living under KEY_ROOT, since
    # the flagged twin is itself answer-key material.
    conf_text = _conf_text_with_trees("data-room", "_key/nested/flagged", "_key")
    conf = load_room_conf(write(tmp_path, conf_text))
    assert conf.get("FLAGGED_TREE") == "_key/nested/flagged"


# ---------------------------------------------------------------------------
# Case-insensitivity: a case-insensitive filesystem (macOS, Windows) treats
# 'data-room' and 'DATA-ROOM' as the same directory regardless of what OS
# this check runs on, so the overlap comparison must be case-insensitive
# unconditionally, not just when the host happens to be case-insensitive.
# String comparison alone can't see every aliasing route (a hardlink, bind
# mount, or symlink can also make two differently-spelled paths the same
# directory) — that residual risk is covered by build_flagged_tree's
# os.path.samefile backstop in synthvdr/twin.py, not here at the config
# level, since load_room_conf never touches the filesystem.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "blind, flagged, key_root, expected_match",
    [
        ("data-room", "DATA-ROOM", "_key", "BLIND_TREE"),
        ("data-room", "Data-Room", "_key", "BLIND_TREE"),
        ("data-room", "data-ROOM", "_key", "BLIND_TREE"),
        ("data-room", "_key/flagged", "DATA-ROOM", "BLIND_TREE"),
        ("data-room", "_KEY", "_key/sub", "KEY_ROOT"),
    ],
    ids=[
        "flagged=DATA-ROOM",
        "flagged=Data-Room",
        "flagged=data-ROOM",
        "key=DATA-ROOM (aliases blind)",
        "flagged=_KEY (key nested inside, case-aliased)",
    ],
)
def test_pairwise_tree_overlap_is_rejected_case_insensitively(
    tmp_path, blind, flagged, key_root, expected_match
):
    conf_text = _conf_text_with_trees(blind, flagged, key_root)
    with pytest.raises(RoomConfError, match=expected_match):
        load_room_conf(write(tmp_path, conf_text))


def test_canonical_and_legitimate_layouts_still_load_under_case_folding(tmp_path):
    # Regression guard: case-folding the comparison must not make any
    # genuinely-separate, correctly-spelled layout look like an overlap.
    conf = load_room_conf(write(tmp_path, SAMPLE))
    assert conf.get("BLIND_TREE") == "data-room"
    assert conf.get("FLAGGED_TREE") == "_key/flagged"
    assert conf.get("KEY_ROOT") == "_key"


def test_get_relative_path_validates_a_non_required_key_on_demand(tmp_path):
    conf = load_room_conf(write(tmp_path, SAMPLE + 'SUBSET_OUT="_key/subset"\n'))
    assert conf.get_relative_path("SUBSET_OUT") == "_key/subset"


@pytest.mark.parametrize("bad_value", ["", "/etc", "../escape"])
def test_get_relative_path_rejects_unsafe_value_on_demand(tmp_path, bad_value):
    # SUBSET_OUT isn't in REQUIRED_KEYS, so load_room_conf lets it through
    # unchecked (per test_unknown_key_is_kept_not_rejected above) — the
    # check only happens when a later tool calls get_relative_path on it.
    conf = load_room_conf(write(tmp_path, SAMPLE + f'SUBSET_OUT="{bad_value}"\n'))
    with pytest.raises(RoomConfError, match="SUBSET_OUT"):
        conf.get_relative_path("SUBSET_OUT")


@pytest.mark.parametrize("bad_value", [".", "./", "./."])
def test_get_relative_path_rejects_a_value_that_normalises_to_the_room_root(tmp_path, bad_value):
    # Isolates the "normalises to the room root" rule in _check_relative_path
    # from _check_tree_layout's pairwise checks: SUBSET_OUT is a standalone
    # key outside BLIND_TREE/FLAGGED_TREE/KEY_ROOT, so this can only be
    # caught by get_relative_path's own check, not by a coincidental overlap
    # with one of the three core trees.
    conf = load_room_conf(write(tmp_path, SAMPLE + f'SUBSET_OUT="{bad_value}"\n'))
    with pytest.raises(RoomConfError, match="normalises to the room root"):
        conf.get_relative_path("SUBSET_OUT")
