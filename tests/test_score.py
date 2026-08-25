"""Tests for synthvdr.score: the deterministic pre-match, adjudication hook,
scorecard rendering, baseline diff, and room-provenance checking.

Beyond the floor set by the task brief, this file also covers:

  - all three provenance branches (verified, UNVERIFIED for each reason a
    required input can be missing, and the hard refusal on a proven
    mismatch) — this is the one defect class an eval tool cannot afford:
    a confident, precise, entirely meaningless scorecard;
  - that an unmatched-and-unadjudicated report is distinguishable, in both
    the Scorecard and the rendered text, from a report an adjudicator
    positively confirmed matches nothing — both currently score recall 0
    for that report, but they are not the same finding;
  - division-by-zero paths (zero findings in the key, zero reported
    findings, both at once) do not raise and do not fabricate a number;
  - determinism: byte-identical rendered output across two calls in one
    process, and — because a same-process check cannot see reliance on
    PYTHONHASHSEED-salted set iteration order, which is stable for the
    life of one process — across two fresh subprocesses with different
    PYTHONHASHSEED values.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from synthvdr.schema import SEVERITIES, Distractor, Finding, FindingSet
from synthvdr.score import (
    Adjudication,
    AdjudicationError,
    ProvenanceError,
    ToolFinding,
    ToolOutput,
    ToolOutputError,
    check_provenance,
    load_adjudications,
    load_adjudications_for_room,
    load_tool_output,
    parse_markdown_report,
    prematch,
    render_scorecard,
    score,
    validate_adjudications,
)

SRC = "11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md"
CORR = "02_financial/2.4_provisions/2.4.1_provision.md"
DX_DOC = "11_environmental-hs/11.4_hse-notices/11.4.1_notice.md"


def findings():
    return FindingSet(
        [
            Finding(
                id="ENV-1", title="Contamination under-provisioned", severity="critical",
                workstream="environmental", multi_document=True, source=SRC,
                location="Table 4", substance="estimate exceeds provision",
                corroboration=[CORR], discoverable_from_blind=True,
            ),
            Finding(
                id="EMP-2", title="Contractors misclassified", severity="medium",
                workstream="employment", multi_document=False,
                source="09_employment/9.1_contracts/9.1.4_consultancy.md",
                location="clause 3", substance="misclassification",
                discoverable_from_blind=True,
            ),
        ],
        "Project Testbed",
    )


def distractors():
    return [Distractor(id="DX-1", title="Remediated notice", location=DX_DOC, resolution="x.md")]


def output(*tool_findings, tool="acme/1.0"):
    return ToolOutput(tool=tool, room_hash="abc", findings=list(tool_findings))


# --- brief floor -------------------------------------------------------------


def test_prematch_links_on_the_source_document():
    out = output(ToolFinding("Land issue", "high", [SRC], "the estimate is high"))
    matched, unmatched = prematch(out, findings())
    assert matched == {0: ["ENV-1"]}
    assert unmatched == []


def test_prematch_leaves_an_uncited_finding_for_adjudication():
    out = output(ToolFinding("Something", "high", [], "no documents cited"))
    matched, unmatched = prematch(out, findings())
    assert matched == {}
    assert unmatched == [0]


def test_adjudication_is_honoured():
    out = output(ToolFinding("Something", "medium", [], "misclassified contractors"))
    card = score(out, findings(), distractors(), adjudications=[Adjudication(0, "EMP-2", "clear")])
    assert card.by_severity["medium"] == (1, 1)


def test_recall_counts_only_matched_findings():
    out = output(ToolFinding("Land issue", "critical", [SRC], "x"))
    card = score(out, findings(), distractors())
    assert card.by_severity["critical"] == (1, 1)
    assert card.by_severity["medium"] == (0, 1)
    assert card.recall == 0.5
    assert card.misses == ["EMP-2"]


def test_a_distractor_report_is_a_false_alarm_not_a_hit():
    out = output(ToolFinding("Regulator notice", "high", [DX_DOC], "looks bad"))
    card = score(out, findings(), distractors())
    assert card.false_alarms == ["DX-1"]
    assert card.recall == 0.0


def test_precision_excludes_false_alarms_and_unmatched_reports():
    out = output(
        ToolFinding("Land issue", "critical", [SRC], "x"),
        ToolFinding("Regulator notice", "high", [DX_DOC], "y"),
    )
    card = score(out, findings(), distractors())
    assert card.precision == 0.5


def test_partial_trail_credit_when_only_part_of_a_multi_document_trail_is_cited():
    out = output(ToolFinding("Land issue", "critical", [SRC], "x"))
    card = score(out, findings(), distractors())
    assert card.partial_trails == ["ENV-1"]


def test_full_trail_is_not_marked_partial():
    out = output(ToolFinding("Land issue", "critical", [SRC, CORR], "x"))
    card = score(out, findings(), distractors())
    assert card.partial_trails == []


def test_loads_json_tool_output(tmp_path):
    path = tmp_path / "out.json"
    path.write_text(
        json.dumps(
            {
                "tool": "acme/1.0",
                "room_hash": "abc",
                "findings": [{"title": "t", "severity": "high", "documents": [SRC], "summary": "s"}],
            }
        )
    )
    loaded = load_tool_output(path)
    assert loaded.tool == "acme/1.0"
    assert loaded.findings[0].documents == [SRC]


def test_scorecard_renders_every_finding_in_the_hit_table():
    out = output(ToolFinding("Land issue", "critical", [SRC], "x"))
    text = render_scorecard(score(out, findings(), distractors()), out, findings())
    assert "ENV-1" in text and "EMP-2" in text


# --- an unadjudicated miss is not the same finding as a confirmed non-match -


def test_unmatched_and_unadjudicated_is_distinguished_from_a_confirmed_non_match():
    out = output(ToolFinding("Something", "high", [], "no documents cited"))

    unadjudicated_card = score(out, findings(), distractors())
    assert unadjudicated_card.recall == 0.0
    assert unadjudicated_card.unadjudicated == [0]

    confirmed_card = score(
        out, findings(), distractors(), adjudications=[Adjudication(0, None, "not a real finding")]
    )
    assert confirmed_card.recall == 0.0
    assert confirmed_card.unadjudicated == []


def test_render_scorecard_flags_unadjudicated_reports_distinctly():
    out = output(ToolFinding("Something", "high", [], "no documents cited"))
    card = score(out, findings(), distractors())
    text = render_scorecard(card, out, findings())
    assert "not adjudicated" in text
    assert "1 reported findings cited no known document" in text


def test_render_scorecard_says_nothing_extra_once_everything_is_adjudicated():
    out = output(ToolFinding("Something", "high", [], "no documents cited"))
    card = score(out, findings(), distractors(), adjudications=[Adjudication(0, None, "not real")])
    text = render_scorecard(card, out, findings())
    assert "not adjudicated" not in text


# --- division-by-zero paths ---------------------------------------------------


def test_recall_is_zero_not_an_error_with_zero_findings_in_the_key():
    empty = FindingSet([], "Empty Room")
    out = output(ToolFinding("Something", "high", [], "x"))
    card = score(out, empty, distractors())
    assert card.recall == 0.0
    assert card.by_severity == {s: (0, 0) for s in SEVERITIES}
    assert card.misses == []


def test_precision_is_zero_not_an_error_with_zero_reported_findings():
    out = output()
    card = score(out, findings(), distractors())
    assert card.precision == 0.0
    assert card.recall == 0.0
    assert card.hit_table == [("ENV-1", "critical", False), ("EMP-2", "medium", False)]


def test_scores_cleanly_with_zero_findings_in_the_key_and_zero_reported():
    out = output()
    card = score(out, FindingSet([], "Empty Room"), [])
    assert card.recall == 0.0
    assert card.precision == 0.0
    assert card.hit_table == []
    assert card.misses == []


# --- provenance checking ------------------------------------------------------


def _write_manifest(room, content_hash=None, extra=None):
    key = room / "_key"
    key.mkdir(exist_ok=True)
    body = {"room": "Project Testbed", "documents": 1, "findings": 1, "built": "2026-08-24"}
    if content_hash is not None:
        body["content_hash"] = content_hash
    if extra:
        body.update(extra)
    (key / "manifest.json").write_text(json.dumps(body), encoding="utf-8")


def test_provenance_verified_when_hashes_match(tmp_path):
    _write_manifest(tmp_path, content_hash="abc123")
    out = ToolOutput(tool="acme/1.0", room_hash="abc123", findings=[])
    status = check_provenance(tmp_path, out)
    assert status.verified is True
    assert "abc123" in status.detail


def test_provenance_refuses_a_proven_mismatch(tmp_path):
    _write_manifest(tmp_path, content_hash="abc123")
    out = ToolOutput(tool="acme/1.0", room_hash="xyz789", findings=[])
    with pytest.raises(ProvenanceError) as excinfo:
        check_provenance(tmp_path, out)
    message = str(excinfo.value)
    assert "abc123" in message
    assert "xyz789" in message


def test_provenance_unverified_when_manifest_is_absent(tmp_path):
    out = ToolOutput(tool="acme/1.0", room_hash="abc123", findings=[])
    status = check_provenance(tmp_path, out)
    assert status.verified is False
    assert "manifest" in status.detail.lower()


def test_provenance_unverified_when_manifest_has_no_content_hash(tmp_path):
    _write_manifest(tmp_path, content_hash=None)
    out = ToolOutput(tool="acme/1.0", room_hash="abc123", findings=[])
    status = check_provenance(tmp_path, out)
    assert status.verified is False
    assert "content_hash" in status.detail.lower()


def test_provenance_unverified_when_room_hash_is_empty(tmp_path):
    _write_manifest(tmp_path, content_hash="abc123")
    out = ToolOutput(tool="acme/1.0", room_hash="", findings=[])
    status = check_provenance(tmp_path, out)
    assert status.verified is False
    assert "room_hash" in status.detail.lower()


def test_provenance_unverified_when_manifest_is_not_valid_json(tmp_path):
    key = tmp_path / "_key"
    key.mkdir()
    (key / "manifest.json").write_text("{not valid json", encoding="utf-8")
    out = ToolOutput(tool="acme/1.0", room_hash="abc123", findings=[])
    status = check_provenance(tmp_path, out)
    assert status.verified is False


def test_verified_provenance_renders_in_the_scorecard(tmp_path):
    _write_manifest(tmp_path, content_hash="abc123")
    out = ToolOutput(tool="acme/1.0", room_hash="abc123", findings=[ToolFinding("Land issue", "critical", [SRC], "x")])
    status = check_provenance(tmp_path, out)
    card = score(out, findings(), distractors())
    text = render_scorecard(card, out, findings(), provenance=status)
    assert "Provenance: verified" in text


def test_unverified_provenance_renders_in_the_scorecard_naming_what_was_missing(tmp_path):
    out = ToolOutput(tool="acme/1.0", room_hash="", findings=[])
    status = check_provenance(tmp_path, out)
    card = score(out, findings(), distractors())
    text = render_scorecard(card, out, findings(), provenance=status)
    assert "UNVERIFIED" in text
    assert "manifest" in text.lower()


def test_render_scorecard_omits_the_provenance_line_when_not_supplied():
    out = output(ToolFinding("Land issue", "critical", [SRC], "x"))
    text = render_scorecard(score(out, findings(), distractors()), out, findings())
    assert "Provenance" not in text


# --- determinism ---------------------------------------------------------------


def _build_scorecard_text() -> str:
    out = output(
        ToolFinding("Land issue", "critical", [SRC], "x"),
        ToolFinding("Regulator notice", "high", [DX_DOC], "y"),
        ToolFinding("Something else", "low", [], "z"),
    )
    card = score(out, findings(), distractors())
    return render_scorecard(card, out, findings())


def test_render_scorecard_is_byte_identical_across_two_calls():
    assert _build_scorecard_text() == _build_scorecard_text()


_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    from synthvdr.schema import Distractor, Finding, FindingSet
    from synthvdr.score import ToolFinding, ToolOutput, render_scorecard, score

    findings = FindingSet(
        [
            Finding(id="ENV-1", title="a", severity="critical", workstream="environmental",
                    multi_document=True, source="src1.md", location="x", substance="s",
                    corroboration=["corr1.md"]),
            Finding(id="ENV-2", title="b", severity="high", workstream="environmental",
                    multi_document=True, source="src5.md", location="x", substance="s",
                    corroboration=["corr5.md"]),
            Finding(id="FIN-1", title="c", severity="medium", workstream="financial",
                    multi_document=False, source="src2.md", location="x", substance="s"),
            Finding(id="FIN-2", title="d", severity="medium", workstream="financial",
                    multi_document=False, source="src3.md", location="x", substance="s"),
            Finding(id="FIN-3", title="e", severity="low", workstream="financial",
                    multi_document=False, source="src4.md", location="x", substance="s"),
        ],
        "Project Testbed",
    )
    distractors = [
        Distractor(id="DX-1", title="a", location="dx1.md", resolution="r1.md"),
        Distractor(id="DX-2", title="b", location="dx2.md", resolution="r2.md"),
        Distractor(id="DX-3", title="c", location="dx3.md", resolution="r3.md"),
    ]
    output = ToolOutput(
        tool="acme/1.0",
        room_hash="abc",
        findings=[
            ToolFinding("t1", "critical", ["src1.md"], "s"),
            ToolFinding("t2", "high", ["src5.md"], "s"),
            ToolFinding("t3", "high", ["dx1.md"], "s"),
            ToolFinding("t4", "medium", ["dx2.md"], "s"),
            ToolFinding("t5", "low", ["dx3.md"], "s"),
            ToolFinding("t6", "high", [], "s"),
            ToolFinding("t7", "high", [], "s"),
        ],
    )
    card = score(output, findings, distractors)
    print(render_scorecard(card, output, findings))
    """
)


