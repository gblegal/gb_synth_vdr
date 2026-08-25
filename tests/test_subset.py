"""Tests for the deterministic subset builder and its reconciliation (gate 11).

Beyond the base behaviour (selection, build, check), this file also covers
three things the implementation must get right but that are easy to get
wrong silently:

  - determinism that survives a fresh Python process, not just a second
    call in the same one — a same-process check cannot see reliance on
    PYTHONHASHSEED-salted `hash()` or on set/dict iteration order, because
    both are stable for the life of one process;
  - out_dir safety inside the room — out_dir is a build_subset PARAMETER,
    not a room.conf key, so load_room_conf's path validation never sees
    it, and build_subset deletes-and-rebuilds it with shutil.rmtree;
  - out_dir safety OUTSIDE the room — the same rmtree can just as easily
    destroy a caller's own, wholly unrelated directory. This is guarded by
    synthvdr.ownership's marker-file check, the same algorithm
    synthvdr.twin uses for the flagged tree (Task 7), reused rather than
    re-derived;
  - the flagged tree's marker file (synthvdr.twin.MARKER_NAME) must never
    leak into a subset, confirmed rather than assumed, because the subset
    is built from the blind tree and the marker has no blind counterpart —
    and the subset's OWN marker (SUBSET_MARKER_NAME) must never be counted
    as a subset document either.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from synthvdr.roomconf import load_room_conf
from synthvdr.schema import Finding, FindingSet
from synthvdr.subset import (
    SUBSET_MARKER_NAME,
    SUBSET_MARKER_TEXT,
    SubsetError,
    build_subset,
    check_subset,
    select_subset,
)
from synthvdr.twin import MARKER_NAME, build_flagged_tree

CONF = '''ROOM_CODENAME="Project Testbed"
INDEX_TOTAL=12
BLIND_TOTAL=12
FLAGGED_TOTAL=12
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="ENV|FIN"
EXPECTED_KDP_CARRIERS=2
SECTION_DIRS="01_corporate"
'''


def paths():
    return [f"01_corporate/1.1_constitutional/1.1.{n}_doc.md" for n in range(1, 13)]


@pytest.fixture
def room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF)
    for rel in paths():
        p = tmp_path / "data-room" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {rel}\n\nBody.\n")
    (tmp_path / "_key").mkdir(exist_ok=True)
    return tmp_path


def findings():
    return FindingSet(
        [
            Finding(
                id="ENV-1", title="a", severity="critical", workstream="environmental",
                multi_document=True, source=paths()[0], location="x", substance="s",
                corroboration=[paths()[1]],
            ),
            Finding(
                id="FIN-1", title="b", severity="high", workstream="financial",
                multi_document=False, source=paths()[2], location="y", substance="s",
            ),
        ],
        "Project Testbed",
    )


def conf_for(room):
    return load_room_conf(room / "room.conf")


def test_selection_includes_every_evidence_document(room):
    selected = select_subset(room, conf_for(room), findings(), total=6)
    for rel in paths()[:3]:
        assert rel in selected


def test_selection_is_deterministic(room):
    a = select_subset(room, conf_for(room), findings(), total=6)
    b = select_subset(room, conf_for(room), findings(), total=6)
    assert a == b


def test_selection_honours_the_total(room):
    assert len(select_subset(room, conf_for(room), findings(), total=6)) == 6


def test_total_below_evidence_count_still_keeps_every_evidence_document(room):
    selected = select_subset(room, conf_for(room), findings(), total=2)
    assert len(selected) == 3
    assert set(paths()[:3]) <= set(selected)


def test_build_writes_the_subset_and_reports_full_coverage(room):
    report = build_subset(room, conf_for(room), findings(), total=6, out_dir=room / "subset")
    assert report.complete
    assert report.findings_covered == report.findings_total == 2
    assert (room / "subset" / paths()[0]).is_file()
    assert (room / "_key" / "subset-manifest.csv").is_file()


def test_check_reconciles_without_writing(room):
    build_subset(room, conf_for(room), findings(), total=6, out_dir=room / "subset")
    before = sorted(p.name for p in (room / "subset").rglob("*"))
    report = check_subset(room, conf_for(room), findings())
    assert report.complete
    assert sorted(p.name for p in (room / "subset").rglob("*")) == before


def test_check_detects_a_missing_evidence_document(room):
    build_subset(room, conf_for(room), findings(), total=6, out_dir=room / "subset")
    (room / "subset" / paths()[1]).unlink()
    report = check_subset(room, conf_for(room), findings())
    assert not report.complete
    assert any("ENV-1" in e for e in report.errors)


def test_check_refuses_to_report_success_with_zero_findings_parsed(room):
    build_subset(room, conf_for(room), findings(), total=6, out_dir=room / "subset")
    report = check_subset(room, conf_for(room), FindingSet([], "Project Testbed"))
    assert not report.complete
    assert any("no findings" in e.lower() for e in report.errors)


# --- Determinism across a fresh process (PYTHONHASHSEED), not just a second
# call in the same one. ------------------------------------------------------


def _select_in_subprocess(room: Path, hash_seed: str) -> str:
    script = textwrap.dedent(
        f"""
        from pathlib import Path
        from synthvdr.roomconf import load_room_conf
        from synthvdr.schema import Finding, FindingSet
        from synthvdr.subset import select_subset

        room = Path({str(room)!r})
        conf = load_room_conf(room / "room.conf")
        paths = [f"01_corporate/1.1_constitutional/1.1.{{n}}_doc.md" for n in range(1, 13)]
        fs = FindingSet(
            [
                Finding(
                    id="ENV-1", title="a", severity="critical", workstream="environmental",
                    multi_document=True, source=paths[0], location="x", substance="s",
                    corroboration=[paths[1]],
                ),
                Finding(
                    id="FIN-1", title="b", severity="high", workstream="financial",
                    multi_document=False, source=paths[2], location="y", substance="s",
                ),
            ],
            "Project Testbed",
        )
        print(select_subset(room, conf, fs, total=6))
        """
    )
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True
    )
    return result.stdout.strip()


def test_selection_is_byte_identical_across_processes_with_different_hash_seeds(room):
    """A same-process determinism check cannot see reliance on Python's
    randomised string hash (builtin hash(), or set/dict iteration order
    over strings) instead of hashlib.sha256 — PYTHONHASHSEED is fixed for
    the life of one process, so two calls in it agree even when the
    underlying ordering is not truly stable. Running in two fresh
    processes with different seeds is the only way to see that.
    """
    first = _select_in_subprocess(room, "1")
    second = _select_in_subprocess(room, "4242")
    assert first == second


# --- out_dir safety: build_subset deletes-and-rebuilds out_dir, and out_dir
# is a parameter, not a validated room.conf key. -----------------------------


def test_build_refuses_an_out_dir_that_is_the_room_root(room):
    with pytest.raises(SubsetError):
        build_subset(room, conf_for(room), findings(), total=6, out_dir=room)


def test_build_refuses_an_out_dir_that_is_an_ancestor_of_the_room_root(room):
    with pytest.raises(SubsetError):
        build_subset(room, conf_for(room), findings(), total=6, out_dir=room.parent)


def test_build_refuses_an_out_dir_inside_the_blind_tree(room):
    with pytest.raises(SubsetError):
        build_subset(
            room, conf_for(room), findings(), total=6, out_dir=room / "data-room" / "nested"
        )


def test_build_refuses_an_out_dir_that_is_the_blind_tree_itself(room):
    with pytest.raises(SubsetError):
        build_subset(room, conf_for(room), findings(), total=6, out_dir=room / "data-room")


def test_build_refuses_an_out_dir_inside_the_key_root(room):
    with pytest.raises(SubsetError):
        build_subset(room, conf_for(room), findings(), total=6, out_dir=room / "_key" / "subset")


def test_build_accepts_an_out_dir_whose_name_merely_starts_with_a_trees_name(room):
    # "data-room-archive" is not "data-room" and does not sit inside it —
    # the guard must compare path PARTS (via _is_inside), not do a raw
    # string-prefix match that would false-positive on this.
    report = build_subset(
        room, conf_for(room), findings(), total=6, out_dir=room / "data-room-archive"
    )
    assert report.complete


def test_build_refuses_an_out_dir_matching_a_configured_tree_via_a_symlink(room, tmp_path):
    # A same-inode alias under a different spelling — the class _same_file
    # exists to catch — must be refused exactly like the literal path.
    alias = tmp_path / "alias-to-data-room"
    alias.symlink_to(room / "data-room", target_is_directory=True)
    with pytest.raises(SubsetError):
        build_subset(room, conf_for(room), findings(), total=6, out_dir=alias)


def test_build_accepts_an_out_dir_that_is_a_sibling_of_the_configured_trees(room):
    # The legitimate case: out_dir does not overlap the room root or any
    # configured tree at all.
    report = build_subset(room, conf_for(room), findings(), total=6, out_dir=room / "subset")
    assert report.complete


# --- The flagged tree's marker file must never leak into a subset. ----------


def test_subset_is_built_from_the_blind_tree_and_never_carries_the_flagged_marker(room):
    conf = conf_for(room)
    build_flagged_tree(room, conf, findings())
    assert (room / conf.get("FLAGGED_TREE") / MARKER_NAME).is_file()

    # total far exceeds the number of documents in the room, so every
    # candidate file gets pulled into filler — if select_subset ever walked
    # FLAGGED_TREE instead of BLIND_TREE, the marker would show up here.
    selected = select_subset(room, conf, findings(), total=1000)
    assert not any(Path(rel).name == MARKER_NAME for rel in selected)

    report = build_subset(room, conf, findings(), total=1000, out_dir=room / "subset")
    assert report.complete
    assert not any((room / "subset").rglob(MARKER_NAME))


# --- check_subset genuinely writes nothing, not just "looks unchanged". ----
#
# A chmod-to-read-only test was tried here first and dropped: in this
# execution environment, writes into a directory chmod'd to 0o500 silently
# succeeded anyway (an ACL or sandbox layer overrides the POSIX mode bits
# for the owner), so that test could pass even when check_subset DID write.
# Monkeypatching every write-capable call is deterministic regardless of
# the filesystem/sandbox underneath, and pinpoints exactly which call
# fired if the assertion ever trips.


def test_check_subset_writes_nothing(room, monkeypatch):
    build_subset(room, conf_for(room), findings(), total=6, out_dir=room / "subset")

    def _forbidden(name):
        def _raise(*args, **kwargs):
            raise AssertionError(f"check_subset must write nothing, but called {name}")

        return _raise

    for name in ("write_text", "write_bytes", "mkdir", "unlink", "rmdir", "touch", "symlink_to"):
        monkeypatch.setattr(Path, name, _forbidden(f"Path.{name}"), raising=True)
    monkeypatch.setattr(shutil, "copyfile", _forbidden("shutil.copyfile"), raising=True)
    monkeypatch.setattr(shutil, "copy", _forbidden("shutil.copy"), raising=True)
    monkeypatch.setattr(shutil, "copy2", _forbidden("shutil.copy2"), raising=True)
    monkeypatch.setattr(shutil, "rmtree", _forbidden("shutil.rmtree"), raising=True)
    monkeypatch.setattr(shutil, "move", _forbidden("shutil.move"), raising=True)

    report = check_subset(room, conf_for(room), findings())
    assert report.complete


# --- Ownership guard: build_subset must refuse to rmtree a non-empty
# directory it did not create, wherever that directory lives — including
# entirely outside the room. _assert_safe_out_dir (above) only knows about
# paths inside the room; a caller-supplied out_dir under, say, /tmp/mine is
# invisible to it. This reuses synthvdr.ownership — the same algorithm
# synthvdr.twin uses for the flagged tree (Task 7) — rather than a second,
# independently-written copy of "prove ownership before deleting". -------


def test_build_refuses_a_non_empty_foreign_directory_without_the_marker(room, tmp_path_factory):
    # A directory entirely outside the room, holding data build_subset had
    # no part in creating — exactly the shape of the coordinator's report:
    # out_dir pointed at a user's own folder, silently emptied by rmtree.
    foreign = tmp_path_factory.mktemp("foreign")
    victim = foreign / "precious.txt"
    victim.write_text("someone else's data\n")

    with pytest.raises(SubsetError, match="was not created by this tool"):
        build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)

    assert victim.read_text(encoding="utf-8") == "someone else's data\n"


def test_build_rebuilds_a_target_carrying_the_marker(room, tmp_path_factory):
    foreign = tmp_path_factory.mktemp("foreign")
    (foreign / SUBSET_MARKER_NAME).write_text(SUBSET_MARKER_TEXT, encoding="utf-8")
    stale = foreign / "stale.md"
    stale.write_text("leftover\n")

    report = build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)

    assert report.complete
    assert not stale.exists()
    assert (foreign / paths()[0]).is_file()


def test_build_proceeds_when_the_target_is_empty(room, tmp_path_factory):
    # An empty directory holds nothing to destroy, so it needs no marker —
    # this is what lets a caller pre-create out_dir themselves.
    foreign = tmp_path_factory.mktemp("foreign")

    report = build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)

    assert report.complete
    assert (foreign / paths()[0]).is_file()


def test_build_two_consecutive_builds_succeed(room, tmp_path_factory):
    foreign = tmp_path_factory.mktemp("foreign")

    first = build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)
    second = build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)

    assert first.complete
    assert second.complete


def test_build_writes_the_marker_naming_the_tool(room, tmp_path_factory):
    foreign = tmp_path_factory.mktemp("foreign")

    build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)

    marker = foreign / SUBSET_MARKER_NAME
    assert marker.read_text(encoding="utf-8") == SUBSET_MARKER_TEXT
    assert "synthvdr" in SUBSET_MARKER_TEXT
    assert "deleted and rebuilt" in SUBSET_MARKER_TEXT


def test_marker_is_not_counted_in_the_report_and_is_not_a_subset_document(room, tmp_path_factory):
    foreign = tmp_path_factory.mktemp("foreign")

    report = build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)

    # The marker genuinely exists on disk...
    assert (foreign / SUBSET_MARKER_NAME).is_file()
    # ...but adds nothing to any of the report's counts...
    assert report.total == 6
    assert report.evidence + report.filler == report.total
    # ...and check_subset's own recount agrees, whether or not the marker
    # is present in the directory it scans.
    recheck = check_subset(room, conf_for(room), findings(), out_dir=foreign)
    assert recheck.total == 6
    # ...and it was never treated as a selectable document in the first
    # place, evidence or filler.
    manifest = (room / "_key" / "subset-manifest.csv").read_text(encoding="utf-8")
    assert SUBSET_MARKER_NAME not in manifest


def test_marker_is_written_before_any_document_so_a_partial_failure_is_recoverable(
    room, monkeypatch, tmp_path_factory
):
    # Task 7's own reasoning, reapplied: if the marker went down AFTER
    # documents were copied, a build that died partway through would leave
    # a non-empty, unmarked directory — and the NEXT build would refuse it
    # as foreign, a permanent lockout rather than a retryable failure.
    foreign = tmp_path_factory.mktemp("foreign")
    real_copyfile = shutil.copyfile
    calls = {"n": 0}

    def _flaky_copyfile(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated failure partway through the build")
        return real_copyfile(src, dst)

    monkeypatch.setattr(shutil, "copyfile", _flaky_copyfile)
    with pytest.raises(OSError):
        build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)
    monkeypatch.undo()

    assert (foreign / SUBSET_MARKER_NAME).is_file()

    report = build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)
    assert report.complete


# --- Two further bypasses in the shared ownership guard, found by review
# against a real build_subset call and a real victim file, and fixed in
# synthvdr/ownership.py (both are pre-existing in Task 7's original
# algorithm, not introduced by this file's out_dir guard). Both are proven
# here the same way the coordinator reproduced them: an end-to-end
# build_subset call against a foreign directory holding a planted file,
# asserting that file still EXISTS afterwards — not merely that
# SubsetError was raised, since code that raises after deleting would
# still pass a raises-only test. -------------------------------------------


def test_build_refuses_a_directory_whose_marker_is_a_symlink(room, tmp_path_factory):
    # A symlink named exactly SUBSET_MARKER_NAME, pointing at an unrelated
    # regular file, must not satisfy the marker check — is_file() on a
    # symlink follows it and would otherwise read as "marker present".
    foreign = tmp_path_factory.mktemp("foreign")
    victim = foreign / "precious.txt"
    victim.write_text("someone else's data\n")
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "unrelated.txt"
    elsewhere.write_text("not a marker\n")
    (foreign / SUBSET_MARKER_NAME).symlink_to(elsewhere)

    with pytest.raises(SubsetError, match="was not created by this tool"):
        build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)

    assert victim.read_text(encoding="utf-8") == "someone else's data\n"


def test_build_refuses_a_directory_whose_marker_differs_only_in_case(room, tmp_path_factory):
    # A same-case-INsensitive-lookup match must not satisfy the marker
    # check either: entries must be compared by exact `==`, never via a
    # filesystem path lookup for `marker_name` (which a case-insensitive
    # filesystem, e.g. APFS by default, would resolve onto this entry).
    #
    # This assertion is written to be meaningful regardless of whether the
    # host filesystem happens to be case-insensitive: after the fix,
    # `entries` always carries the literal on-disk name, and the match is
    # always a plain Python string `==` — so a differently-cased entry is
    # never accepted as the marker on ANY filesystem. No runtime
    # case-sensitivity probe or skip is needed for the assertion to hold.
    foreign = tmp_path_factory.mktemp("foreign")
    victim = foreign / "precious.txt"
    victim.write_text("someone else's data\n")
    wrong_case = SUBSET_MARKER_NAME.upper()
    assert wrong_case != SUBSET_MARKER_NAME  # sanity: the marker name has letters to case-flip
    (foreign / wrong_case).write_text(SUBSET_MARKER_TEXT, encoding="utf-8")

    with pytest.raises(SubsetError, match="was not created by this tool"):
        build_subset(room, conf_for(room), findings(), total=6, out_dir=foreign)

    assert victim.read_text(encoding="utf-8") == "someone else's data\n"
