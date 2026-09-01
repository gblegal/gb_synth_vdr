import itertools
import re
import unicodedata

import pytest

from synthvdr.roomconf import (
    PATH_KEYS,
    ROOM_ROOT_LABEL,
    RoomConfError,
    _casefolded_parts,
    load_room_conf,
    resolve_tree_map,
)

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


# ---------------------------------------------------------------------------
# Review item 16: a key set twice was the one malformation in room.conf that
# was NOT rejected by line number — it silently took the last value, and every
# gate downstream then checked the room against a number the author can see in
# the file and did not intend.


def test_a_key_set_twice_is_rejected_naming_both_lines(tmp_path):
    with pytest.raises(RoomConfError) as excinfo:
        load_room_conf(write(tmp_path, SAMPLE + "INDEX_TOTAL=99\n"))
    message = str(excinfo.value)
    assert "INDEX_TOTAL is set again" in message
    # Both values and both line numbers, so the author can see which line to
    # delete without opening the file to work out where the other one is.
    assert "'99'" in message and "'20'" in message
    assert re.search(r"line \d+:", message) and re.search(r"on line \d+", message)


def test_a_key_repeated_with_the_same_value_is_rejected_too(tmp_path):
    # Deliberate, not an oversight: a repeat means one of two copies was
    # edited, and the values matching is luck about which one.
    with pytest.raises(RoomConfError, match="INDEX_TOTAL is set again"):
        load_room_conf(write(tmp_path, SAMPLE + "INDEX_TOTAL=20\n"))


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
# These cases are load-time only — the directories are never created — so
# os.path.samefile cannot fire for them, which is what makes them the
# case-folded comparison's own coverage rather than a second route to the
# same rejection.
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


# ---------------------------------------------------------------------------
# Property 1 — a configured tree must live exactly where it says it does.
#
#   (room / value).resolve() == room.resolve() / normalised(value)
#
# If any component of the path — the final one or any ancestor — is a symlink
# that redirects, those two differ and the value is rejected. One rule kills
# the whole symlink class, rather than enumerating the shapes it can take.
#
# Every case below points the symlink at a sibling that overlaps nothing, so
# the pairwise checks in Property 2 have nothing to fire on and only Property
# 1 can catch it. Both are parametrised over PATH_KEYS, so a path key added
# later is exercised here automatically.
# ---------------------------------------------------------------------------


def _conf_text_with_key(key, value):
    text = SAMPLE.replace(_ORIGINAL_PATH_LINE[key], f'{key}="{value}"')
    assert text != SAMPLE, f"{key} line not found in SAMPLE — fixture out of date"
    return text


@pytest.mark.parametrize("key", PATH_KEYS)
def test_a_path_key_reached_through_a_symlinked_ancestor_is_rejected(tmp_path, key):
    # 'redirect' is an ancestor component, not the tree itself: the value
    # 'redirect/inner' looks like a plain relative path and passes every
    # string-level check, but lands on <room>/elsewhere/inner.
    (tmp_path / "elsewhere" / "inner").mkdir(parents=True)
    (tmp_path / "redirect").symlink_to(tmp_path / "elsewhere")

    with pytest.raises(RoomConfError, match="does not resolve to where it says"):
        load_room_conf(write(tmp_path, _conf_text_with_key(key, "redirect/inner")))


@pytest.mark.parametrize("key", PATH_KEYS)
def test_a_path_key_that_is_itself_a_symlink_is_rejected(tmp_path, key):
    # The final component redirecting is the same defect as an ancestor
    # redirecting, and is caught by the same rule — no special case for it.
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "redirect").symlink_to(tmp_path / "elsewhere")

    with pytest.raises(RoomConfError, match="does not resolve to where it says"):
        load_room_conf(write(tmp_path, _conf_text_with_key(key, "redirect")))


@pytest.mark.parametrize("key", PATH_KEYS)
def test_a_path_key_whose_symlink_leaves_the_room_is_rejected(tmp_path, key):
    # The other half of Property 1: the resolved tree must be a proper
    # subdirectory of the room. Stated on the RESOLVED path rather than on
    # the spelling of the value, because "looks absolute" is host-specific —
    # on Windows 'C:\\Windows' and a UNC path are absolute without starting
    # with '/', so the string checks in _check_relative_path would pass them
    # and the equality half of Property 1 would too (they resolve to
    # themselves). Here the same rule is reached on POSIX via a symlink
    # pointing out of the room.
    room = tmp_path / "room"
    room.mkdir()
    (tmp_path / "outside").mkdir()
    (room / "redirect").symlink_to(tmp_path / "outside")
    conf_path = room / "room.conf"
    conf_path.write_text(_conf_text_with_key(key, "redirect/inner"))

    with pytest.raises(RoomConfError, match="resolves outside the room root"):
        load_room_conf(conf_path)


