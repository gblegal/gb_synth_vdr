from synthvdr.roomconf import load_room_conf
from synthvdr.schema import Finding, FindingSet
from synthvdr.twin import (
    annotation_block,
    build_flagged_tree,
    derive_twin,
    is_valid_twin,
    split_twin,
)

FLAG = "Key diligence points"
BLIND = "# Phase 2 report\n\nRemediation is estimated at GBP 18.6m.\n"

CONF_TEMPLATE = '''ROOM_CODENAME="Project Testbed"
INDEX_TOTAL=20
BLIND_TOTAL=20
FLAGGED_TOTAL=20
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="CORP|ENV|FIN"
EXPECTED_KDP_CARRIERS=4
SECTION_DIRS="01_corporate 11_environmental-hs"
'''


def finding(fid="ENV-1", source=None, corroboration=None, multi_document=True):
    return Finding(
        id=fid,
        title="Site contamination under-provisioned",
        severity="critical",
        workstream="environmental",
        multi_document=multi_document,
        source=source or "11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md",
        location="Table 4",
        substance="Estimate far above the provision.",
        corroboration=(
            corroboration
            if corroboration is not None
            else ["02_financial/2.4_provisions/2.4.1_environmental-provision.md"]
        ),
        cross_links=["FIN-3"],
    )


def make_room(tmp_path, conf_text=CONF_TEMPLATE):
    """A room directory with a room.conf and an empty blind tree, ready for
    build_flagged_tree tests to populate with files under data-room/.
    """
    room = tmp_path
    conf_path = room / "room.conf"
    conf_path.write_text(conf_text)
    (room / "data-room").mkdir()
    return room, load_room_conf(conf_path)


def write_blind(room, rel, content):
    path = room / "data-room" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


def test_benign_twin_is_byte_identical():
    assert derive_twin(BLIND, None) == BLIND


def test_carrier_twin_is_blind_plus_trailing_block():
    block = annotation_block([finding()], FLAG)
    twin = derive_twin(BLIND, block)
    assert twin.startswith(BLIND)
    assert twin.endswith(block)


def test_block_names_the_flag_string_and_the_finding():
    block = annotation_block([finding()], FLAG)
    assert f"## {FLAG}" in block
    assert "ENV-1" in block


def test_split_twin_recovers_body_and_block():
    block = annotation_block([finding()], FLAG)
    body, recovered = split_twin(derive_twin(BLIND, block), FLAG)
    assert body == BLIND
    assert recovered == block


def test_split_twin_of_a_benign_file_returns_no_block():
    body, block = split_twin(BLIND, FLAG)
    assert body == BLIND and block is None


def test_is_valid_twin_accepts_identical_and_appended():
    block = annotation_block([finding()], FLAG)
    assert is_valid_twin(BLIND, BLIND, FLAG)
    assert is_valid_twin(BLIND, derive_twin(BLIND, block), FLAG)


def test_is_valid_twin_rejects_a_modified_body():
    tampered = BLIND.replace("18.6m", "4.2m")
    assert not is_valid_twin(BLIND, tampered, FLAG)


def test_is_valid_twin_rejects_a_modified_body_even_with_a_well_formed_block():
    # A bare tampered body (no block at all) is rejected purely because
    # split_twin finds no marker. The sharper case is a carrier whose body
    # was altered but which still carries a plausible-looking block — that
    # must be caught by the body-equality check itself, not by the absence
    # of a block.
    tampered = BLIND.replace("18.6m", "4.2m")
    block = annotation_block([finding()], FLAG)
    assert not is_valid_twin(BLIND, derive_twin(tampered, block), FLAG)


def test_derive_twin_is_idempotent():
    block = annotation_block([finding()], FLAG)
    once = derive_twin(BLIND, block)
    assert derive_twin(split_twin(once, FLAG)[0], block) == once