def _render_in_subprocess(hash_seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT], capture_output=True, text=True, env=env, check=True
    )
    return result.stdout


def test_render_scorecard_is_byte_identical_across_processes_with_different_hash_seeds():
    """A same-process check cannot see reliance on PYTHONHASHSEED-salted
    set iteration order over strings, because PYTHONHASHSEED is fixed for
    the life of one process — two calls in it agree even when the
    underlying ordering is not truly stable. This fixture deliberately
    carries >=2 entries in every set-derived collection (false_alarms,
    partial_trails) so a missing sorted() has something to disagree about.
    """
    first = _render_in_subprocess("1")
    second = _render_in_subprocess("4242")
    assert first == second
    assert "DX-1" in first and "DX-2" in first and "DX-3" in first
    assert "ENV-1" in first and "ENV-2" in first


# --- adjudications: auto-load, validate, and take precedence -----------------


def test_load_adjudications_parses_a_well_formed_file(tmp_path):
    path = tmp_path / "adjudications.yaml"
    path.write_text(
        "adjudications:\n"
        "  - tool_index: 4\n"
        "    finding_id: EMP-2\n"
        "    reason: \"clear\"\n"
        "  - tool_index: 7\n"
        "    finding_id: null\n"
        "    reason: \"matches nothing\"\n",
        encoding="utf-8",
    )
    loaded = load_adjudications(path)
    assert loaded == [
        Adjudication(4, "EMP-2", "clear"),
        Adjudication(7, None, "matches nothing"),
    ]


