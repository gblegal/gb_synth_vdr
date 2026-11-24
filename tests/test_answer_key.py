"""Tests for the classification answer key — _key/labels.yaml in,
_key/answer-key.jsonl out.

The downstream classifier (gb-docclass) scores itself against rooms whose
generator recorded what every document is. This module's contract: the
labels file names a document type for every blind document, the domain
pack names the classifier's workstream for every section, and the builder
refuses to write a key that covers anything less than the whole room —
a partially-labelled room would grade the classifier against silence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synthvdr.answer_key import AnswerKeyError, build_answer_key, load_labels
from synthvdr.domain import DEFAULT_DOMAIN_ROOT, Archetype, DomainPack, Section, load_domain
from synthvdr.roomconf import load_room_conf
from tests.conftest import room_conf_text

CONF_LINES = room_conf_text(ROOM_CODENAME="Test Room")

LABELS = """\
labels:
  - path: "01_corporate/1.1.1_constitutional-01.md"
    document_type: "Articles of association"
"""


def _room(tmp_path: Path, labels: str = LABELS, docs=("01_corporate/1.1.1_constitutional-01.md",)):
    (tmp_path / "room.conf").write_text(CONF_LINES)
    for rel in docs:
        doc = tmp_path / "data-room" / rel
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("body\n")
    key_root = tmp_path / "_key"
    key_root.mkdir(exist_ok=True)
    if labels is not None:
        (key_root / "labels.yaml").write_text(labels)
    return tmp_path, load_room_conf(tmp_path / "room.conf")


def test_a_fully_labelled_room_produces_the_key(tmp_path):
    room, conf = _room(tmp_path)
    out = build_answer_key(room, conf, load_domain(DEFAULT_DOMAIN_ROOT))
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert records == [
        {
            "source_path": "01_corporate/1.1.1_constitutional-01.md",
            "document_type": "Articles of association",
            "primary_workstream": "corporate",
            "secondary_workstreams": [],
            "teams_as_of": "0.15.0",
        }
    ], (
        "one line per document, speaking the classifier's vocabulary: the "
        "type from the labels file, the workstream from the section's "
        "classifier_workstream, and the document list whose team names "
        "those are"
    )
    assert out == room / "_key" / "answer-key.jsonl", (
        "the key is answer-key material and lives under KEY_ROOT"
    )


def test_an_unlabelled_document_refuses_the_whole_key(tmp_path):
    room, conf = _room(
        tmp_path,
        docs=(
            "01_corporate/1.1.1_constitutional-01.md",
            "01_corporate/1.1.2_constitutional-02.md",
        ),
    )
    with pytest.raises(AnswerKeyError, match="1.1.2_constitutional-02"):
        build_answer_key(room, conf, load_domain(DEFAULT_DOMAIN_ROOT))


def test_a_label_for_a_document_that_does_not_exist_is_refused(tmp_path):
    phantom = LABELS + (
        '  - path: "01_corporate/9.9.9_ghost.md"\n'
        '    document_type: "Lease"\n'
    )
    room, conf = _room(tmp_path, labels=phantom)
    with pytest.raises(AnswerKeyError, match="9.9.9_ghost"):
        build_answer_key(room, conf, load_domain(DEFAULT_DOMAIN_ROOT))


def test_a_duplicate_label_is_refused_not_last_wins(tmp_path):
    doubled = LABELS + (
        '  - path: "01_corporate/1.1.1_constitutional-01.md"\n'
        '    document_type: "Lease"\n'
    )
    room, conf = _room(tmp_path, labels=doubled)
    with pytest.raises(AnswerKeyError, match="1.1.1_constitutional-01"):
        load_labels(room / "_key" / "labels.yaml")


def test_missing_labels_file_is_a_plain_refusal(tmp_path):
    room, conf = _room(tmp_path, labels=None)
    with pytest.raises(AnswerKeyError, match="labels.yaml"):
        build_answer_key(room, conf, load_domain(DEFAULT_DOMAIN_ROOT))


def test_a_section_without_classifier_workstream_is_refused(tmp_path):
    room, conf = _room(tmp_path)
    bare = DomainPack(
        sections=[
            Section(
                number=1,
                dir_name="01_corporate",
                title="Corporate",
                workstream="corporate",
                weight=1.0,
                subsections=["constitutional"],
            )
        ],
        archetypes={"standard": Archetype(name="standard", floor=1, filename_patterns=[])},
        default_archetype="standard",
        tier_f_floor=1,
        finding_archetypes={"corporate": []},
    )
    with pytest.raises(AnswerKeyError, match="01_corporate") as excinfo:
        build_answer_key(room, conf, bare)
    assert "classifier_workstream" in str(excinfo.value), (
        "the fix is a field in sections.yaml; the error must name it"
    )


def test_the_shipped_pack_names_a_classifier_workstream_for_every_section():
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    missing = [s.dir_name for s in pack.sections if not s.classifier_workstream]
    assert missing == [], (
        "every shipped section must say which classifier workstream its "
        f"documents belong to; missing: {missing}"
    )


# gb-docclass divided three teams on its document list 0.15.0 (release
# 2.0.1, 23 November 2026): ip-it into ip and it-data, employment into
# employment and incentives, finance into finance and banking. One document
# from each section the division touched, with the team a new key must give
# it. 9.3 is the Employment section's share-schemes subsection.
DIVIDED_SECTIONS = {
    "02_financial/2.1_statutory-accounts/2.1.1_statutory-accounts-01.md": "finance",
    "04_financing-banking/4.1_facilities/4.1.1_facilities-01.md": "banking",
    "06_intellectual-property/6.1_registrations/6.1.1_registrations-01.md": "ip",
    "07_information-technology/7.1_systems/7.1.1_systems-01.md": "it-data",
    "09_employment/9.1_contracts/9.1.1_contracts-01.md": "employment",
    "09_employment/9.3_share-schemes/9.3.1_share-schemes-01.md": "incentives",
    "14_data-protection/14.2_dpias/14.2.1_dpias-01.md": "it-data",
}


def _labels_for(paths) -> str:
    return "labels:\n" + "".join(
        f'  - path: "{p}"\n    document_type: "Correspondence"\n' for p in paths
    )


def test_a_new_key_names_the_teams_gb_docclass_divided_and_never_ip_it(tmp_path):
    room, conf = _room(
        tmp_path, labels=_labels_for(DIVIDED_SECTIONS), docs=tuple(DIVIDED_SECTIONS)
    )
    out = build_answer_key(room, conf, load_domain(DEFAULT_DOMAIN_ROOT))
    records = [json.loads(line) for line in out.read_text().splitlines()]
    got = {r["source_path"]: r["primary_workstream"] for r in records}
    assert got == DIVIDED_SECTIONS, (
        "each section the division touched goes to one of the new teams; "
        "share-scheme documents go to incentives, the rest of Employment "
        "stays employment"
    )
    assert "ip-it" not in out.read_text(), "ip-it is retired and never issued again"
    assert {r.get("teams_as_of") for r in records} == {"0.15.0"}, (
        "every row says it is written on the new names, which is how "
        "gb-docclass's scorer knows employment and finance mean the "
        "narrower teams and not either half"
    )


def test_teams_as_of_sits_after_secondary_workstreams(tmp_path):
    room, conf = _room(tmp_path)
    out = build_answer_key(room, conf, load_domain(DEFAULT_DOMAIN_ROOT))
    keys = list(json.loads(out.read_text().splitlines()[0]))
    assert keys == [
        "source_path",
        "document_type",
        "primary_workstream",
        "secondary_workstreams",
        "teams_as_of",
    ], "the field order gb-docclass's own key builder writes"


def test_every_shipped_team_is_a_current_gb_docclass_team():
    from synthvdr.answer_key import CLASSIFIER_TEAMS

    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    named = {s.classifier_workstream for s in pack.sections} | {
        team
        for s in pack.sections
        for team in s.classifier_workstream_by_subsection.values()
    }
    assert named - CLASSIFIER_TEAMS == set(), (
        "sections.yaml may only name teams gb-docclass issues today"
    )
    assert "ip-it" not in CLASSIFIER_TEAMS


def _one_section_pack(team: str, by_subsection=None) -> DomainPack:
    return DomainPack(
        sections=[
            Section(
                number=1,
                dir_name="01_corporate",
                title="Corporate",
                workstream="corporate",
                weight=1.0,
                subsections=["constitutional"],
                classifier_workstream=team,
                classifier_workstream_by_subsection=by_subsection or {},
            )
        ],
        archetypes={"standard": Archetype(name="standard", floor=1, filename_patterns=[])},
        default_archetype="standard",
        tier_f_floor=1,
        finding_archetypes={"corporate": []},
    )


def test_a_section_naming_the_retired_ip_it_is_refused(tmp_path):
    room, conf = _room(tmp_path)
    with pytest.raises(AnswerKeyError, match="ip-it") as excinfo:
        build_answer_key(room, conf, _one_section_pack("ip-it"))
    message = str(excinfo.value)
    assert "01_corporate" in message and "it-data" in message, (
        "the refusal names the section and what the retired team became"
    )


def test_a_section_naming_no_gb_docclass_team_is_refused(tmp_path):
    room, conf = _room(tmp_path)
    with pytest.raises(AnswerKeyError, match="real_estate"):
        build_answer_key(room, conf, _one_section_pack("real_estate"))


def test_a_subsection_team_for_a_subsection_the_section_lacks_is_refused():
    from synthvdr.domain import DomainError

    with pytest.raises(DomainError, match="share-schemes"):
        _one_section_pack("corporate", {"share-schemes": "incentives"})


def test_cli_writes_the_key_and_reports(tmp_path):
    from synthvdr.__main__ import main

    _room(tmp_path)
    assert main(["answerkey", "--room", str(tmp_path)]) == 0
    assert (tmp_path / "_key" / "answer-key.jsonl").is_file()


def test_cli_refuses_plainly_without_labels(tmp_path, capsys):
    from synthvdr.__main__ import main

    _room(tmp_path, labels=None)
    assert main(["answerkey", "--room", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "labels.yaml" in err
    assert "\n" not in err.strip(), "errors print as one readable line"


# ---------------------------------------------------------------------------
# consolidate_wave_labels — the wave hand-back half. Same guarantees as
# consolidate_wave_incoming: pure, safe to re-run over untouched intake,
# and a conflict raises rather than letting the later value silently win.
# ---------------------------------------------------------------------------


def test_wave_labels_merge_sorted_by_path():
    from synthvdr.answer_key import consolidate_wave_labels

    merged = consolidate_wave_labels(
        {"labels": []},
        {
            "wave1-batch-b": {"labels": [
                {"path": "02_financial/2.1.1_accounts-01.md",
                 "document_type": "Statutory accounts"}]},
            "wave1-batch-a": {"labels": [
                {"path": "01_corporate/1.1.1_constitutional-01.md",
                 "document_type": "Articles of association"}]},
        },
    )
    assert [row["path"] for row in merged["labels"]] == [
        "01_corporate/1.1.1_constitutional-01.md",
        "02_financial/2.1.1_accounts-01.md",
    ], "labels.yaml is canonical and sorted, so reruns produce the same bytes"


def test_wave_labels_rerun_over_untouched_intake_is_a_no_op():
    from synthvdr.answer_key import consolidate_wave_labels

    incoming = {
        "wave1-batch-a": {"labels": [
            {"path": "01_corporate/1.1.1_constitutional-01.md",
             "document_type": "Articles of association"}]},
    }
    once = consolidate_wave_labels({"labels": []}, incoming)
    twice = consolidate_wave_labels(once, incoming)
    assert twice == once, (
        "wave n re-reads every prior wave's intake; an already-applied "
        "label must be a no-op, or every wave doubles the file"
    )


def test_wave_labels_conflict_raises_rather_than_last_wins():
    from synthvdr.answer_key import consolidate_wave_labels

    existing = {"labels": [
        {"path": "01_corporate/1.1.1_constitutional-01.md",
         "document_type": "Articles of association"}]}
    with pytest.raises(AnswerKeyError, match="1.1.1_constitutional-01") as excinfo:
        consolidate_wave_labels(
            existing,
            {"wave2-batch-a": {"labels": [
                {"path": "01_corporate/1.1.1_constitutional-01.md",
                 "document_type": "Lease"}]}},
        )
    message = str(excinfo.value)
    assert "Articles of association" in message and "Lease" in message, (
        "both claimed types must be named — the author needs to see what "
        "they are contradicting"
    )


def test_wave_labels_malformed_row_names_the_file_and_index():
    from synthvdr.answer_key import consolidate_wave_labels

    with pytest.raises(AnswerKeyError, match=r"wave1-batch-a.*labels\[1\]"):
        consolidate_wave_labels(
            {"labels": []},
            {"wave1-batch-a": {"labels": [
                {"path": "a.md", "document_type": "Lease"},
                {"path": "b.md"},
            ]}},
        )


# ---------------------------------------------------------------------------
# The optional vocabulary check — authors free-type document_type, and a
# drifted name ("NDA" for "Non-disclosure agreement") would score as a
# classifier miss. The classifier's own list, handed in as a file, catches
# the drift at key-build time.
# ---------------------------------------------------------------------------


def test_vocabulary_refuses_a_name_outside_the_list(tmp_path):
    room, conf = _room(tmp_path)
    with pytest.raises(AnswerKeyError, match="Articles of association"):
        build_answer_key(
            room, conf, load_domain(DEFAULT_DOMAIN_ROOT), vocabulary={"Lease"}
        )


def test_vocabulary_accepts_a_listed_name(tmp_path):
    room, conf = _room(tmp_path)
    out = build_answer_key(
        room, conf, load_domain(DEFAULT_DOMAIN_ROOT),
        vocabulary={"Articles of association"},
    )
    assert out.is_file()


def test_cli_vocabulary_flag(tmp_path, capsys):
    from synthvdr.__main__ import main

    _room(tmp_path)
    vocab = tmp_path / "types.txt"
    vocab.write_text("# the classifier's document list\nLease\n")
    assert main(["answerkey", "--room", str(tmp_path),
                 "--vocabulary", str(vocab)]) == 2
    assert "Articles of association" in capsys.readouterr().err


# --- the classifier vocabulary, checkable per wave --------------------------


def test_load_vocabulary_skips_blanks_and_comments(tmp_path):
    from synthvdr.answer_key import load_vocabulary

    path = tmp_path / "classifier-vocab.txt"
    path.write_text(
        "# taken from the taxonomy's document_types\n"
        "Articles of association\n"
        "\n"
        "  Lease  \n"
        "   # indented comment\n",
        encoding="utf-8",
    )
    assert load_vocabulary(path) == {"Articles of association", "Lease"}


def test_load_vocabulary_does_not_normalise_case_or_punctuation(tmp_path):
    """A type is matched by exact equality, so normalising here would accept
    a label the classifier itself would refuse."""
    from synthvdr.answer_key import load_vocabulary

    path = tmp_path / "v.txt"
    path.write_text("Non-disclosure agreement\n", encoding="utf-8")
    vocab = load_vocabulary(path)
    assert "non-disclosure agreement" not in vocab
    assert "Non disclosure agreement" not in vocab


def test_labels_outside_vocabulary_groups_by_type():
    from synthvdr.answer_key import labels_outside_vocabulary

    labels = {
        "a.md": "Lease",
        "b.md": "NDA",
        "c.md": "NDA",
        "d.md": "Articles of association",
    }
    out = labels_outside_vocabulary(labels, {"Lease", "Articles of association"})
    assert out == {"NDA": ["b.md", "c.md"]}


def test_labels_outside_vocabulary_is_empty_when_every_label_is_known():
    from synthvdr.answer_key import labels_outside_vocabulary

    labels = {"a.md": "Lease"}
    assert labels_outside_vocabulary(labels, {"Lease"}) == {}


def test_per_wave_check_and_the_build_refusal_agree():
    """One predicate, two callers. A wave that clears the per-wave check
    cannot then be refused by the builder for the same reason — the bug
    this pair exists to prevent is a drift found only at package time."""
    from synthvdr.answer_key import labels_outside_vocabulary

    vocab = {"Lease"}
    labels = {"a.md": "Lease", "b.md": "Tenancy"}
    drifted = labels_outside_vocabulary(labels, vocab)
    assert sorted(drifted) == ["Tenancy"]
    # answer_key_records refuses on exactly sorted(labels_outside_vocabulary(...))
    assert sorted(drifted) == sorted({t for t in labels.values() if t not in vocab})


def test_cli_uses_the_rooms_own_vocabulary_without_being_told(tmp_path, capsys):
    """The flag being forgotten used to mean no checking at all, silently."""
    from synthvdr.__main__ import main

    _room(tmp_path)
    (tmp_path / "_key" / "classifier-vocab.txt").write_text("Lease\n", encoding="utf-8")
    # The room's only label is "Articles of association", which this vocabulary
    # does not contain — so a default that works refuses, and one that is
    # skipped writes the key.
    assert main(["answerkey", "--room", str(tmp_path)]) == 2
    assert "outside the" in capsys.readouterr().err
    assert not (tmp_path / "_key" / "answer-key.jsonl").is_file()


def test_cli_explicit_vocabulary_overrides_the_rooms_copy(tmp_path):
    from synthvdr.__main__ import main

    _room(tmp_path)
    (tmp_path / "_key" / "classifier-vocab.txt").write_text("Lease\n", encoding="utf-8")
    override = tmp_path / "elsewhere.txt"
    override.write_text("Articles of association\n", encoding="utf-8")
    assert main(["answerkey", "--room", str(tmp_path), "--vocabulary", str(override)]) == 0
    assert (tmp_path / "_key" / "answer-key.jsonl").is_file()


def test_cli_warns_when_an_eval_room_has_no_vocabulary(tmp_path, capsys):
    from synthvdr.__main__ import main

    _room(tmp_path)
    conf = tmp_path / "room.conf"
    conf.write_text(conf.read_text() + 'ROOM_ROLE="eval"\n')
    assert main(["answerkey", "--room", str(tmp_path)]) == 0
    assert "unchecked" in capsys.readouterr().err


def test_cli_does_not_warn_an_exemplar_room_about_a_vocabulary(tmp_path, capsys):
    """An exemplar room has none by definition; warning there is noise, and
    noise is how a real warning gets ignored."""
    from synthvdr.__main__ import main

    _room(tmp_path)
    conf = tmp_path / "room.conf"
    conf.write_text(conf.read_text() + 'ROOM_ROLE="exemplar"\n')
    assert main(["answerkey", "--room", str(tmp_path)]) == 0
    assert capsys.readouterr().err == ""
