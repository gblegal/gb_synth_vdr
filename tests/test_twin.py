import re
import unicodedata

import pytest

from synthvdr.roomconf import PATH_KEYS, ROOM_ROOT_LABEL, RoomConf, load_room_conf
from synthvdr.schema import Finding, FindingSet
from synthvdr.twin import (
    MARKER_NAME,
    MARKER_TEXT,
    SUBJECT_KEY,
    TwinError,
    _is_inside,
    _overlaps,
    _same_file,
    annotation_block,
    assert_safe_delete_target,
    assert_target_is_ours,
    build_flagged_tree,
    derive_twin,
    is_valid_twin,
    split_twin,
)

FLAG = "Key diligence points"
BLIND = "# Phase 2 report\n\nRemediation is estimated at GBP 18.6m.\n"

CONF_TEMPLATE = '''ROOM_CODENAME="Project Testbed"
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


def finding(fid="ENV-1", source=None, corroboration=None, multi_document=True):
    return Finding(
        id=fid,
        title="Site contamination under-provisioned",
        severity="critical",
        workstream="environmental",
        multi_document=multi_document,
        source=source or "11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md",
        location="Table 4",
        substance="Estimate far above the provision.",
        corroboration=(
            corroboration
            if corroboration is not None
            else ["02_financial/2.4_provisions/2.4.1_environmental-provision.md"]
        ),
        cross_links=["FIN-3"],
    )


def make_room(tmp_path, conf_text=CONF_TEMPLATE):
    """A room directory with a room.conf and an empty blind tree, ready for
    build_flagged_tree tests to populate with files under data-room/.
    """
    room = tmp_path
    conf_path = room / "room.conf"
    conf_path.write_text(conf_text)
    (room / "data-room").mkdir()
    return room, load_room_conf(conf_path)


def write_blind(room, rel, content):
    path = room / "data-room" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


def test_benign_twin_is_byte_identical():
    assert derive_twin(BLIND, None) == BLIND


def test_carrier_twin_is_blind_plus_trailing_block():
    block = annotation_block([finding()], FLAG)
    twin = derive_twin(BLIND, block)
    assert twin.startswith(BLIND)
    assert twin.endswith(block)


def test_block_names_the_flag_string_and_the_finding():
    block = annotation_block([finding()], FLAG)
    assert f"## {FLAG}" in block
    assert "ENV-1" in block


def test_split_twin_recovers_body_and_block():
    block = annotation_block([finding()], FLAG)
    body, recovered = split_twin(derive_twin(BLIND, block), FLAG)
    assert body == BLIND
    assert recovered == block


def test_split_twin_of_a_benign_file_returns_no_block():
    body, block = split_twin(BLIND, FLAG)
    assert body == BLIND and block is None


def test_is_valid_twin_accepts_identical_and_appended():
    block = annotation_block([finding()], FLAG)
    assert is_valid_twin(BLIND, BLIND, FLAG)
    assert is_valid_twin(BLIND, derive_twin(BLIND, block), FLAG)


def test_is_valid_twin_rejects_a_modified_body():
    tampered = BLIND.replace("18.6m", "4.2m")
    assert not is_valid_twin(BLIND, tampered, FLAG)


def test_is_valid_twin_rejects_a_modified_body_even_with_a_well_formed_block():
    # A bare tampered body (no block at all) is rejected purely because
    # split_twin finds no marker. The sharper case is a carrier whose body
    # was altered but which still carries a plausible-looking block — that
    # must be caught by the body-equality check itself, not by the absence
    # of a block.
    tampered = BLIND.replace("18.6m", "4.2m")
    block = annotation_block([finding()], FLAG)
    assert not is_valid_twin(BLIND, derive_twin(tampered, block), FLAG)


def test_derive_twin_is_idempotent():
    block = annotation_block([finding()], FLAG)
    once = derive_twin(BLIND, block)
    assert derive_twin(split_twin(once, FLAG)[0], block) == once


def test_split_twin_finds_the_last_heading_occurrence_when_the_body_has_a_decoy():
    # Blind-first discipline forbids a blind document from containing the flag
    # heading, but split_twin must still behave sanely if it ever happened —
    # it must recover the *appended* block, not an earlier look-alike in the
    # body. rfind (not find) is what makes that true.
    decoy_body = f"# Report\n\n## {FLAG}\n\nAn unrelated paragraph that happens to echo the heading.\n"
    block = annotation_block([finding()], FLAG)
    twin = derive_twin(decoy_body, block)
    body, recovered = split_twin(twin, FLAG)
    assert body == decoy_body
    assert recovered == block


# ---------------------------------------------------------------------------
# build_flagged_tree: the filesystem writer. Non-markdown files must never be
# annotated, a document evidencing two findings gets exactly one combined
# block, and the delete-and-rebuild step must only ever touch the configured
# FLAGGED_TREE — never its parent or the blind tree.
# ---------------------------------------------------------------------------


def test_non_markdown_carrier_is_copied_byte_for_byte_and_never_annotated(tmp_path):
    room, conf = make_room(tmp_path)
    csv_bytes = b"period,amount\nQ1,18600000\n"
    write_blind(room, "02_financial/2.4_provisions/register.csv", csv_bytes)
    f = finding(source="02_financial/2.4_provisions/register.csv", corroboration=[], multi_document=False)
    findings = FindingSet(findings=[f], room="Project Testbed")

    report = build_flagged_tree(room, conf, findings)

    flagged_csv = room / "_key/flagged/02_financial/2.4_provisions/register.csv"
    assert flagged_csv.read_bytes() == csv_bytes
    assert report.written == 1
    assert report.carriers == 0
    assert report.identical == 1


def test_document_evidencing_two_findings_gets_one_combined_block(tmp_path):
    room, conf = make_room(tmp_path)
    body = "# Phase 2 report\n\nRemediation is estimated at GBP 18.6m.\n"
    write_blind(room, "11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md", body)
    env = finding(fid="ENV-1", source="11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md", corroboration=[], multi_document=False)
    fin = finding(
        fid="FIN-3",
        source="02_financial/2.4_provisions/2.4.1_environmental-provision.md",
        corroboration=["11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md"],
    )
    write_blind(room, "02_financial/2.4_provisions/2.4.1_environmental-provision.md", "# Provisions note\n")
    findings = FindingSet(findings=[env, fin], room="Project Testbed")

    report = build_flagged_tree(room, conf, findings)

    flagged = (room / "_key/flagged/11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md").read_text(
        encoding="utf-8"
    )
    # phase-2.md is evidence for both findings: it must carry exactly one
    # block naming both, not one block per finding.
    assert flagged.count(f"## {FLAG}") == 1
    assert "ENV-1" in flagged and "FIN-3" in flagged
    assert flagged == derive_twin(body, annotation_block([env, fin], FLAG))
    # environmental-provision.md is FIN-3's own source — a second, separate
    # carrier document, annotated with FIN-3 alone.
    provision_flagged = (
        room / "_key/flagged/02_financial/2.4_provisions/2.4.1_environmental-provision.md"
    ).read_text(encoding="utf-8")
    assert "FIN-3" in provision_flagged and "ENV-1" not in provision_flagged
    assert report.written == 2
    assert report.carriers == 2
    assert report.identical == 0


def test_build_flagged_tree_only_touches_the_configured_flagged_root(tmp_path):
    room, conf = make_room(tmp_path)
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")

    # A stale leftover under the flagged tree must be removed on rebuild...
    stale = room / "_key/flagged/stale.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("leftover from a previous run\n")
    # ...and "a previous run" means synthvdr's own, which leaves the marker
    # that licenses the next build to clear the tree. Without it this is
    # someone else's directory and the build must refuse instead (see
    # test_build_flagged_tree_refuses_a_non_empty_target_it_did_not_create).
    (room / "_key/flagged" / MARKER_NAME).write_text(MARKER_TEXT)
    # ...but a sibling file under KEY_ROOT, outside FLAGGED_TREE, must survive.
    sibling = room / "_key/manifest.txt"
    sibling.write_text("keep-me\n")

    findings = FindingSet(findings=[], room="Project Testbed")
    build_flagged_tree(room, conf, findings)

    assert not stale.exists()
    assert sibling.read_text(encoding="utf-8") == "keep-me\n"
    assert (room / "data-room/01_corporate/1.1_articles/1.1.1_articles.md").read_text(
        encoding="utf-8"
    ) == "# Articles\n"


def test_build_flagged_tree_refuses_a_flagged_root_that_resolves_outside_the_room(tmp_path):
    # load_room_conf is the primary defence and already rejects an unsafe
    # FLAGGED_TREE value (see tests/test_roomconf.py), so simulate the one
    # way past it: a RoomConf built by hand rather than via load_room_conf.
    room, conf = make_room(tmp_path)
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")

    # A real directory a naive rmtree would wipe out if the guard didn't
    # fire. It lives under tmp_path — never outside the test sandbox — but
    # is a sibling of `room`, i.e. genuinely outside the room the function
    # must stay confined to.
    victim = tmp_path / "outside" / "escape"
    victim.mkdir(parents=True)
    (victim / "sentinel.txt").write_text("must survive\n")

    escaping_conf = RoomConf(
        values={**conf.values, "FLAGGED_TREE": "../outside/escape"},
        path=conf.path,
    )
    findings = FindingSet(findings=[], room="Project Testbed")

    with pytest.raises(TwinError, match="FLAGGED_TREE"):
        build_flagged_tree(room, escaping_conf, findings)

    assert (victim / "sentinel.txt").read_text(encoding="utf-8") == "must survive\n"


def test_build_flagged_tree_refuses_a_flagged_root_that_is_the_room_root(tmp_path):
    # "." and "./" pass load_room_conf's non-empty / non-absolute / no-'..'
    # checks and pass simple containment (the room root is trivially
    # "inside" the room) — only the dedicated room-root check catches them.
    # Simulate a hand-built RoomConf, since load_room_conf itself now
    # rejects a FLAGGED_TREE that normalises to the room root.
    room, conf = make_room(tmp_path)
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")
    (room / "_key").mkdir()
    (room / "_key" / "findings.yaml").write_text("findings: []\n")

    escaping_conf = RoomConf(values={**conf.values, "FLAGGED_TREE": "."}, path=conf.path)
    findings = FindingSet(findings=[], room="Project Testbed")

    # Matched on the room-root wording specifically, not just "FLAGGED_TREE"
    # generically: BLIND_TREE is always nested under the room, so if the
    # dedicated room-root check were removed, the blind-tree-overlap check
    # below it would also fire (blind is trivially "inside" a flagged root
    # that IS the room) and this test would pass for the wrong reason.
    with pytest.raises(TwinError, match="room root itself"):
        build_flagged_tree(room, escaping_conf, findings)

    # The whole room — room.conf, the blind tree, and the key root — must
    # be exactly as it was: nothing must have been deleted.
    assert conf.path.exists()
    assert (room / "_key" / "findings.yaml").read_text(encoding="utf-8") == "findings: []\n"
    assert (room / "data-room/01_corporate/1.1_articles/1.1.1_articles.md").read_text(
        encoding="utf-8"
    ) == "# Articles\n"


def test_build_flagged_tree_refuses_a_flagged_root_that_is_the_blind_tree(tmp_path):
    # load_room_conf now rejects FLAGGED_TREE == BLIND_TREE too, so this
    # again requires a hand-built RoomConf to reach the backstop.
    room, conf = make_room(tmp_path)
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")

    escaping_conf = RoomConf(
        values={**conf.values, "FLAGGED_TREE": conf.get("BLIND_TREE")},
        path=conf.path,
    )
    findings = FindingSet(findings=[], room="Project Testbed")

    with pytest.raises(TwinError, match="FLAGGED_TREE"):
        build_flagged_tree(room, escaping_conf, findings)

    assert (room / "data-room/01_corporate/1.1_articles/1.1.1_articles.md").read_text(
        encoding="utf-8"
    ) == "# Articles\n"


def test_build_flagged_tree_raises_for_a_finding_with_a_nonexistent_evidence_path(tmp_path):
    # A mistyped path in findings.yaml must not be silently dropped — the
    # finding would never actually be planted in the corpus, and nothing
    # would say so. Same silent-failure shape as a stripped annotation
    # block, one stage further upstream.
    room, conf = make_room(tmp_path)
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")
    missing_path = "11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md"
    f = finding(source=missing_path, corroboration=[], multi_document=False)
    findings = FindingSet(findings=[f], room="Project Testbed")

    with pytest.raises(TwinError, match=re.escape(missing_path)):
        build_flagged_tree(room, conf, findings)


# ---------------------------------------------------------------------------
# Case aliasing: on a case-insensitive filesystem (this sandbox included —
# macOS/APFS is case-insensitive by default), 'data-room' and 'DATA-ROOM'
# are the same physical directory even though Path.resolve() does NOT
# normalise the case of either string, so a naive string-equality check
# after resolve() would still see two different paths. Two independent
# defences close this: the case-folded string comparison above (which,
# for this exact scenario, already catches it — see the mutation note in
# the report) and the os.path.samefile backstop, which compares device and
# inode and so also catches aliasing routes no string comparison could
# ever see (a hardlink, a bind mount, Unicode normalisation differences).
# ---------------------------------------------------------------------------


def test_build_flagged_tree_refuses_when_flagged_and_blind_are_the_same_directory_on_disk(tmp_path):
    room, conf = make_room(tmp_path)
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")

    # FLAGGED_TREE is a different *string* from BLIND_TREE, but on this
    # case-insensitive filesystem it names the exact same on-disk directory.
    # load_room_conf now rejects this at load time (case-folded comparison),
    # so reaching build_flagged_tree's own backstop requires a hand-built
    # RoomConf, as in the earlier bypass tests above.
    escaping_conf = RoomConf(
        values={**conf.values, "FLAGGED_TREE": "DATA-ROOM"},
        path=conf.path,
    )
    findings = FindingSet(findings=[], room="Project Testbed")

    with pytest.raises(TwinError):
        build_flagged_tree(room, escaping_conf, findings)

    assert (room / "data-room/01_corporate/1.1_articles/1.1.1_articles.md").read_text(
        encoding="utf-8"
    ) == "# Articles\n"


def test_build_flagged_tree_refuses_an_alias_the_string_check_cannot_see(tmp_path):
    # Isolates the samefile backstop's own necessity within build_flagged_tree,
    # separately from _overlaps: BLIND_TREE and FLAGGED_TREE spelled with
    # different Unicode normalisation forms of the same name ('café-room',
    # NFC vs NFD). casefold() does not perform Unicode normalisation, so
    # _overlaps genuinely does not fire for this pair — if the samefile
    # loop were removed, nothing else in build_flagged_tree would catch it.
    # Skipped on a filesystem that doesn't alias the two spellings.
    import unicodedata

    nfc = unicodedata.normalize("NFC", "café-room")
    nfd = unicodedata.normalize("NFD", "café-room")
    assert nfc != nfd, "test assumption broken: NFC and NFD must differ as plain strings"

    room = tmp_path
    conf_path = room / "room.conf"
    conf_path.write_text(CONF_TEMPLATE.replace('BLIND_TREE="data-room"', f'BLIND_TREE="{nfc}"'))
    blind_root = room / nfc
    blind_root.mkdir()
    if not (room / nfd).exists():
        pytest.skip("host filesystem does not alias Unicode NFC/NFD spellings")
    (blind_root / "articles.md").write_text("# Articles\n")

    conf = load_room_conf(conf_path)
    assert not _overlaps(room / nfd, blind_root), (
        "test assumption broken: the string-level check must not catch this "
        "on its own, or the test isn't isolating the samefile backstop"
    )
    escaping_conf = RoomConf(values={**conf.values, "FLAGGED_TREE": nfd}, path=conf.path)
    findings = FindingSet(findings=[], room="Project Testbed")

    with pytest.raises(TwinError):
        build_flagged_tree(room, escaping_conf, findings)

    assert (blind_root / "articles.md").read_text(encoding="utf-8") == "# Articles\n"


def test_same_file_detects_a_case_alias_on_this_filesystem(tmp_path):
    # A direct, isolated check on _same_file itself: True for a real
    # on-disk alias, regardless of which (if any) earlier string-level
    # check would also have caught this particular case.
    real = tmp_path / "data-room"
    real.mkdir()
    alias = tmp_path / "DATA-ROOM"
    assert str(alias.resolve()) != str(real.resolve()), (
        "test assumption broken: resolve() must not itself normalise case, "
        "or this isn't exercising the alias at all"
    )
    assert _same_file(alias, real)


def test_same_file_is_false_for_genuinely_different_directories(tmp_path):
    a = tmp_path / "one"
    b = tmp_path / "two"
    a.mkdir()
    b.mkdir()
    assert not _same_file(a, b)


def test_same_file_is_false_rather_than_raising_when_a_path_does_not_exist(tmp_path):
    real = tmp_path / "data-room"
    real.mkdir()
    missing = tmp_path / "does-not-exist"
    assert not _same_file(missing, real)
    assert not _same_file(real, missing)


def test_same_file_detects_a_unicode_normalisation_alias_if_the_filesystem_has_one(tmp_path):
    # A second, independent aliasing route from case-folding: some
    # filesystems (macOS's default APFS/HFS+, this sandbox included)
    # normalise Unicode differently on lookup, so a composed ('café', NFC)
    # and a decomposed ('café', NFD — e + combining acute accent) spelling
    # can be the same directory even though neither casefold() nor
    # Path.resolve() treat them as equal strings — proving _same_file adds
    # real, distinct coverage beyond the string-level checks, not just a
    # second way of catching the same case-aliasing exploit. Skipped on a
    # filesystem that doesn't alias the two spellings (most Linux
    # filesystems), where they are genuinely different directories and
    # there is nothing here to detect.
    import unicodedata

    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    assert nfc != nfd, "test assumption broken: NFC and NFD must differ as plain strings"
    real = tmp_path / nfc
    real.mkdir()
    alias = tmp_path / nfd
    if not alias.exists():
        pytest.skip("host filesystem does not alias Unicode NFC/NFD spellings")
    assert str(alias.resolve()) != str(real.resolve())
    assert _same_file(alias, real)


# ---------------------------------------------------------------------------
# _is_inside / _overlaps in isolation: the string-level comparison that sits
# alongside _same_file. Case-insensitive unconditionally (see
# _casefolded_parts), independent of whether _same_file would also catch a
# given scenario once the paths exist on disk.
# ---------------------------------------------------------------------------


def test_is_inside_is_case_insensitive(tmp_path):
    outer = tmp_path / "data-room"
    inner = tmp_path / "DATA-ROOM" / "sub"
    assert _is_inside(inner, outer)


def test_is_inside_true_for_equal_paths(tmp_path):
    a = tmp_path / "data-room"
    assert _is_inside(a, a)


def test_is_inside_false_for_unrelated_paths(tmp_path):
    a = tmp_path / "data-room"
    b = tmp_path / "flagged"
    assert not _is_inside(a, b)
    assert not _is_inside(b, a)


def test_overlaps_is_case_insensitive_in_either_direction(tmp_path):
    a = tmp_path / "data-room"
    b = tmp_path / "DATA-ROOM" / "sub"
    assert _overlaps(a, b)
    assert _overlaps(b, a)


def test_overlaps_false_for_two_genuinely_separate_trees(tmp_path):
    a = tmp_path / "data-room"
    b = tmp_path / "flagged"
    assert not _overlaps(a, b)


# ---------------------------------------------------------------------------
# The two live bypasses of the round-3 guard, each asserting the victim file
# is STILL THERE afterwards — not merely that something raised.
#
# Both got through because the round-3 guard reasoned about a hardcoded list
# of pairs: build_flagged_tree never read KEY_ROOT at all, and the samefile
# backstop enumerated exactly three pairs. Property 1 (a tree resolves to its
# literal declared path) and Property 2 (every pair of configured trees is
# checked, generically) replace that reasoning; these tests pin the two
# concrete exploits to it.
# ---------------------------------------------------------------------------


def test_build_flagged_tree_refuses_a_flagged_tree_behind_a_symlinked_ancestor(tmp_path):
    # BLIND_TREE="data-room", KEY_ROOT="_key", FLAGGED_TREE="attack/subdir"
    # with attack -> _key on disk. The string comparison sees 'attack/subdir'
    # and '_key' as unrelated trees; the resolved path lands under _key,
    # where the sanctioned FLAGGED_TREE-under-KEY_ROOT exception then
    # permits it. Only Property 1 catches this.
    room, conf = make_room(tmp_path)
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")
    (room / "_key" / "subdir").mkdir(parents=True)
    victim = room / "_key" / "subdir" / "precious.txt"
    victim.write_text("answer-key material\n")
    (room / "attack").symlink_to(room / "_key")

    hostile = RoomConf(values={**conf.values, "FLAGGED_TREE": "attack/subdir"}, path=conf.path)
    findings = FindingSet(findings=[], room="Project Testbed")

    with pytest.raises(TwinError, match="does not resolve to where it says"):
        build_flagged_tree(room, hostile, findings)

    assert victim.read_text(encoding="utf-8") == "answer-key material\n"


def test_build_flagged_tree_refuses_a_unicode_alias_between_key_root_and_flagged_tree(tmp_path):
    # KEY_ROOT in NFC and FLAGGED_TREE in NFD: different byte strings that
    # casefold() does not reconcile, the same directory on a normalising
    # filesystem. Deleting FLAGGED_TREE wipes KEY_ROOT. Only the device/inode
    # comparison, applied to the FLAGGED_TREE/KEY_ROOT pair the round-3 guard
    # never looked at, catches this.
    nfc = unicodedata.normalize("NFC", "café-key")
    nfd = unicodedata.normalize("NFD", "café-key")
    assert nfc != nfd, "test assumption broken: NFC and NFD must differ as plain strings"

    room = tmp_path
    conf_path = room / "room.conf"
    conf_path.write_text(CONF_TEMPLATE.replace('KEY_ROOT="_key"', f'KEY_ROOT="{nfc}"'))
    (room / "data-room").mkdir()
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")
    (room / nfc).mkdir()
    if not (room / nfd).exists():
        pytest.skip("host filesystem does not alias Unicode NFC/NFD spellings")
    victim = room / nfc / "findings.yaml"
    victim.write_text("findings: []\n")

    conf = load_room_conf(conf_path)
    assert not _overlaps(room / nfd, room / nfc), (
        "test assumption broken: the casefolded string check must not catch "
        "this on its own, or the test isn't isolating the samefile comparison"
    )
    hostile = RoomConf(values={**conf.values, "FLAGGED_TREE": nfd}, path=conf.path)
    findings = FindingSet(findings=[], room="Project Testbed")

    with pytest.raises(TwinError, match="are the same directory"):
        build_flagged_tree(room, hostile, findings)

    assert victim.read_text(encoding="utf-8") == "findings: []\n"


def test_build_flagged_tree_refuses_a_symlink_planted_after_the_config_was_loaded(tmp_path):
    # The TOCTOU case, and the reason Property 1 is re-evaluated at build
    # time rather than trusted from load_room_conf: this conf is entirely
    # legitimate when it loads, and the redirect appears afterwards.
    room = tmp_path
    conf_path = room / "room.conf"
    conf_path.write_text(CONF_TEMPLATE.replace('FLAGGED_TREE="_key/flagged"', 'FLAGGED_TREE="out/flagged"'))
    (room / "data-room").mkdir()
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")
    (room / "out").mkdir()

    conf = load_room_conf(conf_path)  # clean at load time

    (room / "out").rmdir()
    (room / "_key" / "flagged").mkdir(parents=True)
    victim = room / "_key" / "flagged" / "findings.yaml"
    victim.write_text("findings: []\n")
    (room / "out").symlink_to(room / "_key")

    findings = FindingSet(findings=[], room="Project Testbed")
    with pytest.raises(TwinError, match="does not resolve to where it says"):
        build_flagged_tree(room, conf, findings)

    assert victim.read_text(encoding="utf-8") == "findings: []\n"


# ---------------------------------------------------------------------------
# The rmtree precondition, in isolation. It iterates the resolved tree map
# rather than a fixed list of pairs, so a path key added to PATH_KEYS is
# covered here automatically — which is what the parametrisation asserts.
# ---------------------------------------------------------------------------


def _canonical_resolved(tmp_path):
    return {
        ROOM_ROOT_LABEL: tmp_path,
        "BLIND_TREE": tmp_path / "data-room",
        "FLAGGED_TREE": tmp_path / "_key" / "flagged",
        "KEY_ROOT": tmp_path / "_key",
    }


def test_delete_precondition_accepts_the_canonical_layout(tmp_path):
    resolved = _canonical_resolved(tmp_path)
    assert assert_safe_delete_target(resolved[SUBJECT_KEY], resolved) is None


@pytest.mark.parametrize(
    "label", [k for k in PATH_KEYS if k != SUBJECT_KEY] + [ROOM_ROOT_LABEL]
)
def test_delete_precondition_refuses_when_any_other_root_sits_inside_the_target(tmp_path, label):
    resolved = _canonical_resolved(tmp_path)
    subject = resolved[SUBJECT_KEY]
    hostile = {**resolved, label: subject / "victim"}
    with pytest.raises(TwinError, match=re.escape(label)):
        assert_safe_delete_target(subject, hostile)


@pytest.mark.parametrize(
    "label", [k for k in PATH_KEYS if k != SUBJECT_KEY] + [ROOM_ROOT_LABEL]
)
def test_delete_precondition_refuses_when_any_other_root_is_the_target(tmp_path, label):
    resolved = _canonical_resolved(tmp_path)
    subject = resolved[SUBJECT_KEY]
    hostile = {**resolved, label: subject}
    with pytest.raises(TwinError, match=re.escape(label)):
        assert_safe_delete_target(subject, hostile)


@pytest.mark.parametrize(
    "label", [k for k in PATH_KEYS if k != SUBJECT_KEY] + [ROOM_ROOT_LABEL]
)
def test_delete_precondition_refuses_a_same_inode_alias_of_the_target(tmp_path, label):
    # Isolates the device/inode clause from the path-prefix one: NFC and NFD
    # spellings of one name share an inode but are not prefixes of each
    # other, casefolded or otherwise, so _is_inside genuinely cannot see this.
    nfc = unicodedata.normalize("NFC", "café-flagged")
    nfd = unicodedata.normalize("NFD", "café-flagged")
    assert nfc != nfd, "test assumption broken: NFC and NFD must differ as plain strings"
    subject = tmp_path / nfc
    subject.mkdir()
    alias = tmp_path / nfd
    if not alias.exists():
        pytest.skip("host filesystem does not alias Unicode NFC/NFD spellings")

    resolved = {**_canonical_resolved(tmp_path), SUBJECT_KEY: subject, label: alias}
    assert not _is_inside(alias, subject), (
        "test assumption broken: the path-prefix clause must not catch this "
        "on its own, or the test isn't isolating the same-inode clause"
    )
    with pytest.raises(TwinError, match="same directory on disk"):
        assert_safe_delete_target(subject, resolved)


# ---------------------------------------------------------------------------
# "We only delete what we made."
#
# Properties 1 and 2 bind the delete to the *declared* path. Nothing in a path
# binds it to data this tool owns: FLAGGED_TREE may legitimately name any
# directory in the room — '_key/index-src', '_key/incoming', 'scratch', a
# mount point holding another volume — and its contents are deleted. Note
# that the sanctioned FLAGGED_TREE-under-KEY_ROOT exception has nothing to do
# with it: 'scratch' and 'docs-elsewhere' go the same way with KEY_ROOT
# untouched. The marker file is the positive rule that closes this.
# ---------------------------------------------------------------------------


def _conf_with_flagged(room, flagged):
    """A loaded RoomConf whose FLAGGED_TREE is `flagged`, with a blind tree
    holding one document."""
    conf_path = room / "room.conf"
    conf_path.write_text(CONF_TEMPLATE.replace('FLAGGED_TREE="_key/flagged"', f'FLAGGED_TREE="{flagged}"'))
    (room / "data-room").mkdir(exist_ok=True)
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")
    return load_room_conf(conf_path)


@pytest.mark.parametrize(
    "flagged",
    ["_key/index-src", "_key/incoming", "scratch", "docs-elsewhere"],
    ids=["under-key-root", "under-key-root-2", "plain-room-dir", "plain-room-dir-2"],
)
def test_build_flagged_tree_refuses_a_non_empty_target_it_did_not_create(tmp_path, flagged):
    room = tmp_path
    conf = _conf_with_flagged(room, flagged)
    target = room / flagged
    target.mkdir(parents=True)
    victim = target / "precious.txt"
    victim.write_text("someone else's data\n")

    with pytest.raises(TwinError, match="was not created by this tool"):
        build_flagged_tree(room, conf, FindingSet(findings=[], room="Project Testbed"))

    assert victim.read_text(encoding="utf-8") == "someone else's data\n"


def test_build_flagged_tree_refuses_a_target_whose_marker_is_a_symlink(tmp_path, tmp_path_factory):
    # A symlink named exactly MARKER_NAME, pointing at any unrelated
    # regular file, must not satisfy the marker check — is_file() on a
    # symlink follows it and would otherwise read as "marker present".
    # Found by review, reproduced end-to-end against a real
    # build_flagged_tree call with a planted victim file destroyed as a
    # result, before this fix (synthvdr/ownership.py).
    room = tmp_path
    conf = _conf_with_flagged(room, "scratch")
    target = room / "scratch"
    target.mkdir(parents=True)
    victim = target / "precious.txt"
    victim.write_text("someone else's data\n")
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "unrelated.txt"
    elsewhere.write_text("not a marker\n")
    (target / MARKER_NAME).symlink_to(elsewhere)

    with pytest.raises(TwinError, match="was not created by this tool"):
        build_flagged_tree(room, conf, FindingSet(findings=[], room="Project Testbed"))

    assert victim.read_text(encoding="utf-8") == "someone else's data\n"


def test_build_flagged_tree_refuses_a_target_whose_marker_differs_only_in_case(tmp_path):
    # Entries must be matched by exact `==` against the literal on-disk
    # name, never via a filesystem path lookup for MARKER_NAME (which a
    # case-insensitive filesystem, e.g. APFS by default, would resolve
    # onto a differently-cased entry). Written to be meaningful regardless
    # of the host filesystem's own case sensitivity: post-fix, a
    # differently-cased entry is never accepted as the marker on ANY
    # filesystem, so no runtime case-sensitivity probe or skip is needed.
    room = tmp_path
    conf = _conf_with_flagged(room, "scratch")
    target = room / "scratch"
    target.mkdir(parents=True)
    victim = target / "precious.txt"
    victim.write_text("someone else's data\n")
    wrong_case = MARKER_NAME.upper()
    assert wrong_case != MARKER_NAME  # sanity: the marker name has letters to case-flip
    (target / wrong_case).write_text(MARKER_TEXT)

    with pytest.raises(TwinError, match="was not created by this tool"):
        build_flagged_tree(room, conf, FindingSet(findings=[], room="Project Testbed"))

    assert victim.read_text(encoding="utf-8") == "someone else's data\n"


def test_build_flagged_tree_refuses_a_second_build_after_the_marker_is_deleted(tmp_path):
    # The marker is the licence, and it can be revoked by hand. Once it is
    # gone the tree is indistinguishable from someone else's directory, and
    # the build must stop rather than clear it.
    room = tmp_path
    conf = _conf_with_flagged(room, "_key/flagged")
    findings = FindingSet(findings=[], room="Project Testbed")
    build_flagged_tree(room, conf, findings)

    target = room / "_key/flagged"
    (target / MARKER_NAME).unlink()
    survivor = target / "01_corporate/1.1_articles/1.1.1_articles.md"
    assert survivor.exists()

    with pytest.raises(TwinError, match="was not created by this tool"):
        build_flagged_tree(room, conf, findings)

    assert survivor.read_text(encoding="utf-8") == "# Articles\n"


def test_build_flagged_tree_rebuilds_a_target_carrying_the_marker(tmp_path):
    room = tmp_path
    conf = _conf_with_flagged(room, "_key/flagged")
    target = room / "_key/flagged"
    target.mkdir(parents=True)
    (target / MARKER_NAME).write_text(MARKER_TEXT)
    stale = target / "stale.md"
    stale.write_text("leftover\n")

    build_flagged_tree(room, conf, FindingSet(findings=[], room="Project Testbed"))

    assert not stale.exists()
    assert (target / "01_corporate/1.1_articles/1.1.1_articles.md").exists()


def test_build_flagged_tree_proceeds_when_the_target_is_empty(tmp_path):
    # An empty directory holds nothing to destroy, so it needs no marker —
    # this is what lets a user pre-create the flagged root themselves.
    room = tmp_path
    conf = _conf_with_flagged(room, "_key/flagged")
    (room / "_key/flagged").mkdir(parents=True)

    build_flagged_tree(room, conf, FindingSet(findings=[], room="Project Testbed"))

    assert (room / "_key/flagged/01_corporate/1.1_articles/1.1.1_articles.md").exists()


def test_build_flagged_tree_writes_the_marker_naming_the_tool(tmp_path):
    room = tmp_path
    conf = _conf_with_flagged(room, "_key/flagged")
    build_flagged_tree(room, conf, FindingSet(findings=[], room="Project Testbed"))

    marker = room / "_key/flagged" / MARKER_NAME
    assert marker.read_text(encoding="utf-8") == MARKER_TEXT
    assert "synthvdr" in MARKER_TEXT
    assert "deleted and rebuilt" in MARKER_TEXT


def test_two_consecutive_builds_succeed_and_agree(tmp_path):
    # The marker the first build writes must license the second, not block
    # it — and must not leak into the tally or the tree's contents.
    room = tmp_path
    conf = _conf_with_flagged(room, "_key/flagged")
    findings = FindingSet(findings=[], room="Project Testbed")

    first = build_flagged_tree(room, conf, findings)
    listing_after_first = sorted(p.name for p in (room / "_key/flagged").rglob("*"))
    second = build_flagged_tree(room, conf, findings)

    assert first == second
    assert sorted(p.name for p in (room / "_key/flagged").rglob("*")) == listing_after_first
    assert first.written == 1


def test_marker_is_not_counted_in_the_tally(tmp_path):
    room = tmp_path
    conf = _conf_with_flagged(room, "_key/flagged")
    report = build_flagged_tree(room, conf, FindingSet(findings=[], room="Project Testbed"))
    # One blind document in, one document out — the marker is infrastructure,
    # not a twin, and must never inflate .written or .identical.
    assert (report.written, report.carriers, report.identical) == (1, 0, 1)


# ---------------------------------------------------------------------------
# Bare OSErrors escaping a module that otherwise raises TwinError. Neither
# case destroys anything; both are the wrong exception type reaching a caller.
# The symlink-loop cases are the residue Property 1 provably cannot reach: a
# loop resolves to itself, so there is no redirect for it to detect.
# ---------------------------------------------------------------------------


def test_build_flagged_tree_raises_twin_error_when_the_target_is_an_existing_file(tmp_path):
    room = tmp_path
    conf = _conf_with_flagged(room, "_key/flagged")
    (room / "_key").mkdir()
    (room / "_key/flagged").write_text("i am a file, not a directory\n")

    with pytest.raises(TwinError, match="names an existing file"):
        build_flagged_tree(room, conf, FindingSet(findings=[], room="Project Testbed"))

    assert (room / "_key/flagged").read_text(encoding="utf-8") == "i am a file, not a directory\n"


@pytest.mark.parametrize(
    "flagged", ["loop-a/inner", "loop-a"], ids=["loop-in-ancestor", "loop-is-target"]
)
def test_build_flagged_tree_raises_twin_error_for_a_symlink_loop(tmp_path, flagged):
    room = tmp_path
    conf = _conf_with_flagged(room, flagged)
    (room / "loop-a").symlink_to(room / "loop-b")
    (room / "loop-b").symlink_to(room / "loop-a")

    with pytest.raises(TwinError, match="could not prepare FLAGGED_TREE"):
        build_flagged_tree(room, conf, FindingSet(findings=[], room="Project Testbed"))


def test_target_ownership_check_accepts_a_missing_target(tmp_path):
    # A target that does not exist yet is the ordinary first-build case.
    assert assert_target_is_ours(tmp_path / "nothing-here") is None


def test_build_flagged_tree_raises_twin_error_when_the_target_cannot_be_read(tmp_path):
    # The third bare-OSError route, found by mutating the inspection's own
    # wrap: exists()/is_dir()/is_file() all swallow OSError and answer False,
    # but iterdir() does not, so an unreadable flagged tree raises
    # PermissionError straight out of the guard. Probed for capability rather
    # than guessed from the OS — running as root, or on a filesystem that
    # ignores modes, the directory stays readable and there is nothing here
    # to test.
    room = tmp_path
    conf = _conf_with_flagged(room, "_key/flagged")
    target = room / "_key/flagged"
    target.mkdir(parents=True)
    (target / "precious.txt").write_text("someone else's data\n")
    target.chmod(0o000)
    try:
        try:
            list(target.iterdir())
        except PermissionError:
            pass
        else:
            pytest.skip("this user can read a mode-000 directory; nothing to test")

        with pytest.raises(TwinError, match="could not be inspected"):
            build_flagged_tree(room, conf, FindingSet(findings=[], room="Project Testbed"))
    finally:
        target.chmod(0o755)

    assert (target / "precious.txt").read_text(encoding="utf-8") == "someone else's data\n"


def test_build_flagged_tree_reports_a_missing_path_key_as_a_twin_error(tmp_path):
    # A RoomConf built by hand can be missing a path key entirely. The guard
    # must refuse cleanly rather than crashing with a KeyError from inside
    # the resolver — an unhandled KeyError here would mean the delete is
    # reached or not depending on where the crash lands.
    room, conf = make_room(tmp_path)
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")
    values = {k: v for k, v in conf.values.items() if k != "KEY_ROOT"}
    incomplete = RoomConf(values=values, path=conf.path)

    with pytest.raises(TwinError, match="missing key KEY_ROOT"):
        build_flagged_tree(room, incomplete, FindingSet(findings=[], room="Project Testbed"))
