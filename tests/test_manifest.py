"""Tests for `synthvdr.manifest` — the room's content hash, and the
`manifest` command that writes `_key/manifest.json`.

The hash construction used to live in the /vdr-package skill's markdown as
a code block the agent was told to "run exactly ... copy and adapt only the
total/paths, never the hash construction itself". That is an algorithm
under version control in prose: nothing tested it, nothing stopped it being
retyped slightly differently, and `check_provenance` — which compares the
result as a plain string — cannot tell a differently-constructed hash from
a different room. It lives here now, with the skill running a command.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from synthvdr.manifest import MANIFEST_NAME, compute_content_hash

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


def _tree(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return root


def test_content_hash_is_the_documented_construction(tmp_path):
    # sha256 over the SORTED `rel_path + "\0" + sha256(bytes)` of every file
    # in the tree, joined with newlines. Spelled out here independently of
    # the implementation, because this exact form is what every packaged
    # room's provenance already rests on: change it and every hash handed
    # out with a released room stops matching.
    root = _tree(tmp_path / "data-room", {
        "01_corporate/articles.md": "# Articles\n",
        "06_property/lease.md": "# Lease\n",
    })
    entries = sorted(
        f"{p.relative_to(root).as_posix()}\0{hashlib.sha256(p.read_bytes()).hexdigest()}"
        for p in root.rglob("*")
        if p.is_file()
    )
    expected = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    digest, count = compute_content_hash(root)
    assert digest == expected
    assert count == 2


def test_content_hash_covers_content_and_path(tmp_path):
    root = _tree(tmp_path / "data-room", {"01_corporate/articles.md": "# Articles\n"})
    original, _ = compute_content_hash(root)

    (root / "01_corporate" / "articles.md").write_text("# Articles\n\nAmended.\n")
    assert compute_content_hash(root)[0] != original, "content must move the hash"

    (root / "01_corporate" / "articles.md").write_text("# Articles\n")
    assert compute_content_hash(root)[0] == original
    (root / "01_corporate" / "articles.md").rename(root / "01_corporate" / "arts.md")
    assert compute_content_hash(root)[0] != original, "the path must move it too"


def test_the_manifest_command_writes_a_hash_that_verifies(tmp_path, capsys):
    # End to end, the way /vdr-package now runs it: write the manifest, hand
    # the hash to whoever produces the tool output, and score.
    from synthvdr.__main__ import main
    from synthvdr.classify_score import ClassificationOutput
    from synthvdr.score import check_provenance

    (tmp_path / "room.conf").write_text(CONF)
    _tree(tmp_path / "data-room", {"01_corporate/articles.md": "# Articles\n"})
    (tmp_path / "_key").mkdir()
    (tmp_path / "_key" / "findings.yaml").write_text(
        "room: Test Room\nfindings: []\n"
    )

    code = main(["manifest", "--room", str(tmp_path), "--built", "2026-09-02"])
    printed = capsys.readouterr().out
    assert code == 0, printed

    manifest = json.loads((tmp_path / "_key" / MANIFEST_NAME).read_text())
    assert manifest["room"] == "Test Room"
    assert manifest["documents"] == 1
    assert manifest["built"] == "2026-09-02"
    assert manifest["content_hash"] == compute_content_hash(tmp_path / "data-room")[0]
    assert manifest["content_hash"] in printed, (
        "the hash has to be handed to whoever produces the tool output, so "
        "the command must print it rather than only writing it"
    )

    output = ClassificationOutput(
        tool="acme/1.0", room_hash=manifest["content_hash"], records=[]
    )
    assert check_provenance(tmp_path, output).verified


def test_the_manifest_command_refuses_a_room_it_cannot_read(tmp_path, capsys):
    from synthvdr.__main__ import main

    code = main(["manifest", "--room", str(tmp_path)])
    assert code == 2
    assert "room.conf" in capsys.readouterr().err