def test_load_adjudications_rejects_invalid_yaml(tmp_path):
    path = tmp_path / "adjudications.yaml"
    path.write_text("adjudications: [this is not: a proper: mapping list\n", encoding="utf-8")
    with pytest.raises(AdjudicationError):
        load_adjudications(path)


def test_load_adjudications_rejects_a_missing_top_level_key(tmp_path):
    path = tmp_path / "adjudications.yaml"
    path.write_text("not_adjudications: []\n", encoding="utf-8")
    with pytest.raises(AdjudicationError):
        load_adjudications(path)


def test_load_adjudications_rejects_a_row_missing_finding_id(tmp_path):
    path = tmp_path / "adjudications.yaml"
    path.write_text(
        "adjudications:\n  - tool_index: 0\n    reason: \"no finding_id key at all\"\n",
        encoding="utf-8",
    )
    with pytest.raises(AdjudicationError):
        load_adjudications(path)


def test_validate_adjudications_rejects_an_out_of_range_tool_index():
    out = output(ToolFinding("Something", "high", [], "x"))
    bad = [Adjudication(5, "EMP-2", "way out of range")]
    with pytest.raises(AdjudicationError) as excinfo:
        validate_adjudications(bad, out, findings())
    assert "5" in str(excinfo.value)


def test_validate_adjudications_rejects_an_unknown_finding_id():
    out = output(ToolFinding("Something", "high", [], "x"))
    bad = [Adjudication(0, "NOPE-9", "not a real finding id")]
    with pytest.raises(AdjudicationError) as excinfo:
        validate_adjudications(bad, out, findings())
    assert "NOPE-9" in str(excinfo.value)


