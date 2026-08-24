import textwrap

import pytest

from synthvdr.schema import (
    SchemaError,
    load_distractors,
    load_findings,
    render_findings_md,
    validate,
)

FINDINGS = textwrap.dedent(
    """
    schema_version: 1
    room: "Project Testbed"
    findings:
      - id: ENV-1
        title: Site contamination under-provisioned
        severity: critical
        workstream: environmental
        multi_document: true
        source: 11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md
        location: "Table 4"
        corroboration:
          - 02_financial/2.4_provisions/2.4.1_environmental-provision.md
        substance: Estimate far above the provision.
        cross_links: [FIN-3]
      - id: FIN-3
        title: Provision below the underlying estimate
        severity: high
        workstream: financial
        multi_document: false
        source: 02_financial/2.4_provisions/2.4.1_environmental-provision.md
        location: "Note 18"
        corroboration: []
        substance: The provision is not supported.
        cross_links: [ENV-1]
    """
).strip()

DISTRACTORS = textwrap.dedent(
    """
    distractors:
      - id: DX-1
        title: Alarming-looking notice, fully remediated
        shape_matches: ENV-1
        location: 11_environmental-hs/11.4_hse-notices/11.4.1_improvement-notice.md
        resolution: 11_environmental-hs/11.4_hse-notices/11.4.2_closure-letter.md
    """
).strip()


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_loads_findings_and_indexes_by_id(tmp_path):
    fs = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    assert set(fs.by_id) == {"ENV-1", "FIN-3"}
    assert fs.by_id["ENV-1"].severity == "critical"


def test_evidence_paths_put_source_first(tmp_path):
    fs = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    paths = fs.by_id["ENV-1"].evidence_paths()
    assert paths[0].endswith("11.2.1_phase-2.md")
    assert len(paths) == 2


def test_valid_key_produces_no_errors(tmp_path):
    fs = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    dx = load_distractors(write(tmp_path, "distractors.yaml", DISTRACTORS))
    assert validate(fs, dx) == []


def test_duplicate_ids_rejected(tmp_path):
    with pytest.raises(SchemaError, match="duplicate"):
        load_findings(write(tmp_path, "dupe.yaml", FINDINGS.replace("FIN-3", "ENV-1")))


def test_unknown_severity_rejected(tmp_path):
    bad = FINDINGS.replace("severity: critical", "severity: catastrophic")
    with pytest.raises(SchemaError, match="severity"):
        load_findings(write(tmp_path, "bad.yaml", bad))


def test_dangling_cross_link_is_an_error(tmp_path):
    bad = FINDINGS.replace("cross_links: [FIN-3]", "cross_links: [NOPE-9]")
    fs = load_findings(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, [])
    assert any("NOPE-9" in e for e in errors)


def test_multi_document_finding_needs_corroboration(tmp_path):
    bad = FINDINGS.replace(
        "    corroboration:\n      - 02_financial/2.4_provisions/2.4.1_environmental-provision.md",
        "    corroboration: []",
    )
    assert bad != FINDINGS, "fixture mutation must actually change the corroboration block"
    fs = load_findings(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, [])
    assert any("multi_document" in e for e in errors)


def test_corroboration_without_multi_document_is_an_error(tmp_path):
    bad = FINDINGS.replace(
        "corroboration: []",
        "corroboration: [02_financial/2.4_provisions/2.4.1_environmental-provision.md]",
    )
    assert bad != FINDINGS, "fixture mutation must actually change the corroboration block"
    fs = load_findings(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, [])
    assert any("multi_document" in e for e in errors)


def test_distractor_resolution_must_differ_from_location(tmp_path):
    bad = DISTRACTORS.replace(
        "resolution: 11_environmental-hs/11.4_hse-notices/11.4.2_closure-letter.md",
        "resolution: 11_environmental-hs/11.4_hse-notices/11.4.1_improvement-notice.md",
    )
    fs = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    dx = load_distractors(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, dx)
    assert any("DX-1" in e for e in errors)


def test_rendered_markdown_names_every_finding(tmp_path):
    fs = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    md = render_findings_md(fs, "Project Testbed")
    assert md.startswith("#")
    # Every finding must get its own heading — not merely be mentioned in
    # passing (e.g. via another finding's cross-links line).
    for finding in fs.findings:
        assert f"## {finding.id} —" in md
