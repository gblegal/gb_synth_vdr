import textwrap
from pathlib import Path

import pytest

from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import (
    SEVERITIES,
    ConsolidationResult,
    SchemaError,
    severity_targets,
    allocate_new_finding_ids,
    consolidate_wave_incoming,
    derive_prefix_for_workstream,
    evidence_outside_sections,
    load_bearing_paths,
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


# ---------------------------------------------------------------------------
# Review 2026-08-26, B1: `consolidate_wave_incoming` upserted every key the author sent, so a
# vdr-author subagent that echoed back a rewritten `workstream`, `title` or `corroboration`
# overwrote the signed-off Gate B registry with it — silently, because `validate()` has no
# opinion on any of those values. Consolidation refines `location` and `substance`; a row that
# reaches for anything else has misunderstood its brief, and must say so rather than land.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Review item 15: subagent intake was read with bare row["id"] / row["workstream"],
# so an incoming file missing either field escaped as a raw KeyError mid-
# consolidation instead of the SchemaError every other path in this module
# raises — the thing that lets /vdr-build print one readable line rather than a
# traceback. These files are written by a vdr-author subagent, which is exactly
# the untrusted input consolidate_wave_incoming exists to police.
#
# pytest.raises(SchemaError) does not catch KeyError or TypeError, so each of
# these fails for the right reason against the old code, not on a message match.


def test_consolidation_names_the_file_and_row_for_a_findings_row_with_no_id():
    incoming = {"wave2-batch-a": {"findings": [{"title": "no id here"}]}}
    with pytest.raises(SchemaError, match=r"wave2-batch-a: findings\[0\].*'id'"):
        consolidate_wave_incoming(
            FINDINGS_DOC, incoming, {}, PREFIX_FOR_WORKSTREAM_CONSOLIDATION
        )


def test_consolidation_names_the_file_and_row_for_a_new_finding_with_no_id():
    incoming = {"wave2-batch-a": {"new_findings": [{"workstream": "environmental"}]}}
    with pytest.raises(SchemaError, match=r"wave2-batch-a: new_findings\[0\].*'id'"):
        consolidate_wave_incoming(
            FINDINGS_DOC, incoming, {}, PREFIX_FOR_WORKSTREAM_CONSOLIDATION
        )


def test_consolidation_names_the_field_for_a_new_finding_with_no_workstream():
    incoming = {"wave2-batch-a": {"new_findings": [{"id": "wave2-batch-a-NEW-1"}]}}
    with pytest.raises(SchemaError, match=r"wave2-batch-a:wave2-batch-a-NEW-1.*'workstream'"):
        consolidate_wave_incoming(
            FINDINGS_DOC, incoming, {}, PREFIX_FOR_WORKSTREAM_CONSOLIDATION
        )


def test_consolidation_skips_an_already_mapped_row_before_checking_its_workstream():
    """Pins the ORDER of the two checks, which is not interchangeable.

    A resumed build re-reads incoming files whose rows it has already allocated
    ids for. `id` must be required first, because it is the key the skip is
    decided on; `workstream` must be required after, or a rerun would start
    validating rows this run is deliberately ignoring and fail where the first
    run succeeded — breaking the idempotency property the resumed-build test
    above exists to protect.
    """
    incoming = {"wave2-batch-a": {"new_findings": [{"id": "wave2-batch-a-NEW-1"}]}}
    result = consolidate_wave_incoming(
        FINDINGS_DOC,
        incoming,
        {"wave2-batch-a-NEW-1": "ENV-2"},
        PREFIX_FOR_WORKSTREAM_CONSOLIDATION,
    )
    assert result.new_mapping == {}


def test_consolidation_rejects_an_incoming_row_that_is_not_a_mapping():
    # `findings: [foo]` parses to a list of strings. On a string row,
    # `"id" not in row` is a substring test, so _require alone would let it
    # through to `row["id"]` and a TypeError.
    incoming = {"wave2-batch-a": {"findings": ["ENV-1"]}}
    with pytest.raises(SchemaError, match=r"wave2-batch-a: findings\[0\].*not a mapping"):
        consolidate_wave_incoming(
            FINDINGS_DOC, incoming, {}, PREFIX_FOR_WORKSTREAM_CONSOLIDATION
        )


FINDINGS_DOC_WITH_CORROBORATION = {
    "schema_version": 1,
    "room": "Project Testbed",
    "findings": [
        {
            "id": "IP-1",
            "title": "Founder IP never assigned",
            "severity": "critical",
            "workstream": "ip",
            "multi_document": True,
            "source": "06_ip-it/6.1_registrations/6.1.1_trade-marks.md",
            "corroboration": ["06_ip-it/6.2_assignments/6.2.1_founder-deed.md"],
            "substance": "Seed finding.",
        }
    ],
}


def test_consolidate_wave_incoming_rejects_a_row_that_rewrites_a_gate_b_field():
    """The observed corruption: an author returned the ID prefix as the workstream and a title
    of its own. Neither is caught by `validate()` — `workstream` is a free-form string in the
    schema — so the rewritten registry would have shipped, and a wrong `workstream` also feeds
    `derive_prefix_for_workstream`'s correspondence cross-check.
    """
    incoming_docs = {
        "wave1-batch-a": {
            "findings": [
                {
                    "id": "ENV-1",
                    "workstream": "ENV",
                    "title": "Author retitled it",
                    "location": "Condition 3",
                    "substance": "Refined substance.",
                }
            ]
        }
    }
    with pytest.raises(SchemaError, match=r"ENV-1.*'title', 'workstream'"):
        consolidate_wave_incoming(FINDINGS_DOC, incoming_docs, {}, {})


def test_consolidate_wave_incoming_leaves_the_registry_untouched_when_it_rejects():
    incoming_docs = {
        "wave1-batch-a": {"findings": [{"id": "ENV-1", "workstream": "ENV"}]}
    }
    with pytest.raises(SchemaError):
        consolidate_wave_incoming(FINDINGS_DOC, incoming_docs, {}, {})
    assert FINDINGS_DOC["findings"][0]["workstream"] == "environmental"


def test_consolidate_wave_incoming_rejects_a_string_valued_corroboration():
    """The loud half of the same bug. A string where the registry holds a list survives
    consolidation, then loads as a character list — `evidence_paths()` returns
    ['...', 'b', '.', 'm', 'd'] and `build_flagged_tree` raises far from the cause.
    """
    incoming_docs = {
        "wave1-batch-a": {
            "findings": [
                {
                    "id": "IP-1",
                    "corroboration": "06_ip-it/6.2_assignments/6.2.1_founder-deed.md",
                    "substance": "Refined substance.",
                }
            ]
        }
    }
    with pytest.raises(SchemaError, match=r"IP-1.*'corroboration'"):
        consolidate_wave_incoming(FINDINGS_DOC_WITH_CORROBORATION, incoming_docs, {}, {})


def test_consolidate_wave_incoming_rejects_a_gate_b_field_the_registry_does_not_hold():
    """A key absent from the master row is still a Gate B field the author does not own.
    Dropping it silently would be the same defect one layer quieter: the author believes it
    added a cross-link, the registry never hears about it, and nothing says otherwise.
    """
    incoming_docs = {
        "wave1-batch-a": {
            "findings": [{"id": "ENV-1", "cross_links": ["FIN-9"], "substance": "Refined."}]
        }
    }
    with pytest.raises(SchemaError, match=r"ENV-1.*'cross_links'"):
        consolidate_wave_incoming(FINDINGS_DOC, incoming_docs, {}, {})


def test_consolidate_wave_incoming_accepts_a_row_carrying_only_location_and_substance():
    incoming_docs = {
        "wave1-batch-a": {
            "findings": [
                {"id": "ENV-1", "location": "Condition 3", "substance": "Refined substance."}
            ]
        }
    }
    result = consolidate_wave_incoming(FINDINGS_DOC, incoming_docs, {}, {})
    (row,) = result.findings_doc["findings"]
    assert row["location"] == "Condition 3"
    assert row["substance"] == "Refined substance."
    assert row["title"] == "Existing environmental finding"
    assert row["workstream"] == "environmental"


def test_load_bearing_paths_covers_findings_and_distractors(tmp_path):
    """Review 2026-08-26, B2. What `/vdr-build` must author first is every document the
    answer key depends on — a finding's `source` and `corroboration`, and BOTH ends of a
    distractor, since a trap whose resolving document does not exist yet is a trap that
    reads as a real finding.
    """
    findings = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    distractors = load_distractors(write(tmp_path, "distractors.yaml", DISTRACTORS))
    paths = load_bearing_paths(findings, distractors)

    assert findings.all_evidence_paths() <= paths
    for distractor in distractors:
        assert distractor.location in paths
        assert distractor.resolution in paths


def test_load_bearing_paths_with_no_distractors_is_just_the_evidence(tmp_path):
    findings = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    assert load_bearing_paths(findings, []) == findings.all_evidence_paths()


def test_evidence_outside_sections_names_a_path_in_a_dropped_section(tmp_path):
    findings = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    # FINDINGS plants ENV-1 in 11_environmental-hs and FIN-3 in 02_financial.
    # A room that dropped 11_environmental-hs cannot hold ENV-1's source.
    offenders = evidence_outside_sections(findings, [], ["02_financial"])
    assert offenders == [
        "11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md"
    ]


def test_evidence_outside_sections_is_empty_when_every_section_is_declared(tmp_path):
    findings = load_findings(write(tmp_path, "findings.yaml", FINDINGS))
    declared = sorted({p.split("/")[0] for p in findings.all_evidence_paths()})
    assert evidence_outside_sections(findings, [], declared) == []


# A fixture built for this check specifically: the shipped DISTRACTORS puts DX-1's
# two ends in one section, which cannot tell a both-ends implementation from a
# location-only one. Here the ends are in DIFFERENT sections, and the finding's
# corroboration deliberately collides with the distractor's location.
SPLIT_FINDINGS = textwrap.dedent(
    """
    schema_version: 1
    room: "Project Testbed"
    findings:
      - id: ENV-1
        title: Contamination under-provisioned
        severity: critical
        workstream: environmental
        multi_document: true
        source: 11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md
        location: "Table 4"
        corroboration:
          - 02_financial/2.4_provisions/2.4.1_provision.md
        substance: Estimate far above the provision.
    """
).strip()

SPLIT_DISTRACTORS = textwrap.dedent(
    """
    distractors:
      - id: DX-1
        title: Alarming-looking notice, fully remediated
        shape_matches: ENV-1
        location: 02_financial/2.4_provisions/2.4.1_provision.md
        resolution: 15_litigation/15.3_correspondence/15.3.1_closure-letter.md
    """
).strip()


def test_evidence_outside_sections_flags_a_resolution_whose_own_section_is_dropped(tmp_path):
    # The end that is easy to forget. A trap whose RESOLVING document was dropped
    # is not a trap — it reads as a real finding, and scores a review tool against
    # evidence the room does not contain. Here only the resolution's section is
    # dropped, so a location-only implementation returns [] and fails this.
    findings = load_findings(write(tmp_path, "findings.yaml", SPLIT_FINDINGS))
    distractors = load_distractors(write(tmp_path, "distractors.yaml", SPLIT_DISTRACTORS))

    offenders = evidence_outside_sections(
        findings, distractors, ["11_environmental-hs", "02_financial"]
    )

    assert offenders == ["15_litigation/15.3_correspondence/15.3.1_closure-letter.md"]


def test_evidence_outside_sections_reports_a_shared_path_only_once(tmp_path):
    # 2.4.1_provision.md is BOTH the finding's corroboration and the distractor's
    # location. Dropping 02_financial must surface it once, not twice.
    findings = load_findings(write(tmp_path, "findings.yaml", SPLIT_FINDINGS))
    distractors = load_distractors(write(tmp_path, "distractors.yaml", SPLIT_DISTRACTORS))

    offenders = evidence_outside_sections(
        findings, distractors, ["11_environmental-hs", "15_litigation"]
    )

    assert offenders == ["02_financial/2.4_provisions/2.4.1_provision.md"]


def test_evidence_outside_sections_returns_paths_in_sorted_order(tmp_path):
    # Sorted so the same room always reports the same list in the same order —
    # asserted against a KNOWN multi-element expectation, not against sorted() of
    # whatever came back, which would be true of any return at all.
    findings = load_findings(write(tmp_path, "findings.yaml", SPLIT_FINDINGS))
    distractors = load_distractors(write(tmp_path, "distractors.yaml", SPLIT_DISTRACTORS))

    offenders = evidence_outside_sections(findings, distractors, [])

    assert offenders == [
        "02_financial/2.4_provisions/2.4.1_provision.md",
        "11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md",
        "15_litigation/15.3_correspondence/15.3.1_closure-letter.md",
    ]


# ---------------------------------------------------------------------------
# Review 2026-08-26, S5. /vdr-findings asked for "roughly a 1 : 3 : 4 : 3 split
# across critical / high / medium / low". That needs at least 11 findings, and XS
# budgets 4. Scaled down it reads 0.36 / 1.1 / 1.45 / 1.1, which is not a split
# anyone can write, so the skill left the author to improvise the one case its
# smallest preset always hits. The ratio is a function now, defined at every
# preset the tool ships.
# ---------------------------------------------------------------------------


def test_severity_targets_sums_to_the_budget_at_every_shipped_preset():
    from synthvdr.slots import SIZE_PRESETS

    for preset in SIZE_PRESETS.values():
        targets = severity_targets(preset.findings)
        assert sum(targets.values()) == preset.findings, preset.name


def test_severity_targets_gives_every_band_at_least_one_finding():
    from synthvdr.slots import SIZE_PRESETS

    for preset in SIZE_PRESETS.values():
        targets = severity_targets(preset.findings)
        assert set(targets) == set(SEVERITIES), preset.name
        assert min(targets.values()) >= 1, f"{preset.name}: {targets}"


def test_severity_targets_at_xs_is_one_per_band():
    # The case that could not be expressed at all before: four findings, four
    # bands, which is also the widest scoring signal available at that size.
    assert severity_targets(4) == {"critical": 1, "high": 1, "medium": 1, "low": 1}


def test_severity_targets_holds_the_ratio_where_there_is_room_for_it():
    targets = severity_targets(110)
    assert targets == {"critical": 10, "high": 30, "medium": 40, "low": 30}


def test_severity_targets_adds_to_medium_first():
    # Stated in the skill as the tie-break rule, so it must be the real one.
    assert severity_targets(12)["medium"] > severity_targets(11)["medium"]


def test_severity_targets_refuses_a_budget_smaller_than_the_bands():
    with pytest.raises(SchemaError, match="four severity bands"):
        severity_targets(3)
