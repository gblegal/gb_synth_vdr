"""Structural gates: counts, canon, twinning, cross-references."""

from __future__ import annotations

from ..index_build import count_slots, render_index
from .runner import fail, ok, skip


def gate_01_index(ctx):
    index_path = ctx.room / "index.md"
    index_src = ctx.key_root / "index-src"
    if not index_path.is_file() or not index_src.is_dir():
        return skip("1", "index count and regeneration", "index.md or _key/index-src/ absent")
    text = index_path.read_text(encoding="utf-8")
    expected = ctx.conf.get_int("INDEX_TOTAL")
    found = count_slots(text)
    if found != expected:
        return fail("1", "index count and regeneration", f"index.md lists {found} slots, expected {expected}")
    if render_index(index_src) != text:
        return fail(
            "1",
            "index count and regeneration",
            "index.md differs from a regeneration of _key/index-src/ — never hand-edit index.md",
        )
    return ok("1", "index count and regeneration", f"{found} slots")


def gate_02_counts(ctx):
    if not ctx.blind_root.is_dir():
        return skip("2", "tree counts", f"{ctx.blind_root} absent")
    blind = [p for p in ctx.blind_files() if p.suffix in (".md", ".csv")]
    expected = ctx.conf.get_int("BLIND_TOTAL")
    if len(blind) != expected:
        return fail("2", "tree counts", f"blind tree holds {len(blind)} documents, expected {expected}")
    if not ctx.flagged_root.is_dir():
        return ok("2", "tree counts", f"blind {len(blind)}; flagged tree absent")
    flagged = [p for p in ctx.flagged_root.rglob("*") if p.is_file() and p.suffix in (".md", ".csv")]
    expected_flagged = ctx.conf.get_int("FLAGGED_TOTAL")
    if len(flagged) != expected_flagged:
        return fail("2", "tree counts", f"flagged tree holds {len(flagged)}, expected {expected_flagged}")
    return ok("2", "tree counts", f"blind {len(blind)}, flagged {len(flagged)}")


import re

import yaml

from ..roomconf import RoomConfError
from ..twin import is_valid_twin, split_twin

SLOT_REF = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{1,3})\b")

# The exact shape twin.annotation_block() writes a finding's ID in:
# "- **{id} ({severity})** — {substance}". Matching on "**ID (" rather than
# trusting FINDING_PREFIXES keeps this prefix-agnostic — the check below is
# "does this ID appear in the answer key", and the answer key is the
# authority on which prefixes are legitimate, not a second, separate guess.
CLAIMED_ID = re.compile(r"\*\*([^\s()*]+)\s*\(")


def _claimed_ids(block: str) -> set:
    """Every finding ID a block actually claims to carry, per CLAIMED_ID.

    Empty for a block gutted down to a bare heading — that emptiness IS the
    signal gate 8 uses to catch a gutted block, not a special case handled
    separately from a genuinely mismatched or fabricated ID.
    """
    return set(CLAIMED_ID.findall(block))


def gate_06_dir_canon(ctx):
    if not ctx.blind_root.is_dir():
        return skip("6", "directory canon", f"{ctx.blind_root} absent")
    expected = set(ctx.conf.get_list("SECTION_DIRS"))
    found = {p.name for p in ctx.blind_root.iterdir() if p.is_dir()}
    unexpected = found - expected
    missing = expected - found
    if unexpected or missing:
        parts = []
        if unexpected:
            parts.append("unexpected: " + ", ".join(sorted(unexpected)))
        if missing:
            parts.append("missing: " + ", ".join(sorted(missing)))
        return fail("6", "directory canon", "; ".join(parts))
    return ok("6", "directory canon", f"{len(found)} sections match room.conf")


