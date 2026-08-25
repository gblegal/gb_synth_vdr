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
    ProvenanceError,
    ToolFinding,
    ToolOutput,
    check_provenance,
    load_tool_output,
    prematch,
    render_scorecard,
    score,
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
    assert matched == {0: "ENV-1"}
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