@pytest.mark.parametrize("key", PATH_KEYS)
def test_a_path_key_that_symlinks_to_the_room_root_is_rejected(tmp_path, key):
    room = tmp_path / "room"
    room.mkdir()
    (room / "redirect").symlink_to(room)
    conf_path = room / "room.conf"
    conf_path.write_text(_conf_text_with_key(key, "redirect"))

    with pytest.raises(RoomConfError, match="room root itself"):
        load_room_conf(conf_path)


@pytest.mark.parametrize(
    "bad_value, reason",
    [
        ("", "is empty"),
        ("/tmp/x", "is an absolute path"),
        ("../../escape", r"contains a '\.\.' segment"),
        (".", "normalises to the room root itself"),
    ],
)
def test_an_unsafe_path_value_is_reported_with_its_specific_reason(tmp_path, bad_value, reason):
    # Property 1's containment rule would reject all four of these anyway,
    # since none of them resolves to a proper subdirectory of the room — so
    # without this test the cheap string checks in _check_relative_path look
    # like dead code from load_room_conf's point of view. They are not: they
    # are what turns "this landed somewhere unexpected" into a message naming
    # the actual mistake, and they are the only validation get_relative_path
    # has, since it never sees a room to resolve against.
    conf_text = _conf_text_with_key("FLAGGED_TREE", bad_value)
    with pytest.raises(RoomConfError, match=reason):
        load_room_conf(write(tmp_path, conf_text))


@pytest.mark.parametrize("key", PATH_KEYS)
def test_a_path_key_on_a_plain_relative_path_survives_property_one(tmp_path, key):
    # The positive control: Property 1 must not reject a tree that really
    # does live where it says, whether or not the directory exists yet.
    (tmp_path / "elsewhere").mkdir()
    conf = load_room_conf(write(tmp_path, _conf_text_with_key(key, "elsewhere")))
    assert conf.get(key) == "elsewhere"


# ---------------------------------------------------------------------------
# Property 2 — every pair of configured trees (plus the room root) must name
# a distinct directory, checked generically over the key set.
#
# The Unicode case is the one that proves the device/inode comparison is
# load-bearing: NFC 'café-key' and NFD 'café-key' are different byte strings
# that casefold() does not reconcile (casefold does not normalise Unicode),
# yet on a normalising filesystem they are one directory. Skipped via a real
# capability probe — create the NFC name, look for the NFD one — not an
# os.name guess.
# ---------------------------------------------------------------------------


def _unicode_alias_pair(tmp_path):
    """(nfc, nfd) spellings of one name, with the NFC directory created.
    Skips the calling test if this filesystem keeps the two apart."""
    nfc = unicodedata.normalize("NFC", "café-key")
    nfd = unicodedata.normalize("NFD", "café-key")
    assert nfc != nfd, "test assumption broken: NFC and NFD must differ as plain strings"
    (tmp_path / nfc).mkdir()
    if not (tmp_path / nfd).exists():
        pytest.skip("host filesystem does not alias Unicode NFC/NFD spellings")
    return nfc, nfd


def test_unicode_normalisation_alias_between_two_path_keys_is_rejected(tmp_path):
    nfc, nfd = _unicode_alias_pair(tmp_path)
    assert _casefolded_parts(tmp_path / nfc) != _casefolded_parts(tmp_path / nfd), (
        "test assumption broken: the casefolded string comparison must not "
        "catch this on its own, or the test isn't isolating the samefile check"
    )
    conf_text = _conf_text_with_trees("data-room", nfd, nfc)

    with pytest.raises(RoomConfError, match="are the same directory"):
        load_room_conf(write(tmp_path, conf_text))


def test_unicode_alias_is_not_reported_for_two_genuinely_separate_trees(tmp_path):
    # Positive control for the samefile check: the same filesystem, two
    # accented names that are genuinely different directories, must load.
    _unicode_alias_pair(tmp_path)
    conf_text = _conf_text_with_trees("data-room", "café-flagged", "café-key")
    conf = load_room_conf(write(tmp_path, conf_text))
    assert conf.get("KEY_ROOT") == "café-key"


# ---------------------------------------------------------------------------
# Generality: the pairwise machinery must be driven by the key set, not by a
# hardcoded list of pairs. These two tests read PATH_KEYS directly, so adding
# a path key without teaching the checkers about it fails here rather than
# shipping an unguarded key.
# ---------------------------------------------------------------------------