def gate_07_twin_diff(ctx):
    if not ctx.blind_root.is_dir() or not ctx.flagged_root.is_dir():
        return skip("7", "twin diff", "blind or flagged tree absent")
    flag_string = ctx.conf.get("FLAG_STRING_1")
    bad = []
    for blind in ctx.blind_files():
        rel = blind.relative_to(ctx.blind_root)
        flagged = ctx.flagged_root / rel
        if not flagged.is_file():
            bad.append(f"{rel}: no flagged twin")
            continue
        if blind.suffix != ".md":
            if blind.read_bytes() != flagged.read_bytes():
                bad.append(f"{rel}: non-markdown twin differs")
            continue
        if not is_valid_twin(
            blind.read_text(encoding="utf-8"), flagged.read_text(encoding="utf-8"), flag_string
        ):
            bad.append(f"{rel}: flagged twin is not blind + appended block")
    if bad:
        return fail("7", "twin diff", "; ".join(bad[:5]))
    return ok("7", "twin diff", "every twin identical or blind + appended block")


def gate_08_carrier_census(ctx):
    """twin-diff CANNOT catch a DELETED annotation block — a stripped twin is
    byte-identical to its blind twin, which is what a benign document looks
    like. Only counting carriers detects the destruction of a planted
    finding — but counting alone is CONTENT-BLIND: a block moved onto an
    innocent document, gutted to a bare heading, or rewritten to claim a
    finding ID that was never planted all leave the carrier COUNT
    unchanged, so a scalar check waves every one of them through.

    The answer key (ctx.findings) is the ground truth this gate checks
    against: which flagged documents SHOULD carry a block, and which
    finding ID(s) each one's block should name. EXPECTED_KDP_CARRIERS in
    room.conf is still checked afterwards, but only as a secondary,
    stale-value tripwire once the key-derived checks below are clean — the
    key wins on any conflict, because the key is the ground truth and
    room.conf is a hand-maintained convenience.
    """
    if not ctx.flagged_root.is_dir():
        return skip("8", "annotation-carrier census", f"{ctx.flagged_root} absent")
    flag_string = ctx.conf.get("FLAG_STRING_1")

    expected_ids_by_path = {}
    for finding in ctx.findings.findings:
        for rel in finding.evidence_paths():
            expected_ids_by_path.setdefault(rel, set()).add(finding.id)
    expected_carriers = set(expected_ids_by_path)
    known_ids = set(ctx.findings.by_id)

    actual_ids_by_path = {}
    for path in ctx.flagged_root.rglob("*.md"):
        rel = path.relative_to(ctx.flagged_root).as_posix()
        _, block = split_twin(path.read_text(encoding="utf-8"), flag_string)
        if block is not None:
            actual_ids_by_path[rel] = _claimed_ids(block)
    actual_carriers = set(actual_ids_by_path)

    problems = []

    # Expected but not actually carrying a block: destroyed outright, or
    # moved elsewhere (its own disappearance is a problem regardless of
    # whether the block resurfaced somewhere else — that resurfacing is
    # caught separately, below, as an unexpected carrier).
    for rel in sorted(expected_carriers - actual_carriers):
        problems.append(
            f"{rel}: expected to carry {sorted(expected_ids_by_path[rel])} but carries no "
            "block — a destroyed or moved finding"
        )

    # Carries a block but is not evidence for anything: a block planted on
    # the wrong document (moved there, or freshly fabricated) claims a
    # finding for a document the answer key never named.
    for rel in sorted(actual_carriers - expected_carriers):
        claimed = sorted(actual_ids_by_path[rel]) or ["no finding ID"]
        problems.append(
            f"{rel}: carries a block naming {claimed} but is not an evidence path for any finding"
        )

    # Both expected AND actual: still verify the block claims the RIGHT
    # finding ID(s), not merely that a block of some kind is present. This
    # is what catches a block gutted down to a bare heading (claims nothing)
    # and a block rewritten to claim an ID that was never planted (claims
    # something not in the answer key) on a document that is otherwise a
    # correct, expected carrier.
    for rel in sorted(expected_carriers & actual_carriers):
        claimed = actual_ids_by_path[rel]
        expected = expected_ids_by_path[rel]
        unknown = claimed - known_ids
        if unknown:
            problems.append(f"{rel}: names finding ID(s) not in the answer key: {sorted(unknown)}")
        if claimed != expected:
            problems.append(
                f"{rel}: block names {sorted(claimed) or ['no finding ID']}, expected {sorted(expected)}"
            )

    if problems:
        return fail("8", "annotation-carrier census", "; ".join(problems[:5]))

    # Secondary tripwire, run only once the key-derived checks above are
    # clean. A missing or absent EXPECTED_KDP_CARRIERS, or one that is
    # explicitly 0, is not itself a failure here — the key-derived checks
    # already cover that ground (a nonexistent expectation would surface
    # any real carrier as "not an evidence path for any finding" above).
    try:
        expected_scalar = ctx.conf.get_int("EXPECTED_KDP_CARRIERS")
    except RoomConfError:
        expected_scalar = 0
    if expected_scalar and len(actual_carriers) != expected_scalar:
        return fail(
            "8",
            "annotation-carrier census",
            f"EXPECTED_KDP_CARRIERS={expected_scalar} in room.conf is stale — the answer key "
            f"implies {len(actual_carriers)} carrier(s)",
        )

    return ok("8", "annotation-carrier census", f"{len(actual_carriers)} carrier(s) match the answer key")