def test_validate_adjudications_rejects_a_duplicate_tool_index():
    out = output(
        ToolFinding("Something", "high", [], "x"),
    )
    bad = [
        Adjudication(0, "ENV-1", "first call"),
        Adjudication(0, "EMP-2", "second call, contradicts the first"),
    ]
    with pytest.raises(AdjudicationError):
        validate_adjudications(bad, out, findings())


def test_validate_adjudications_accepts_a_well_formed_confirmed_non_match():
    out = output(ToolFinding("Something", "high", [], "x"))
    ok = [Adjudication(0, None, "matches nothing")]
    validate_adjudications(ok, out, findings())  # must not raise


def test_load_adjudications_for_room_reports_no_file_distinctly_from_zero_applied(tmp_path):
    out = output(ToolFinding("Something", "high", [], "x"))
    loaded, summary = load_adjudications_for_room(tmp_path, out, findings())
    assert loaded == []
    assert summary.applied == 0
    assert "no" in summary.detail.lower()
    assert "adjudications.yaml" in summary.detail


def test_load_adjudications_for_room_reports_the_count_applied(tmp_path):
    key = tmp_path / "_key"
    key.mkdir()
    (key / "adjudications.yaml").write_text(
        "adjudications:\n  - tool_index: 0\n    finding_id: EMP-2\n    reason: \"clear\"\n",
        encoding="utf-8",
    )
    out = output(ToolFinding("Something", "medium", [], "misclassified contractors"))
    loaded, summary = load_adjudications_for_room(tmp_path, out, findings())
    assert loaded == [Adjudication(0, "EMP-2", "clear")]
    assert summary.applied == 1
    assert "1" in summary.detail


