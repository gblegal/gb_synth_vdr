import shutil
from pathlib import Path

import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.index_build import render_index, write_index_sources
from synthvdr.qa.depth import floor_for
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import load_distractors, load_findings
from synthvdr.slots import SIZE_PRESETS, build_slot_manifest, write_anchors_csv
from synthvdr.subset import build_subset
from synthvdr.twin import build_flagged_tree

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "xs-room"

BODY = (
    "This document forms part of the information made available in connection "
    "with the proposed transaction. It records the position as at the date "
    "stated above and should be read with the other documents in this section. "
)


def _prose(words: int) -> str:
    """Generate prose long enough to clear the slot's depth floor.

    NOTE: the XS preset allocates two slots per section on average, so tiers are
    mixed (roughly 50% tier A, 50% tier F). Always use floor_for() to determine
    the requirement for each slot's tier, not a fixed number.
    """
    filler = (BODY * 200).split()
    return " ".join(filler[:words])


def build_fixture_room(dest: Path) -> Path:
    """Copy `fixtures/xs-room` to `dest` and generate the full derived room:
    blind-tree prose above each slot's depth floor (via anchors.csv/floor_for),
    the generated index, the flagged tree and the subset.

    A plain module-level function, not a pytest fixture, so it can be driven
    from a bare subprocess with a chosen `PYTHONHASHSEED` — see
    `test_end_to_end.py`'s cross-process determinism test, which is the only
    way to observe a set/dict-ordering dependence: two calls inside the same
    pytest process always share one interpreter's hash seed, so a same-process
    comparison cannot see it.
    """
    shutil.copytree(FIXTURE, dest)
    conf = load_room_conf(dest / "room.conf")
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    slots = build_slot_manifest(pack, SIZE_PRESETS["XS"])

    write_anchors_csv(slots, dest / "_key" / "anchors.csv")
    write_index_sources(slots, pack, dest / "_key" / "index-src")
    (dest / "index.md").write_text(render_index(dest / "_key" / "index-src"))

    findings = load_findings(dest / "_key" / "findings.yaml")
    evidence = findings.all_evidence_paths()
    for slot in slots:
        target = dest / conf.get("BLIND_TREE") / slot.rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        heading = slot.slug.split("_", 1)[1].replace("-", " ").capitalize()
        extra = ""
        if slot.rel_path in evidence:
            finding = next(f for f in findings.findings if slot.rel_path in f.evidence_paths())
            extra = f"\n\n{finding.substance.strip()}\n"
        floor = floor_for(slot.slot_id, target.name, slot.tier, pack)
        target.write_text(f"# {heading}\n\n{_prose(floor + 60)}{extra}\n")

    distractors = load_distractors(dest / "_key" / "distractors.yaml")
    build_flagged_tree(dest, conf, findings, distractors)
    build_subset(dest, conf, findings, total=10, out_dir=dest / "subset")
    return dest


@pytest.fixture(scope="session")
def build_xs_room():
    return build_fixture_room


# The opening bytes of a real Finder .DS_Store: binary, and not valid UTF-8.
DS_STORE = b"\x00\x00\x00\x01Bud1\x00\x00\x10\x00\x00\x00\x08\x00\xff\xfe"


def _browse_in_finder(*trees: Path) -> None:
    """Leave a .DS_Store in each tree and every folder below it, as opening
    each one in Finder does. Each records its own folder, as Finder's do, so
    a blind folder and its flagged twin never hold the same bytes."""
    for tree in trees:
        for folder in [tree, *[p for p in tree.rglob("*") if p.is_dir()]]:
            (folder / ".DS_Store").write_bytes(DS_STORE + str(folder).encode())


@pytest.fixture(scope="session")
def browse_in_finder():
    return _browse_in_finder


@pytest.fixture
def xs_room(build_xs_room, tmp_path):
    return build_xs_room(tmp_path / "room")