def gate_09_xrefs(ctx):
    """Flag a slot-shaped reference (N.N.N) in the blind tree's prose that
    resolves to no known slot and no allowlisted gap.

    SLOT_REF's shape also matches ordinary prose that is not a reference at
    all — a short-format date ("24.08.26") or a version string are
    indistinguishable from "1.2.3" by shape alone. Bounding the first
    component to the room's ACTUAL section numbers, derived from
    SECTION_DIRS rather than hardcoded, rules out anything with no section
    that number could belong to: a date's day/month/year component
    routinely exceeds any real room's section count, so it is filtered out
    before the known/allowed check ever runs, rather than being flagged and
    then relying on the allowlist to excuse it.

    This narrows the false-positive surface; it does not eliminate it. An
    IN-RANGE version-like string ("1.2" read as "1.2.0", or a genuine
    "1.2.3" typo in prose) is indistinguishable from a real reference by
    shape or range alone. _key/gaps.yaml is the escape hatch for THAT
    residual too, not only for a deliberate, intentional documentation gap —
    treating the allowlist as if it existed solely for deliberate gaps would
    mislead whoever has to maintain it when a false match shows up there.
    """
    files = ctx.blind_files()
    if not files:
        return skip("9", "cross-reference resolution", f"{ctx.blind_root} absent or empty")
    valid_sections = set()
    for entry in ctx.conf.get_list("SECTION_DIRS"):
        prefix = entry.split("_", 1)[0]
        if prefix.isdigit():
            valid_sections.add(int(prefix))
    known = set()
    for path in files:
        stem = path.stem
        if "_" in stem:
            candidate = stem.split("_", 1)[0]
            if SLOT_REF.fullmatch(candidate):
                known.add(candidate)
    gaps_path = ctx.key_root / "gaps.yaml"
    allowed = set()
    if gaps_path.is_file():
        doc = yaml.safe_load(gaps_path.read_text(encoding="utf-8")) or {}
        allowed = {str(row["ref"]) for row in (doc.get("gaps") or [])}
    dangling = []
    for path in files:
        if path.suffix != ".md":
            continue
        own = path.stem.split("_", 1)[0]
        for ref in SLOT_REF.findall(path.read_text(encoding="utf-8")):
            section = ref.split(".", 1)[0]
            if not section.isdigit() or int(section) not in valid_sections:
                # Shape matches, but no section in THIS room could own it —
                # a date or version string, not a reference. Not a slot
                # candidate at all, so it is neither dangling nor in need
                # of an allowlist entry.
                continue
            if ref == own or ref in known or ref in allowed:
                continue
            dangling.append(f"{path.name} -> {ref}")
    if dangling:
        return fail("9", "cross-reference resolution", "; ".join(sorted(set(dangling))[:5]))
    return ok("9", "cross-reference resolution", f"{len(known)} slots, {len(allowed)} allowlisted gaps")