def test_load_adjudications_for_room_propagates_a_malformed_file_loudly(tmp_path):
    key = tmp_path / "_key"
    key.mkdir()
    (key / "adjudications.yaml").write_text("not: [valid, adjudications, shape\n", encoding="utf-8")
    out = output(ToolFinding("Something", "high", [], "x"))
    with pytest.raises(AdjudicationError):
        load_adjudications_for_room(tmp_path, out, findings())


def test_load_adjudications_for_room_propagates_an_unreconcilable_entry_loudly(tmp_path):
    key = tmp_path / "_key"
    key.mkdir()
    (key / "adjudications.yaml").write_text(
        "adjudications:\n  - tool_index: 99\n    finding_id: EMP-2\n    reason: \"bad index\"\n",
        encoding="utf-8",
    )
    out = output(ToolFinding("Something", "high", [], "x"))
    with pytest.raises(AdjudicationError):
        load_adjudications_for_room(tmp_path, out, findings())


def test_adjudication_takes_precedence_over_a_conflicting_prematch():
    # SRC would ordinarily prematch to ENV-1; an adjudicator overriding
    # that call must win.
    out = output(ToolFinding("Land issue", "critical", [SRC], "x"))
    card = score(out, findings(), distractors(), adjudications=[Adjudication(0, "EMP-2", "corrected")])
    assert card.hit_table == [("ENV-1", "critical", False), ("EMP-2", "medium", True)]


