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


def test_rendered_markdown_orders_same_severity_findings_by_id(tmp_path):
    tied = textwrap.dedent(
        """
        findings:
          - id: FIN-9
            title: Should sort after FIN-2 despite appearing first
            severity: high
            workstream: financial
            multi_document: false
            source: 02_financial/2.1_a.md
            location: "Note 1"
            corroboration: []
            substance: Some substance.
            cross_links: []
          - id: FIN-2
            title: Should sort before FIN-9 despite appearing second
            severity: high
            workstream: financial
            multi_document: false
            source: 02_financial/2.2_b.md
            location: "Note 2"
            corroboration: []
            substance: Some other substance.
            cross_links: []
        """
    ).strip()
    fs = load_findings(write(tmp_path, "tied.yaml", tied))
    md = render_findings_md(fs, "Project Testbed")
    # Both findings are "high" severity; the tie-break must fall back to id,
    # not to YAML source order.
    assert md.index("## FIN-2") < md.index("## FIN-9")


# ---------------------------------------------------------------------------
# Path hygiene: source, corroboration, and distractor location/resolution
# must all be non-empty, relative, and free of parent-directory traversal.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_source",
    ["", "/etc/passwd", "11_environmental-hs/../secrets.md"],
)
def test_bad_source_path_is_rejected(tmp_path, bad_source):
    bad = FINDINGS.replace(
        "source: 11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md",
        f'source: "{bad_source}"',
    )
    assert bad != FINDINGS
    fs = load_findings(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, [])
    assert any("ENV-1" in e and "source" in e for e in errors)


@pytest.mark.parametrize(
    "bad_path",
    ["", "/etc/passwd", "02_financial/../../secrets.md"],
)
def test_bad_corroboration_path_is_rejected(tmp_path, bad_path):
    bad = FINDINGS.replace(
        "      - 02_financial/2.4_provisions/2.4.1_environmental-provision.md",
        f'      - "{bad_path}"',
    )
    assert bad != FINDINGS
    fs = load_findings(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, [])
    assert any("ENV-1" in e and "corroboration" in e for e in errors)


def test_self_corroboration_is_rejected(tmp_path):
    bad = FINDINGS.replace(
        "      - 02_financial/2.4_provisions/2.4.1_environmental-provision.md",
        "      - 11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md",
    )
    assert bad != FINDINGS
    fs = load_findings(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, [])
    assert any("ENV-1" in e and "own source" in e for e in errors)


def test_duplicate_corroboration_is_rejected(tmp_path):
    bad = FINDINGS.replace(
        "    corroboration:\n      - 02_financial/2.4_provisions/2.4.1_environmental-provision.md",
        "    corroboration:\n      - 02_financial/2.4_provisions/2.4.1_environmental-provision.md\n"
        "      - 02_financial/2.4_provisions/2.4.1_environmental-provision.md",
    )
    assert bad != FINDINGS
    fs = load_findings(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, [])
    assert any("ENV-1" in e and "duplicate" in e for e in errors)


@pytest.mark.parametrize("field", ["location", "resolution"])
@pytest.mark.parametrize(
    "bad_path",
    ["", "/etc/passwd", "11_environmental-hs/../secrets.md"],
)
def test_bad_distractor_path_is_rejected(tmp_path, field, bad_path):
    original = {
        "location": "location: 11_environmental-hs/11.4_hse-notices/11.4.1_improvement-notice.md",
        "resolution": "resolution: 11_environmental-hs/11.4_hse-notices/11.4.2_closure-letter.md",
    }[field]
    bad = DISTRACTORS.replace(original, f'{field}: "{bad_path}"')
    assert bad != DISTRACTORS
    fs = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    dx = load_distractors(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, dx)
    assert any("DX-1" in e and field in e for e in errors)


# ---------------------------------------------------------------------------
# A distractor's evidence must be genuinely benign: it must not double as a
# finding's real evidence, whether as the finding's source or as one of its
# corroborating documents.
# ---------------------------------------------------------------------------


def test_distractor_location_matching_finding_source_is_rejected(tmp_path):
    bad = DISTRACTORS.replace(
        "location: 11_environmental-hs/11.4_hse-notices/11.4.1_improvement-notice.md",
        "location: 11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md",
    )
    assert bad != DISTRACTORS
    fs = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    dx = load_distractors(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, dx)
    assert any("DX-1" in e and "ENV-1" in e for e in errors)


def test_distractor_resolution_matching_finding_source_is_rejected(tmp_path):
    bad = DISTRACTORS.replace(
        "resolution: 11_environmental-hs/11.4_hse-notices/11.4.2_closure-letter.md",
        "resolution: 02_financial/2.4_provisions/2.4.1_environmental-provision.md",
    )
    assert bad != DISTRACTORS
    fs = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    dx = load_distractors(write(tmp_path, "bad.yaml", bad))
    errors = validate(fs, dx)
    assert any("DX-1" in e and "FIN-3" in e for e in errors)


def test_distractor_location_matching_corroboration_only_path_is_rejected(tmp_path):
    # 03_commercial/.../3.1.1 is corroboration for ENV-9 but nobody's source —
    # this isolates the "appears in corroboration" branch from "equals source".
    findings_text = textwrap.dedent(
        """
        findings:
          - id: ENV-9
            title: Needs a second document to stand up
            severity: high
            workstream: environmental
            multi_document: true
            source: 11_environmental-hs/11.3_permits/11.3.1_permit.md
            location: "Clause 4"
            corroboration:
              - 03_commercial/3.1_contracts/3.1.1_supply-agreement.md
            substance: The permit conflicts with the supply commitment.
            cross_links: []
        """
    ).strip()
    distractors_text = textwrap.dedent(
        """
        distractors:
          - id: DX-9
            title: Looks like a lead, resolves to nothing
            location: 03_commercial/3.1_contracts/3.1.1_supply-agreement.md
            resolution: 03_commercial/3.1_contracts/3.1.2_amendment.md
        """
    ).strip()
    fs = load_findings(write(tmp_path, "findings.yaml", findings_text))
    dx = load_distractors(write(tmp_path, "distractors.yaml", distractors_text))
    errors = validate(fs, dx)
    assert any("DX-9" in e and "ENV-9" in e for e in errors)


# ---------------------------------------------------------------------------
# Loader robustness: only SchemaError should ever escape load_findings and
# load_distractors, never a raw PyYAML or Python exception.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("loader", [load_findings, load_distractors])
def test_malformed_yaml_raises_schema_error(tmp_path, loader):
    path = write(tmp_path, "bad.yaml", "findings: [unterminated")
    with pytest.raises(SchemaError):
        loader(path)


@pytest.mark.parametrize(
    "loader, top_key",
    [(load_findings, "findings"), (load_distractors, "distractors")],
)
def test_non_mapping_row_raises_schema_error(tmp_path, loader, top_key):
    text = f"{top_key}:\n  - invalid row\n"
    path = write(tmp_path, "bad.yaml", text)
    with pytest.raises(SchemaError):
        loader(path)