def test_resolve_tree_map_covers_every_path_key_and_the_room_root(tmp_path):
    conf_path = write(tmp_path, SAMPLE)
    resolved = resolve_tree_map(tmp_path, load_room_conf(conf_path).values)
    assert set(resolved) == set(PATH_KEYS) | {ROOM_ROOT_LABEL}
    assert resolved[ROOM_ROOT_LABEL] == tmp_path.resolve()


@pytest.mark.parametrize("key_a, key_b", list(itertools.combinations(PATH_KEYS, 2)))
def test_every_pair_of_path_keys_is_rejected_when_they_name_the_same_tree(
    tmp_path, key_a, key_b
):
    # Built from the key set rather than by restating the pairs: every
    # unordered pair of path keys is pointed at one shared directory, and
    # every remaining path key at a tree of its own, so the only thing wrong
    # with the layout is the pair under test.
    lines = []
    for line in SAMPLE.splitlines():
        key = line.split("=", 1)[0]
        if key in (key_a, key_b):
            lines.append(f'{key}="shared-tree"')
        elif key in PATH_KEYS:
            lines.append(f'{key}="unrelated-{key.lower()}"')
        else:
            lines.append(line)
    conf_text = "\n".join(lines) + "\n"

    with pytest.raises(RoomConfError) as excinfo:
        load_room_conf(write(tmp_path, conf_text))
    message = str(excinfo.value)
    assert key_a in message and key_b in message, message


# ---------------------------------------------------------------------------
# Review finding E — FINDING_PREFIXES is interpolated directly into
# synthvdr.qa.leakage.finding_id_pattern's regex as `\b(?:{prefixes})-\d+\b`.
# An empty value, an empty segment (a leading, trailing, or doubled '|'), or
# a segment that is not a plausible prefix token collapses that alternation
# towards `-\d+`, which then matches ordinary hyphenated text ("page 12-15",
# "2020-2021") as if it were a finding ID — reachable by an authoring typo
# in room.conf, not just in theory, so it is checked here at load time.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    ["", "|", "CORP|", "|ENV", "CORP||ENV", "corp", "CORP-1", "CO RP", "1CORP"],
    ids=[
        "empty",
        "bare-pipe",
        "trailing-pipe",
        "leading-pipe",
        "doubled-pipe",
        "lowercase",
        "hyphenated",
        "internal-space",
        "leading-digit",
    ],
)
def test_finding_prefixes_rejects_an_implausible_value(tmp_path, bad_value):
    conf_text = SAMPLE.replace('FINDING_PREFIXES="CORP|ENV|FIN"', f'FINDING_PREFIXES="{bad_value}"')
    assert conf_text != SAMPLE
    with pytest.raises(RoomConfError, match="FINDING_PREFIXES"):
        load_room_conf(write(tmp_path, conf_text))


def test_finding_prefixes_accepts_a_single_token(tmp_path):
    conf_text = SAMPLE.replace('FINDING_PREFIXES="CORP|ENV|FIN"', 'FINDING_PREFIXES="CORP"')
    conf = load_room_conf(write(tmp_path, conf_text))
    assert conf.get("FINDING_PREFIXES") == "CORP"


# ---------------------------------------------------------------------------
# ROOM_ROLE — the exemplar/eval declaration the classifier work depends on.
# Optional at load (rooms predate it, and the loader runs on part-built
# rooms all day), never silently wrong when present, and made mandatory at
# QA time by gate 18 rather than here.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["exemplar", "eval"])
def test_room_role_accepts_the_two_legitimate_values(tmp_path, role):
    conf = load_room_conf(write(tmp_path, SAMPLE + f'ROOM_ROLE="{role}"\n'))
    assert conf.get("ROOM_ROLE") == role


def test_room_role_rejects_anything_else(tmp_path):
    with pytest.raises(RoomConfError, match="ROOM_ROLE") as excinfo:
        load_room_conf(write(tmp_path, SAMPLE + 'ROOM_ROLE="evaluation"\n'))
    message = str(excinfo.value)
    assert "evaluation" in message, "the rejected value must be named"
    assert "exemplar" in message and "eval" in message, (
        "the message must list the two legitimate roles, or the author is "
        "left guessing at the vocabulary"
    )


def test_room_role_is_not_required_at_load_time(tmp_path):
    # Deliberate: gate 18 is where a missing declaration blocks, at the
    # point a room is judged fit to leave — not in the loader that every
    # mid-build tool calls. SAMPLE has no ROOM_ROLE line, so this loading
    # cleanly IS the test.
    conf = load_room_conf(write(tmp_path))
    assert "ROOM_ROLE" not in conf.values