def test_split_twin_finds_the_last_heading_occurrence_when_the_body_has_a_decoy():
    # Blind-first discipline forbids a blind document from containing the flag
    # heading, but split_twin must still behave sanely if it ever happened —
    # it must recover the *appended* block, not an earlier look-alike in the
    # body. rfind (not find) is what makes that true.
    decoy_body = f"# Report\n\n## {FLAG}\n\nAn unrelated paragraph that happens to echo the heading.\n"
    block = annotation_block([finding()], FLAG)
    twin = derive_twin(decoy_body, block)
    body, recovered = split_twin(twin, FLAG)
    assert body == decoy_body
    assert recovered == block


# ---------------------------------------------------------------------------
# build_flagged_tree: the filesystem writer. Non-markdown files must never be
# annotated, a document evidencing two findings gets exactly one combined
# block, and the delete-and-rebuild step must only ever touch the configured
# FLAGGED_TREE — never its parent or the blind tree.
# ---------------------------------------------------------------------------


def test_non_markdown_carrier_is_copied_byte_for_byte_and_never_annotated(tmp_path):
    room, conf = make_room(tmp_path)
    csv_bytes = b"period,amount\nQ1,18600000\n"
    write_blind(room, "02_financial/2.4_provisions/register.csv", csv_bytes)
    f = finding(source="02_financial/2.4_provisions/register.csv", corroboration=[], multi_document=False)
    findings = FindingSet(findings=[f], room="Project Testbed")

    report = build_flagged_tree(room, conf, findings)

    flagged_csv = room / "_key/flagged/02_financial/2.4_provisions/register.csv"
    assert flagged_csv.read_bytes() == csv_bytes
    assert report.written == 1
    assert report.carriers == 0
    assert report.identical == 1


def test_document_evidencing_two_findings_gets_one_combined_block(tmp_path):
    room, conf = make_room(tmp_path)
    body = "# Phase 2 report\n\nRemediation is estimated at GBP 18.6m.\n"
    write_blind(room, "11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md", body)
    env = finding(fid="ENV-1", source="11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md", corroboration=[], multi_document=False)
    fin = finding(
        fid="FIN-3",
        source="02_financial/2.4_provisions/2.4.1_environmental-provision.md",
        corroboration=["11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md"],
    )
    write_blind(room, "02_financial/2.4_provisions/2.4.1_environmental-provision.md", "# Provisions note\n")
    findings = FindingSet(findings=[env, fin], room="Project Testbed")

    report = build_flagged_tree(room, conf, findings)

    flagged = (room / "_key/flagged/11_environmental-hs/11.2_site-reports/11.2.1_phase-2.md").read_text(
        encoding="utf-8"
    )
    # phase-2.md is evidence for both findings: it must carry exactly one
    # block naming both, not one block per finding.
    assert flagged.count(f"## {FLAG}") == 1
    assert "ENV-1" in flagged and "FIN-3" in flagged
    assert flagged == derive_twin(body, annotation_block([env, fin], FLAG))
    # environmental-provision.md is FIN-3's own source — a second, separate
    # carrier document, annotated with FIN-3 alone.
    provision_flagged = (
        room / "_key/flagged/02_financial/2.4_provisions/2.4.1_environmental-provision.md"
    ).read_text(encoding="utf-8")
    assert "FIN-3" in provision_flagged and "ENV-1" not in provision_flagged
    assert report.written == 2
    assert report.carriers == 2
    assert report.identical == 0


def test_build_flagged_tree_only_touches_the_configured_flagged_root(tmp_path):
    room, conf = make_room(tmp_path)
    write_blind(room, "01_corporate/1.1_articles/1.1.1_articles.md", "# Articles\n")

    # A stale leftover under the flagged tree must be removed on rebuild...
    stale = room / "_key/flagged/stale.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("leftover from a previous run\n")
    # ...but a sibling file under KEY_ROOT, outside FLAGGED_TREE, must survive.
    sibling = room / "_key/manifest.txt"
    sibling.write_text("keep-me\n")

    findings = FindingSet(findings=[], room="Project Testbed")
    build_flagged_tree(room, conf, findings)

    assert not stale.exists()
    assert sibling.read_text(encoding="utf-8") == "keep-me\n"
    assert (room / "data-room/01_corporate/1.1_articles/1.1.1_articles.md").read_text(
        encoding="utf-8"
    ) == "# Articles\n"
