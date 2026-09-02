"""Tests for the corruption stage — `python3 -m synthvdr corrupt`.

A clean synthetic room teaches a classifier regularities no real room
has: tidy filenames, a seller who never misfiles, text with every
signature phrase intact. The corruption stage writes a deliberately
dirtied twin of the blind tree under `corrupted/`, with an answer key
whose paths follow the mess but whose truth does not — the whole point
is that the classifier's three signals degrade the way a real room
degrades them, while the ground truth stays exact.

Everything is deterministic by (room, seed, profile): per-file decisions
come from sha256 hashes, never from `random`, so the same invocation
produces byte-identical output and a scorecard can name the exact
corrupted room it was produced against.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from synthvdr.corrupt import (
    CORRUPTED_DIR,
    PROFILES,
    CorruptError,
    corrupt_room,
)
from synthvdr.roomconf import load_room_conf

CONF = """\
ROOM_CODENAME="Test Room"
INDEX_TOTAL=1
BLIND_TOTAL=1
FLAGGED_TOTAL=1
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="CORP"
SECTION_DIRS="01_corporate"
EXPECTED_KDP_CARRIERS=0
ROOM_ROLE="eval"
"""

DOCS = {
    "01_corporate/1.1.1_articles.md": "# Articles of Association\n\n"
    + "The company limited by shares.\n" * 30,
    "01_corporate/1.1.2_spa.md": "# Share Purchase Agreement\n\n"
    + "AGREEMENT for the sale and purchase of the entire issued share capital.\n" * 30,
    "01_corporate/1.1.3_cap-table.csv": "holder,shares\nAlice,100\nBob,50\n",
    "06_property/6.1.1_lease.md": "# Lease\n\nTHIS LEASE is made between the parties.\n" * 20,
}

KEY = [
    {"source_path": "01_corporate/1.1.1_articles.md",
     "document_type": "Articles of association",
     "primary_workstream": "corporate", "secondary_workstreams": []},
    {"source_path": "01_corporate/1.1.2_spa.md",
     "document_type": "Share purchase agreement",
     "primary_workstream": "corporate", "secondary_workstreams": []},
    {"source_path": "01_corporate/1.1.3_cap-table.csv",
     "document_type": "Cap table",
     "primary_workstream": "corporate", "secondary_workstreams": []},
    {"source_path": "06_property/6.1.1_lease.md",
     "document_type": "Lease",
     "primary_workstream": "real-estate", "secondary_workstreams": []},
]


def _room(tmp_path: Path):
    (tmp_path / "room.conf").write_text(CONF)
    for rel, body in DOCS.items():
        doc = tmp_path / "data-room" / rel
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(body)
    key_root = tmp_path / "_key"
    key_root.mkdir(exist_ok=True)
    (key_root / "answer-key.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in KEY)
    )
    return tmp_path, load_room_conf(tmp_path / "room.conf")


def _tree_digest(root: Path) -> str:
    pieces = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            pieces.append(path.relative_to(root).as_posix())
            pieces.append(hashlib.sha256(path.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(pieces).encode()).hexdigest()


def _corrupted_docs(room: Path):
    tree = room / CORRUPTED_DIR / "data-room"
    return sorted(
        p.relative_to(tree).as_posix()
        for p in tree.rglob("*")
        if p.is_file() and p.suffix in (".md", ".csv")
    )


def test_every_document_survives_exactly_once(tmp_path):
    room, conf = _room(tmp_path)
    report = corrupt_room(room, conf, seed=1, profile=PROFILES["heavy"])
    assert report.documents == 4
    assert len(_corrupted_docs(room)) == 4, (
        "corruption dirties documents; it never loses or duplicates one"
    )


def test_the_key_follows_the_mess_but_the_truth_does_not(tmp_path):
    room, conf = _room(tmp_path)
    corrupt_room(room, conf, seed=1, profile=PROFILES["heavy"])
    key_rows = [
        json.loads(line)
        for line in (room / CORRUPTED_DIR / "answer-key.jsonl").read_text().splitlines()
    ]
    assert sorted(r["source_path"] for r in key_rows) == _corrupted_docs(room), (
        "the corrupted key must cover exactly the corrupted tree"
    )
    truths = {(r["document_type"], r["primary_workstream"]) for r in key_rows}
    assert truths == {
        ("Articles of association", "corporate"),
        ("Share purchase agreement", "corporate"),
        ("Cap table", "corporate"),
        ("Lease", "real-estate"),
    }, "renaming and misfiling must never change what a document IS"


def test_same_seed_same_bytes(tmp_path):
    room, conf = _room(tmp_path)
    corrupt_room(room, conf, seed=7, profile=PROFILES["heavy"])
    first = _tree_digest(room / CORRUPTED_DIR)
    corrupt_room(room, conf, seed=7, profile=PROFILES["heavy"])
    assert _tree_digest(room / CORRUPTED_DIR) == first, (
        "same room, seed and profile must be byte-identical — a scorecard "
        "has to be able to name the exact corrupted room it ran against"
    )


def test_a_different_seed_produces_a_different_room(tmp_path):
    room, conf = _room(tmp_path)
    corrupt_room(room, conf, seed=1, profile=PROFILES["heavy"])
    first = _tree_digest(room / CORRUPTED_DIR)
    corrupt_room(room, conf, seed=2, profile=PROFILES["heavy"])
    assert _tree_digest(room / CORRUPTED_DIR) != first


def test_extensions_survive_renaming(tmp_path):
    room, conf = _room(tmp_path)
    corrupt_room(room, conf, seed=3, profile=PROFILES["heavy"])
    originals = sorted(Path(p).suffix for p in DOCS)
    corrupted = sorted(Path(p).suffix for p in _corrupted_docs(room))
    assert corrupted == originals, (
        "a renamed file keeps its extension, or ingest stops reading it "
        "for the wrong reason"
    )


def test_csv_content_is_never_noised(tmp_path):
    room, conf = _room(tmp_path)
    corrupt_room(room, conf, seed=1, profile=PROFILES["heavy"])
    log = [
        json.loads(line)
        for line in (room / CORRUPTED_DIR / "log.jsonl").read_text().splitlines()
    ]
    row = next(r for r in log if r["original"].endswith(".csv"))
    tree = room / CORRUPTED_DIR / "data-room"
    assert (tree / row["corrupted"]).read_text() == DOCS["01_corporate/1.1.3_cap-table.csv"], (
        "structured data with broken cells is unusable, not realistic — "
        "a csv may be renamed or misfiled but its content stays intact"
    )


def test_the_log_accounts_for_every_document(tmp_path):
    room, conf = _room(tmp_path)
    corrupt_room(room, conf, seed=1, profile=PROFILES["heavy"])
    log = [
        json.loads(line)
        for line in (room / CORRUPTED_DIR / "log.jsonl").read_text().splitlines()
    ]
    assert sorted(r["original"] for r in log) == sorted(DOCS), (
        "the log is the map from clean to corrupted — every document has a "
        "row, applied corruptions listed, untouched documents saying so"
    )
    for row in log:
        assert isinstance(row["applied"], list)


def test_heavy_corruption_actually_corrupts(tmp_path):
    room, conf = _room(tmp_path)
    report = corrupt_room(room, conf, seed=1, profile=PROFILES["heavy"])
    assert report.renamed + report.misfiled + report.noised + report.truncated > 0, (
        "a corruption pass that changed nothing is a clean room with a "
        "dirty name"
    )


def test_a_foreign_corrupted_dir_is_refused(tmp_path):
    room, conf = _room(tmp_path)
    target = room / CORRUPTED_DIR
    target.mkdir()
    (target / "precious.txt").write_text("not ours")
    with pytest.raises(CorruptError, match="precious|marker|refus"):
        corrupt_room(room, conf, seed=1, profile=PROFILES["light"])


def test_missing_answer_key_names_the_command(tmp_path):
    room, conf = _room(tmp_path)
    (room / "_key" / "answer-key.jsonl").unlink()
    with pytest.raises(CorruptError, match="answerkey"):
        corrupt_room(room, conf, seed=1, profile=PROFILES["light"])


def test_cli_corrupts_and_reports(tmp_path, capsys):
    from synthvdr.__main__ import main

    room, conf = _room(tmp_path)
    code = main(["corrupt", "--room", str(room), "--seed", "5", "--profile", "heavy"])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "corrupted" in printed
    assert (room / CORRUPTED_DIR / "answer-key.jsonl").is_file()


def test_cli_rejects_an_unknown_profile(tmp_path, capsys):
    from synthvdr.__main__ import main

    room, conf = _room(tmp_path)
    code = main(["corrupt", "--room", str(room), "--profile", "cursed"])
    assert code == 2
    assert "cursed" in capsys.readouterr().err


def test_the_corrupted_twin_scores_through_the_key_override(tmp_path, capsys):
    # The full loop: corrupt, then score a run against the corrupted twin
    # via --key. A perfect classifier (its output read straight off the
    # corrupted key) must score 100% — proving the rewritten key and the
    # corrupted tree agree, and that the scorer can be pointed at them.
    from synthvdr.__main__ import main

    room, conf = _room(tmp_path)
    corrupt_room(room, conf, seed=9, profile=PROFILES["heavy"])
    key_rows = [
        json.loads(line)
        for line in (room / CORRUPTED_DIR / "answer-key.jsonl").read_text().splitlines()
    ]
    perfect = tmp_path / "perfect.json"
    perfect.write_text(json.dumps({
        "tool": "oracle/1.0",
        "classifications": key_rows,
    }))
    code = main([
        "score-classification", str(perfect),
        "--room", str(room),
        "--key", str(room / CORRUPTED_DIR / "answer-key.jsonl"),
    ])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "100%" in printed, (
        "a classifier that reads the corrupted key back must score "
        "perfectly against it — anything less means the twin and its key "
        "disagree"
    )


# --- --out: where the twin goes. The default `corrupted/` is fixed, so a
# second profile silently replaced the first twin (ll_vdr_05 renamed the
# directory between runs by hand). A caller-supplied out_dir is a
# delete-and-rebuild target that room.conf never validated, so it gets
# the same safety rule as subset's out_dir: never a configured tree, never
# the room root, never an ancestor of either.


def test_out_dir_places_the_twin_where_asked(tmp_path):
    room, conf = _room(tmp_path)
    out = room / "corrupted-light"
    report = corrupt_room(room, conf, seed=1, profile=PROFILES["light"], out_dir=out)
    assert report.out == out
    assert (out / "answer-key.jsonl").is_file()
    assert (out / "log.jsonl").is_file()
    assert not (room / CORRUPTED_DIR).exists(), (
        "asking for --out must not also write the default directory"
    )


def test_two_profiles_coexist_under_distinct_out_dirs(tmp_path):
    room, conf = _room(tmp_path)
    light = room / "corrupted-light"
    heavy = room / "corrupted-heavy"
    corrupt_room(room, conf, seed=1, profile=PROFILES["light"], out_dir=light)
    corrupt_room(room, conf, seed=1, profile=PROFILES["heavy"], out_dir=heavy)
    assert (light / "answer-key.jsonl").is_file() and (heavy / "answer-key.jsonl").is_file(), (
        "the whole point of --out: a second profile must not clobber the first twin"
    )
    assert _tree_digest(light) != _tree_digest(heavy)


@pytest.mark.parametrize(
    "unsafe",
    [
        lambda room: room,                          # the room root itself
        lambda room: room.parent,                   # an ancestor of the room
        lambda room: room / "data-room",            # the blind tree
        lambda room: room / "data-room" / "dirty",  # inside the blind tree
        lambda room: room / "_key" / "corrupted",   # inside the key root
    ],
)
def test_out_dir_refuses_every_configured_tree_and_the_room_root(tmp_path, unsafe):
    room, conf = _room(tmp_path)
    with pytest.raises(CorruptError, match="refusing"):
        corrupt_room(room, conf, seed=1, profile=PROFILES["light"], out_dir=unsafe(room))
    assert (room / "data-room" / "01_corporate" / "1.1.1_articles.md").is_file(), (
        "a refused out_dir must be refused BEFORE anything is deleted"
    )


def test_cli_out_flag_names_the_directory_it_wrote(tmp_path, capsys):
    from synthvdr.__main__ import main

    room, conf = _room(tmp_path)
    out = room / "corrupted-heavy"
    code = main([
        "corrupt", "--room", str(room), "--profile", "heavy", "--out", str(out),
    ])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert str(out) in printed
    assert (out / "answer-key.jsonl").is_file()
    assert not (room / CORRUPTED_DIR).exists()