def test_adjudication_of_none_removes_an_existing_prematch():
    # An adjudicator can override a pre-match to say it is actually not a
    # real match — this must remove the pre-match, not be ignored.
    out = output(ToolFinding("Land issue", "critical", [SRC], "x"))
    card = score(out, findings(), distractors(), adjudications=[Adjudication(0, None, "false positive")])
    assert card.hit_table == [("ENV-1", "critical", False), ("EMP-2", "medium", False)]
    assert card.recall == 0.0


def test_scorecard_renders_the_adjudication_summary_when_supplied(tmp_path):
    out = output(ToolFinding("Something", "medium", [], "misclassified contractors"))
    adjudications = [Adjudication(0, "EMP-2", "clear")]
    card = score(out, findings(), distractors(), adjudications=adjudications)
    _, summary = load_adjudications_for_room(tmp_path, out, findings())  # no file: "0 applied"
    text = render_scorecard(card, out, findings(), adjudication_summary=summary)
    assert "Adjudications:" in text
    assert "no" in text.lower()


# --- a genuine zero recall must not read the same as "nothing could be matched" -


def test_provisional_recall_is_marked_distinctly_from_a_confirmed_zero():
    """This is the exact scenario the coordinator asked to confirm: when
    prematch matches nothing and nothing has been adjudicated yet, recall
    is 0.0 — but so is a fully-adjudicated run where every report was
    positively confirmed to match nothing. Scorecard.unadjudicated is what
    tells them apart, and render_scorecard must say so, not just carry the
    field silently.
    """
    out = output(ToolFinding("Something", "high", [], "no documents cited"))

    unresolved_card = score(out, findings(), distractors())
    assert unresolved_card.recall == 0.0
    assert unresolved_card.unadjudicated == [0]
    unresolved_text = render_scorecard(unresolved_card, out, findings())
    assert "provisional" in unresolved_text.lower()

    confirmed_card = score(
        out, findings(), distractors(), adjudications=[Adjudication(0, None, "confirmed non-match")]
    )
    assert confirmed_card.recall == 0.0
    assert confirmed_card.unadjudicated == []
    confirmed_text = render_scorecard(confirmed_card, out, findings())
    assert "provisional" not in confirmed_text.lower()

    # Same numeric recall, different rendered text — that is the point.
    assert unresolved_text != confirmed_text


# --- many-to-many matching: one report can evidence more than one finding ----


def test_prematch_credits_every_finding_a_report_cites():
    out = output(ToolFinding("Both", "high", [SRC, "09_employment/9.1_contracts/9.1.4_consultancy.md"], "x"))
    matched, unmatched = prematch(out, findings())
    assert matched == {0: ["EMP-2", "ENV-1"]}
    assert unmatched == []


