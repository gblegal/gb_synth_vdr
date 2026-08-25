import textwrap
from pathlib import Path

import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import (
    ConsolidationResult,
    SchemaError,
    allocate_new_finding_ids,
    consolidate_wave_incoming,
    derive_prefix_for_workstream,
    load_distractors,
    load_findings,
    parse_new_findings_ledger,
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


# ---------------------------------------------------------------------------
# allocate_new_finding_ids — deterministic mid-authoring ID assignment (Task 18 fix round 1:
# spec section 5.1 requires findings discovered during authoring to be appended with "the
# next free number in the owning workstream," but /vdr-build fans a wave out across parallel
# authors with no channel between them, so the allocation itself must be a single, sorted,
# deterministic pass — never something an author picks for itself.
# ---------------------------------------------------------------------------

PREFIX_FOR_WORKSTREAM = {"environmental": "ENV", "operations": "OPS", "financial": "FIN"}


def test_allocate_new_finding_ids_continues_from_the_highest_existing_number():
    mapping = allocate_new_finding_ids(
        existing_ids={"ENV-1", "ENV-2", "FIN-1"},
        prefix_for_workstream=PREFIX_FOR_WORKSTREAM,
        discoveries=[("wave2-batch-a", "wave2-batch-a-NEW-1", "environmental")],
    )
    assert mapping == {"wave2-batch-a-NEW-1": "ENV-3"}


def test_allocate_new_finding_ids_orders_by_label_then_provisional_id():
    discoveries = [
        ("wave2-batch-a", "wave2-batch-a-NEW-2", "environmental"),
        ("wave2-batch-a", "wave2-batch-a-NEW-1", "environmental"),
    ]
    mapping = allocate_new_finding_ids(
        existing_ids=set(), prefix_for_workstream=PREFIX_FOR_WORKSTREAM, discoveries=discoveries
    )
    # NEW-1 sorts before NEW-2 within the same label, so it must claim the lower number
    # regardless of the order the two rows were passed in.
    assert mapping["wave2-batch-a-NEW-1"] == "ENV-1"
    assert mapping["wave2-batch-a-NEW-2"] == "ENV-2"


def test_allocate_new_finding_ids_is_order_independent_across_reruns():
    """Two runs over the SAME intake — the same set of discoveries, gathered from two
    parallel authors' incoming files — must produce the same ids no matter which order the
    files happened to be read in. Simulated here by reversing the input list, which is the
    cheapest proxy for "a rerun that globbed the incoming directory in a different order."
    """
    discoveries = [
        ("wave2-batch-b", "wave2-batch-b-NEW-1", "operations"),
        ("wave2-batch-a", "wave2-batch-a-NEW-1", "environmental"),
        ("wave2-batch-a", "wave2-batch-a-NEW-2", "environmental"),
    ]
    existing_ids = {"ENV-1", "ENV-2", "OPS-3"}
    forward = allocate_new_finding_ids(existing_ids, PREFIX_FOR_WORKSTREAM, discoveries)
    backward = allocate_new_finding_ids(
        existing_ids, PREFIX_FOR_WORKSTREAM, list(reversed(discoveries))
    )
    assert forward == backward
    assert forward == {
        "wave2-batch-a-NEW-1": "ENV-3",
        "wave2-batch-a-NEW-2": "ENV-4",
        "wave2-batch-b-NEW-1": "OPS-4",
    }


def test_allocate_new_finding_ids_raises_on_unknown_workstream():
    with pytest.raises(SchemaError):
        allocate_new_finding_ids(
            existing_ids=set(),
            prefix_for_workstream=PREFIX_FOR_WORKSTREAM,
            discoveries=[("wave1-batch-a", "wave1-batch-a-NEW-1", "esg")],
        )


# ---------------------------------------------------------------------------
# derive_prefix_for_workstream — Task 18 fix round 2, F3: the workstream <-> prefix
# correspondence in FINDING_PREFIXES is positional and unenforced by format; a reordered
# list must be caught, not silently misattributed.
# ---------------------------------------------------------------------------

EXISTING_FOR_PREFIX_TESTS = textwrap.dedent(
    """
    findings:
      - id: ENV-1
        title: Existing environmental finding
        severity: critical
        workstream: environmental
        multi_document: false
        source: 11_environmental-hs/11.1_permits/11.1.1_permit.md
        substance: Seed finding.
      - id: FIN-1
        title: Existing financial finding
        severity: high
        workstream: financial
        multi_document: false
        source: 02_financial/2.1_accounts/2.1.1_accounts.md
        substance: Seed finding.
    """
).strip()


def _existing(tmp_path):
    return load_findings(write(tmp_path, "existing.yaml", EXISTING_FOR_PREFIX_TESTS)).findings


def test_derive_prefix_for_workstream_accepts_the_correct_correspondence(tmp_path):
    mapping = derive_prefix_for_workstream(
        ["environmental", "financial", "operations"], ["ENV", "FIN", "OPS"], _existing(tmp_path)
    )
    assert mapping == {"environmental": "ENV", "financial": "FIN", "operations": "OPS"}


def test_derive_prefix_for_workstream_rejects_a_length_mismatch(tmp_path):
    with pytest.raises(SchemaError, match="2 token"):
        derive_prefix_for_workstream(
            ["environmental", "financial", "operations"], ["ENV", "FIN"], _existing(tmp_path)
        )


def test_derive_prefix_for_workstream_rejects_a_reordered_list(tmp_path):
    """Same length, wrong pairing — the exact silent-misattribution case, caught only because
    an existing finding's own id (ENV-1, workstream environmental) disagrees with what the
    swapped order would assign it (FIN).
    """
    with pytest.raises(SchemaError, match="reordered"):
        derive_prefix_for_workstream(
            ["environmental", "financial"], ["FIN", "ENV"], _existing(tmp_path)
        )


def test_derive_prefix_for_workstream_does_not_block_a_workstream_with_no_existing_finding(
    tmp_path,
):
    # "operations" has no existing finding to cross-check against — only length is checked.
    mapping = derive_prefix_for_workstream(
        ["environmental", "financial", "operations"], ["ENV", "FIN", "OPS"], _existing(tmp_path)
    )
    assert mapping["operations"] == "OPS"


def test_derive_prefix_for_workstream_against_the_real_domain_pack_and_room_conf():
    """Final review, F2: before this, no test ever ran derive_prefix_for_workstream against
    the REAL domain pack and a REAL room.conf together — the shipped fixture room
    (fixtures/xs-room/room.conf) declared only 4 FINDING_PREFIXES tokens against the domain
    pack's 20 workstreams, so this call raised a length SchemaError immediately and the
    genuine end-to-end path (`/vdr-build`'s own Step 3 snippet) was never exercised by
    anything. The fixture's FINDING_PREFIXES was widened to the full 20 tokens, in
    `pack.workstreams()`'s order, specifically so this path is covered.
    """
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    room_conf_path = (
        Path(__file__).resolve().parent.parent / "fixtures" / "xs-room" / "room.conf"
    )
    conf = load_room_conf(room_conf_path)
    mapping = derive_prefix_for_workstream(
        pack.workstreams(), conf.get("FINDING_PREFIXES").split("|"), existing_findings=()
    )
    assert mapping["corporate"] == "CORP"
    assert mapping["financial"] == "FIN"
    assert mapping["commercial"] == "COMM"
    assert mapping["environmental"] == "ENV"
    assert len(mapping) == 20


# ---------------------------------------------------------------------------
# parse_new_findings_ledger / consolidate_wave_incoming — Task 18 fix round 2, F2:
# consolidation must be idempotent across a resumed build, because a wave only advances past
# its gate on a pass, and a gate failure after a successful consolidation must not double
# every discovery on the next attempt.
# ---------------------------------------------------------------------------


def test_parse_new_findings_ledger_reads_the_table():
    text = (
        "# Build status\n\n## New findings\n\n"
        "| Provisional id | Final id | Workstream |\n|---|---|---|\n"
        "| wave2-batch-a-NEW-1 | ENV-2 | environmental |\n"
    )
    assert parse_new_findings_ledger(text) == {"wave2-batch-a-NEW-1": "ENV-2"}


def test_parse_new_findings_ledger_is_empty_for_a_fresh_build():
    assert parse_new_findings_ledger("") == {}
    assert parse_new_findings_ledger("# Build status\n\n## Waves completed\n") == {}


FINDINGS_DOC = {
    "schema_version": 1,
    "room": "Project Testbed",
    "findings": [
        {
            "id": "ENV-1",
            "title": "Existing environmental finding",
            "severity": "critical",
            "workstream": "environmental",
            "multi_document": False,
            "source": "11_environmental-hs/11.1_permits/11.1.1_permit.md",
            "substance": "Seed finding.",
        }
    ],
}

PREFIX_FOR_WORKSTREAM_CONSOLIDATION = {"environmental": "ENV", "operations": "OPS"}


def _wave2_incoming_docs():
    return {
        "wave2-batch-a": {
            "new_findings": [
                {
                    "id": "wave2-batch-a-NEW-1",
                    "title": "Undisclosed related-party balance",
                    "severity": "high",
                    "workstream": "environmental",
                    "multi_document": False,
                    "source": "11_environmental-hs/11.4_permits/11.4.2_variation-notice.md",
                    "location": "Condition 7",
                    "substance": "A permit variation notice tightens a discharge limit.",
                }
            ]
        },
        "wave2-batch-b": {
            "new_findings": [
                {
                    "id": "wave2-batch-b-NEW-1",
                    "title": "Single-source supplier dependency",
                    "severity": "high",
                    "workstream": "operations",
                    "multi_document": False,
                    "source": "14_operations/14.2_supply-chain/14.2.5_supplier-list.md",
                    "location": "Row 3",
                    "substance": "One supplier accounts for the majority of annual spend.",
                }
            ]
        },
    }


def test_consolidate_wave_incoming_allocates_new_discoveries():
    result = consolidate_wave_incoming(
        FINDINGS_DOC, _wave2_incoming_docs(), {}, PREFIX_FOR_WORKSTREAM_CONSOLIDATION
    )
    assert result.new_mapping == {
        "wave2-batch-a-NEW-1": "ENV-2",
        "wave2-batch-b-NEW-1": "OPS-1",
    }
    ids = {row["id"] for row in result.findings_doc["findings"]}
    assert ids == {"ENV-1", "ENV-2", "OPS-1"}


def test_consolidate_wave_incoming_is_idempotent_across_a_resumed_build():
    """The exact bug the coordinator reproduced: a wave whose gate fails AFTER a successful
    consolidation must not double every discovery when /vdr-build resumes and consolidates
    the same, untouched _key/incoming/*.yaml content again. Simulated here by feeding the
    first call's `new_mapping` back in as `already_mapped`, exactly as parsing it out of the
    persisted `_key/build-status.md` ledger would.
    """
    incoming_docs = _wave2_incoming_docs()

    first = consolidate_wave_incoming(
        FINDINGS_DOC, incoming_docs, {}, PREFIX_FOR_WORKSTREAM_CONSOLIDATION
    )
    assert first.new_mapping == {
        "wave2-batch-a-NEW-1": "ENV-2",
        "wave2-batch-b-NEW-1": "OPS-1",
    }

    # Second attempt: same untouched incoming files, findings.yaml already updated from the
    # first call, already_mapped now carries what the ledger would already have recorded.
    second = consolidate_wave_incoming(
        first.findings_doc, incoming_docs, first.new_mapping, PREFIX_FOR_WORKSTREAM_CONSOLIDATION
    )
    assert second.new_mapping == {}, "a rerun over the same intake must allocate nothing new"
    assert second.findings_doc == first.findings_doc, (
        "a rerun must leave findings_doc unchanged — no duplicate ENV-4/OPS-2 entries"
    )
    ids = [row["id"] for row in second.findings_doc["findings"]]
    assert len(ids) == len(set(ids)) == 3, "no duplicate finding ids after the rerun"


def test_consolidate_wave_incoming_upserts_findings_refinements():
    incoming_docs = {
        "wave2-batch-a": {
            "findings": [
                {
                    "id": "ENV-1",
                    "title": "Existing environmental finding",
                    "severity": "critical",
                    "workstream": "environmental",
                    "multi_document": False,
                    "source": "11_environmental-hs/11.1_permits/11.1.1_permit.md",
                    "location": "Condition 3, second paragraph",
                    "substance": "Refined substance now that the document exists.",
                }
            ]
        }
    }
    result = consolidate_wave_incoming(FINDINGS_DOC, incoming_docs, {}, {})
    (row,) = result.findings_doc["findings"]
    assert row["location"] == "Condition 3, second paragraph"
    assert row["substance"] == "Refined substance now that the document exists."


def test_consolidate_wave_incoming_rejects_a_findings_row_not_in_the_gate_b_registry():
    incoming_docs = {"wave2-batch-a": {"findings": [{"id": "NOT-REGISTERED"}]}}
    with pytest.raises(SchemaError, match="NOT-REGISTERED"):
        consolidate_wave_incoming(FINDINGS_DOC, incoming_docs, {}, {})
