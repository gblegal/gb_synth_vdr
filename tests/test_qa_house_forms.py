import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.houseforms import families
from synthvdr.lengths import load_lengths
from synthvdr.qa.houseforms import gate_21_house_forms
from synthvdr.qa.runner import GateContext
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import Finding, FindingSet
from synthvdr.slots import SIZE_PRESETS, build_slot_manifest, write_anchors_csv

from .test_houseforms import HOUSE, filled

PACK = load_domain(DEFAULT_DOMAIN_ROOT)
LENGTHS = load_lengths(DEFAULT_DOMAIN_ROOT, PACK)
ALL_SECTIONS = " ".join(PACK.section_dirs())

CONF = f'''ROOM_CODENAME="Project Testbed"
INDEX_TOTAL=200
BLIND_TOTAL=200
FLAGGED_TOTAL=200
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="CORP|FIN|TAX|FING|COMM|IP|IT|PROP|EMPL|REG|ENV|INS|PEN|DATA|LIT|OPS|MGMT|TXN|ESG|JV"
EXPECTED_KDP_CARRIERS=0
SECTION_DIRS="{ALL_SECTIONS}"
'''

GOOD = filled().replace("within thirty days", "within sixty days").replace("six months'", "three months'")


@pytest.fixture
def m_room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF + 'DOC_LENGTH="long"\n')
    slots = build_slot_manifest(PACK, SIZE_PRESETS["M"])
    write_anchors_csv(slots, tmp_path / "_key" / "anchors.csv")
    fam = families(slots, LENGTHS)[0]
    (tmp_path / "data-room" / fam.house_form.rel_path).parent.mkdir(parents=True)
    (tmp_path / "data-room" / fam.house_form.rel_path).write_text(HOUSE)
    return tmp_path, fam


def ctx_for(room, findings=None):
    return GateContext(
        room=room,
        conf=load_room_conf(room / "room.conf"),
        findings=findings or FindingSet([], ""),
        distractors=[],
    )


def write(room, slot, text):
    (room / "data-room" / slot.rel_path).write_text(text)


def test_not_applicable_in_a_short_room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF)
    result = gate_21_house_forms(ctx_for(tmp_path))
    assert result.status == "PASS" and "not applicable" in result.detail


def test_passes_properly_derived_contracts(m_room):
    room, fam = m_room
    for slot in fam.derived:
        write(room, slot, GOOD)
    result = gate_21_house_forms(ctx_for(room))
    assert result.status == "PASS", result.detail


def test_fails_an_unedited_copy_by_slot_id(m_room):
    room, fam = m_room
    write(room, fam.derived[0], HOUSE)
    result = gate_21_house_forms(ctx_for(room))
    assert result.status == "FAIL"
    assert fam.derived[0].slot_id in result.detail and "blank" in result.detail


def test_fails_evidence_placed_on_a_house_form(m_room):
    room, fam = m_room
    finding = Finding(
        id="COMM-1", title="t", severity="high", workstream="commercial", multi_document=False,
        source=fam.house_form.rel_path, location="clause 1.3", substance="s",
    )
    result = gate_21_house_forms(ctx_for(room, FindingSet([finding], "")))
    assert result.status == "FAIL" and "evidence on a house form" in result.detail


def test_gate_21_fails_a_house_form_with_no_blanks(m_room):
    # Review Focus 3: without blanks, the blank check would pass every derived
    # contract vacuously.
    room, fam = m_room
    (room / "data-room" / fam.house_form.rel_path).write_text(filled())
    result = gate_21_house_forms(ctx_for(room))
    assert result.status == "FAIL" and "no bracketed blanks" in result.detail


def test_ignores_derived_slots_not_yet_written(m_room):
    room, _ = m_room
    assert gate_21_house_forms(ctx_for(room)).status == "PASS"


def test_gate_21_in_a_subset_room_without_families_is_not_applicable(tmp_path):
    # Review Focus 1: an XS room has no family of three.
    (tmp_path / "room.conf").write_text(CONF + 'DOC_LENGTH="long"\n')
    write_anchors_csv(build_slot_manifest(PACK, SIZE_PRESETS["XS"]), tmp_path / "_key" / "anchors.csv")
    result = gate_21_house_forms(ctx_for(tmp_path))
    assert result.status == "PASS" and "not applicable" in result.detail


def test_skips_without_anchors_in_a_long_room(tmp_path):
    (tmp_path / "room.conf").write_text(CONF + 'DOC_LENGTH="long"\n')
    assert gate_21_house_forms(ctx_for(tmp_path)).status == "SKIP"