def test_prematch_matches_are_order_independent():
    """The exact bug the coordinator reported: which finding gets credit
    must not depend on the order a report happened to list its citations.
    """
    emp_src = "09_employment/9.1_contracts/9.1.4_consultancy.md"
    forward = output(ToolFinding("Both", "high", [SRC, emp_src], "x"))
    backward = output(ToolFinding("Both", "high", [emp_src, SRC], "x"))
    matched_forward, _ = prematch(forward, findings())
    matched_backward, _ = prematch(backward, findings())
    assert matched_forward == matched_backward == {0: ["EMP-2", "ENV-1"]}


def test_recall_counts_both_findings_when_one_report_cites_both_regardless_of_order():
    emp_src = "09_employment/9.1_contracts/9.1.4_consultancy.md"
    forward = output(ToolFinding("Both", "high", [SRC, emp_src], "x"))
    backward = output(ToolFinding("Both", "high", [emp_src, SRC], "x"))
    card_forward = score(forward, findings(), distractors())
    card_backward = score(backward, findings(), distractors())
    assert card_forward.recall == card_backward.recall == 1.0
    assert card_forward.misses == card_backward.misses == []
    # Not just the numbers — the whole rendered scorecard must be identical.
    assert render_scorecard(card_forward, forward, findings()) == render_scorecard(
        card_backward, backward, findings()
    )


def test_a_report_can_evidence_three_findings_at_once():
    key = FindingSet(
        [
            Finding(id="A-1", title="a", severity="critical", workstream="w", multi_document=False,
                    source="a.md", location="x", substance="s"),
            Finding(id="B-1", title="b", severity="high", workstream="w", multi_document=False,
                    source="b.md", location="x", substance="s"),
            Finding(id="C-1", title="c", severity="medium", workstream="w", multi_document=False,
                    source="c.md", location="x", substance="s"),
        ],
        "Three Findings",
    )
    out = output(ToolFinding("Three at once", "high", ["a.md", "b.md", "c.md"], "x"))
    matched, unmatched = prematch(out, key)
    assert matched == {0: ["A-1", "B-1", "C-1"]}
    assert unmatched == []
    card = score(out, key, [])
    assert card.recall == 1.0
    assert card.precision == 1.0
    assert card.misses == []


def test_precision_credits_two_correct_reports_of_one_finding_as_both_right():
    """The other half of the same bug class: two reports both correctly
    citing the same finding is precision 1.0 (both reports were right),
    not 0.5 (as if one of them were a duplicate mistake) — recall still
    only credits the one finding once.
    """
    out = output(
        ToolFinding("r1", "critical", [SRC], "x"),
        ToolFinding("r2", "critical", [SRC], "y"),
    )
    card = score(out, findings(), distractors())
    assert card.precision == 1.0
    assert card.recall == 0.5
    assert card.false_alarms == []


def test_partial_trail_is_computed_over_the_union_of_reports_matched_to_a_finding():
    """A multi-document trail split across two separate reports (one citing
    the source, the other the corroboration) is a COMPLETE trail once you
    look at everything matched to that finding, not two partial ones. This
    is what previously made hit_table say hit=True while partial_trails
    also flagged the same finding — a contradiction the per-finding view
    resolves by construction.
    """
    out = output(
        ToolFinding("r1", "critical", [SRC], "x"),
        ToolFinding("r2", "critical", [CORR], "y"),
    )
    card = score(out, findings(), distractors())
    assert card.partial_trails == []
    assert card.hit_table == [("ENV-1", "critical", True), ("EMP-2", "medium", False)]


def test_partial_trail_still_flags_a_genuinely_incomplete_trail():
    # Sanity check the fix didn't just delete the partial-trail check.
    out = output(ToolFinding("r1", "critical", [SRC], "x"))
    card = score(out, findings(), distractors())
    assert card.partial_trails == ["ENV-1"]


