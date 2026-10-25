import shutil
from pathlib import Path

import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.index_build import render_index, write_index_sources
from synthvdr.qa.depth import floor_for
from synthvdr.qa.runner import GateContext
from synthvdr.roomconf import REQUIRED_KEYS, load_room_conf
from synthvdr.schema import FindingSet, load_distractors, load_findings
from synthvdr.slots import SIZE_PRESETS, build_slot_manifest, write_anchors_csv
from synthvdr.subset import build_subset
from synthvdr.twin import build_flagged_tree

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "xs-room"

# The one copy of a minimal, loadable room.conf that unit tests start from.
# It used to be written out in full in some twenty test files, so adding a
# required key meant editing every one of them; now it means adding one line
# here, and the assertion below fails collection until someone does.
#
# The defaults are deliberately the dullest values that load: one document
# per tree, one section, one prefix, no carriers. The tree layout and the two
# flag strings are the project's own (they match fixtures/xs-room), because
# tests write into `room / "data-room"` and spell "Key diligence points" by
# literal throughout (a test of the flag strings themselves still names them,
# as test_qa_leakage does). Every other value is a placeholder that no test
# may lean on: a test whose outcome turns on a count, a prefix, a section
# list or the codename passes that value to room_conf_text by name, even when
# it happens to equal the default, so the dependence is visible at the call
# site and a later change to a default here cannot quietly change what it
# proves.
ROOM_CONF_DEFAULTS = {
    "ROOM_CODENAME": "Project Testbed",
    "INDEX_TOTAL": 1,
    "BLIND_TOTAL": 1,
    "FLAGGED_TOTAL": 1,
    "BLIND_TREE": "data-room",
    "FLAGGED_TREE": "_key/flagged",
    "KEY_ROOT": "_key",
    "FLAG_STRING_1": "Key diligence points",
    "FLAG_STRING_2": "DD flag",
    "FINDING_PREFIXES": "CORP",
    "SECTION_DIRS": "01_corporate",
    "EXPECTED_KDP_CARRIERS": 0,
}
assert tuple(ROOM_CONF_DEFAULTS) == REQUIRED_KEYS, (
    "ROOM_CONF_DEFAULTS has drifted from synthvdr.roomconf.REQUIRED_KEYS — "
    "give the new key a default here, in REQUIRED_KEYS order"
)

# The optional keys a test may add. Listed rather than open-ended because a
# misspelt override (EXPECTED_KDP_CARRIER=2) would otherwise write a line the
# loader accepts and nothing reads, leaving the default standing under a call
# site that appears to set it: the silent default this helper exists to stop.
ROOM_CONF_OPTIONAL_KEYS = ("ROOM_ROLE", "SCAN_PROFILE", "DOC_LENGTH")


def room_conf_text(**overrides) -> str:
    """The canonical room.conf text, with `overrides` replacing defaults.

    An int is written bare and anything else double-quoted, which is how
    the hand-written literals spelled them. A value of None leaves the key
    out, so a test that sometimes declares an optional key can pass its
    parameter straight through (`DOC_LENGTH=doc_length`). Required keys keep
    REQUIRED_KEYS order; optional keys follow in the order given. An unknown
    key raises TypeError rather than being written, for the reason given at
    ROOM_CONF_OPTIONAL_KEYS.
    """
    unknown = sorted(set(overrides) - set(REQUIRED_KEYS) - set(ROOM_CONF_OPTIONAL_KEYS))
    if unknown:
        raise TypeError(
            f"room_conf_text: unknown room.conf key(s) {', '.join(unknown)} — "
            "a typo, or a new optional key to add to ROOM_CONF_OPTIONAL_KEYS"
        )
    values = {**ROOM_CONF_DEFAULTS, **overrides}
    return "".join(
        f"{key}={value}\n" if isinstance(value, int) else f'{key}="{value}"\n'
        for key, value in values.items()
        if value is not None
    )


def gate_ctx(room: Path, findings=None, distractors=(), strict=False) -> GateContext:
    """A GateContext over `room`, loading its room.conf from disk.

    Loaded rather than built in memory so every gate test also runs
    load_room_conf's validation over the conf it wrote. `findings` defaults
    to an empty FindingSet, for the many gates that never look at the
    answer key; a file whose gates do need one keeps its own default finding
    and passes it in.
    """
    return GateContext(
        room=room,
        conf=load_room_conf(room / "room.conf"),
        findings=findings if findings is not None else FindingSet([], ""),
        distractors=list(distractors),
        strict=strict,
    )


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


@pytest.fixture
def xs_room(build_xs_room, tmp_path):
    return build_xs_room(tmp_path / "room")