def test_adjudication_can_assign_a_list_of_finding_ids():
    out = output(ToolFinding("Both", "high", [], "matches both findings at once"))
    card = score(
        out, findings(), distractors(), adjudications=[Adjudication(0, ["ENV-1", "EMP-2"], "matches both")]
    )
    assert card.recall == 1.0
    assert card.hit_table == [("ENV-1", "critical", True), ("EMP-2", "medium", True)]


def test_validate_adjudications_checks_every_id_in_a_list():
    out = output(ToolFinding("Both", "high", [], "x"))
    bad = [Adjudication(0, ["ENV-1", "NOPE-9"], "one real, one not")]
    with pytest.raises(AdjudicationError) as excinfo:
        validate_adjudications(bad, out, findings())
    assert "NOPE-9" in str(excinfo.value)


def test_load_adjudications_accepts_a_list_of_finding_ids(tmp_path):
    path = tmp_path / "adjudications.yaml"
    path.write_text(
        "adjudications:\n  - tool_index: 0\n    finding_id: [ENV-1, EMP-2]\n    reason: \"both\"\n",
        encoding="utf-8",
    )
    loaded = load_adjudications(path)
    assert loaded == [Adjudication(0, ["ENV-1", "EMP-2"], "both")]


def test_load_adjudications_rejects_a_finding_id_list_containing_a_non_string(tmp_path):
    path = tmp_path / "adjudications.yaml"
    path.write_text(
        "adjudications:\n  - tool_index: 0\n    finding_id: [ENV-1, 5]\n    reason: \"bad\"\n",
        encoding="utf-8",
    )
    with pytest.raises(AdjudicationError):
        load_adjudications(path)


# --- structurally invalid tool output is a clean error, never a traceback ----


def test_load_tool_output_rejects_a_json_list_root(tmp_path):
    path = tmp_path / "out.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ToolOutputError):
        load_tool_output(path)


def test_load_tool_output_rejects_a_json_string_root(tmp_path):
    path = tmp_path / "out.json"
    path.write_text('"just a string"', encoding="utf-8")
    with pytest.raises(ToolOutputError):
        load_tool_output(path)


def test_load_tool_output_rejects_findings_that_is_not_a_list(tmp_path):
    path = tmp_path / "out.json"
    path.write_text(json.dumps({"tool": "t", "findings": "not-a-list"}), encoding="utf-8")
    with pytest.raises(ToolOutputError):
        load_tool_output(path)


def test_load_tool_output_rejects_a_finding_row_that_is_not_an_object(tmp_path):
    path = tmp_path / "out.json"
    path.write_text(json.dumps({"tool": "t", "findings": ["not-an-object"]}), encoding="utf-8")
    with pytest.raises(ToolOutputError):
        load_tool_output(path)


def test_load_tool_output_still_accepts_an_explicit_empty_findings_list(tmp_path):
    # JSON's explicit empty list is a deliberate, structured "zero findings"
    # — this must stay valid, unlike the markdown cases below.
    path = tmp_path / "out.json"
    path.write_text(json.dumps({"tool": "t", "findings": []}), encoding="utf-8")
    loaded = load_tool_output(path)
    assert loaded.findings == []


def test_parse_markdown_report_rejects_an_empty_file():
    with pytest.raises(ToolOutputError):
        parse_markdown_report("")


def test_parse_markdown_report_rejects_prose_with_no_headings():
    with pytest.raises(ToolOutputError):
        parse_markdown_report("Just some prose. No headings here at all.")


def test_load_tool_output_rejects_an_empty_markdown_file(tmp_path):
    path = tmp_path / "out.md"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ToolOutputError):
        load_tool_output(path)


def test_load_tool_output_accepts_a_markdown_report_with_findings(tmp_path):
    path = tmp_path / "out.md"
    path.write_text(
        f"## Land issue\n\nSeverity: high. Cites `{SRC}`.\n",
        encoding="utf-8",
    )
    loaded = load_tool_output(path)
    assert len(loaded.findings) == 1
    assert loaded.findings[0].documents == [SRC]
