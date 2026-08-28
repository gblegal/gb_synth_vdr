"""Structural gates: counts, canon, twinning, cross-references."""

from __future__ import annotations

import re

import yaml

from ..index_build import count_slots, render_index
from ..roomconf import RoomConfError
from ..twin import is_valid_twin, split_twin
from .runner import fail, ok, skip, truncated, warn


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
        return fail("7", "twin diff", truncated(bad))
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

    Every evidence path — whatever its suffix — is subject to TWO separate
    obligations, checked independently rather than as one suffix-threaded
    check:

      1. It must EXIST under BLIND_TREE. A path naming no file is
         answer-key corruption regardless of whether it could ever carry a
         block — build_flagged_tree refuses to build a room in that state,
         but a room can still be QA'd after the answer key was edited
         without a rebuild, and that drift is exactly what this gate exists
         to catch.
      2. If it is MARKDOWN, it must additionally carry a block naming its
         finding(s). synthvdr.twin never annotates non-.md evidence (a CSV
         register, say) — it is copied byte-for-byte, by design, because
         there is nowhere in a CSV to append prose — so non-markdown
         evidence is exempt from this second obligation only, and is noted
         separately, below, as INFORMATIONAL. It is NOT exempt from
         obligation 1: an earlier version of this gate tangled the two
         together as one suffix-conditioned check, and fixing the false
         fail this caused on legitimate CSV evidence silently dropped
         existence-checking for every non-markdown path along with it.

    Obligation 1 also covers every DISTRACTOR's `location` and `resolution`
    — not just finding evidence. A distractor never carries an annotation
    block (obligation 2 is finding-only: nothing in synthvdr.twin ever
    annotates a distractor), so a distractor's paths have no equivalent
    "unexpected carrier" signal to fall back on if their existence goes
    unchecked. Before this check existed, repointing every distractor in a
    built room at a path that exists nowhere left all seventeen gates
    PASSING: `synthvdr.score` matches a cited document's location against
    `distractor.location` by string equality, so a location that exists
    nowhere can never be cited by a tool under test, and the scorecard's
    "false alarms" metric would silently and permanently read zero. Sharing
    this gate — rather than adding a new one — keeps "every answer-key path
    must exist under BLIND_TREE" as one invariant with one home, rather than
    the same check maintained twice for two different answer-key artefacts
    that both name paths into the same tree.
    """
    if not ctx.flagged_root.is_dir():
        return skip("8", "annotation-carrier census", f"{ctx.flagged_root} absent")
    flag_string = ctx.conf.get("FLAG_STRING_1")

    # Obligation 1, checked first and independently of obligation 2 below —
    # see the docstring for why these must never be re-merged into one
    # suffix-conditioned check.
    existing_blind = {p.relative_to(ctx.blind_root).as_posix() for p in ctx.blind_files()}
    missing_evidence = {}
    for finding in ctx.findings.findings:
        for rel in finding.evidence_paths():
            if rel not in existing_blind:
                missing_evidence.setdefault(rel, set()).add(finding.id)

    # Distractor half of obligation 1 — see the docstring above for why this
    # lives here rather than in a second, near-identical gate. Recorded as
    # (distractor id, field name) pairs, not just ids, because a distractor
    # whose LOCATION is missing (the alarming document itself was never
    # planted) and one whose RESOLUTION is missing (the benign explanation
    # was never planted, so the trap can never be resolved) are different
    # authoring mistakes and the failure message must say which.
    missing_distractor_paths = {}
    for distractor in ctx.distractors:
        for field_name, rel in (
            ("location", distractor.location),
            ("resolution", distractor.resolution),
        ):
            if rel not in existing_blind:
                missing_distractor_paths.setdefault(rel, []).append((distractor.id, field_name))

    # Obligation 2. A path already reported as missing under obligation 1
    # is skipped here — it cannot also be "expected to carry a block but
    # carries none", because it names nothing that could carry one, and
    # reporting the same broken path under both obligations would obscure
    # which of the two is actually true of it.
    expected_ids_by_path = {}
    non_markdown_evidence = {}
    for finding in ctx.findings.findings:
        for rel in finding.evidence_paths():
            if rel in missing_evidence:
                continue
            if rel.endswith(".md"):
                expected_ids_by_path.setdefault(rel, set()).add(finding.id)
            else:
                non_markdown_evidence.setdefault(rel, set()).add(finding.id)
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

    # Obligation 1 failures, reported first: a nonexistent evidence path is
    # a more fundamental corruption than a carrier mismatch, and a distinct
    # message ("does not exist") tells a reader which of the two problems
    # they are looking at, rather than folding it into "carries no block".
    for rel in sorted(missing_evidence):
        problems.append(
            f"{rel}: names finding(s) {sorted(missing_evidence[rel])} but does not exist under "
            f"{ctx.conf.get('BLIND_TREE')}"
        )
    for rel in sorted(missing_distractor_paths):
        owners = ", ".join(
            f"{did} ({field_name})" for did, field_name in missing_distractor_paths[rel]
        )
        problems.append(
            f"{rel}: names distractor {owners} but does not exist under "
            f"{ctx.conf.get('BLIND_TREE')}"
        )

    # Obligation 2's three checks, unchanged in shape from before — but now
    # operating only over evidence paths already confirmed to exist.

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

    # Never silently dropped: a reader should be able to see that a
    # finding's evidence includes a register (or other non-markdown file)
    # that by design carries no block, whichever of PASS/FAIL this gate
    # ends up reporting.
    info = ""
    if non_markdown_evidence:
        named = "; ".join(
            f"{path} ({', '.join(sorted(ids))})" for path, ids in sorted(non_markdown_evidence.items())
        )
        info = f"; non-markdown evidence, never annotated by design: {named}"

    if problems:
        return fail("8", "annotation-carrier census", truncated(problems) + info)

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
            f"implies {len(actual_carriers)} carrier(s)" + info,
        )

    return ok(
        "8", "annotation-carrier census", f"{len(actual_carriers)} carrier(s) match the answer key" + info
    )


def parse_gaps_allowlist(gaps_yaml_text: str) -> set:
    """The set of allowlisted cross-reference refs from a `_key/gaps.yaml` document's text.

    This is the exact shape gate 9 requires — a mapping with a `gaps` list, each row a
    mapping with at least a `ref` (coerced to `str`, so a numeric-looking ref YAML would
    otherwise hand back as a float or int still compares correctly against the string refs
    `SLOT_REF` extracts from prose):

        gaps:
          - ref: "3.2.9"
            reason: "..."

    `reason` is not read here — it exists for whoever maintains the allowlist, not for this
    gate. Factored out of `gate_09_xrefs` so a skill's own literal `gaps.yaml` example can be
    checked against this exact parser (see tests/test_plugin_surface.py) rather than a
    hand-written reimplementation of it that could silently drift from what the gate does.
    Raises the same `KeyError`/`TypeError` a malformed row always raised inline here.
    """
    doc = yaml.safe_load(gaps_yaml_text) or {}
    return {str(row["ref"]) for row in (doc.get("gaps") or [])}


def gate_09_xrefs(ctx):
    """Flag a slot-shaped reference (N.N.N) in the blind tree's prose that
    resolves to no known slot and no allowlisted gap.

    SLOT_REF's shape also matches ordinary prose that is not a reference at
    all — a short-format date ("24.08.26") or a version string are
    indistinguishable from "1.2.3" by shape alone. The first component is
    checked against the room's ACTUAL section numbers, derived from
    SECTION_DIRS rather than hardcoded. An OUT-OF-RANGE match (no section
    that number could belong to) is surfaced as a WARN, not silently
    dropped and not a hard FAIL: an out-of-range slot-shaped token is a date
    OR a gross typo, and an out-of-range value is if anything MORE likely
    to be an error than an in-range one, not less — silently dropping it
    (this gate's first attempt at this fix) threw away exactly the signal
    it was meant to preserve. WARN surfaces it in the runner's warn count
    (see synthvdr.qa.runner) without failing a build over what is often a
    genuine date.

    An IN-RANGE reference that resolves to no known slot and no allowlisted
    gap is still a hard FAIL, exactly as before — that is a genuinely
    dangling in-room reference, not a shape coincidence, and the bound
    above does nothing to weaken that check.

    This does not eliminate every false positive: an IN-RANGE version-like
    string ("1.2.3" typed in prose, in a room with 3+ sections) is
    indistinguishable from a real reference by shape or range alone, and
    would still hard-FAIL if unresolved. _key/gaps.yaml is the escape hatch
    for THAT residual too, not only for a deliberate, intentional
    documentation gap — treating the allowlist as if it existed solely for
    deliberate gaps would mislead whoever has to maintain it when a false
    match shows up there instead.
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
        allowed = parse_gaps_allowlist(gaps_path.read_text(encoding="utf-8"))
    dangling = []
    out_of_range = []
    for path in files:
        if path.suffix != ".md":
            continue
        own = path.stem.split("_", 1)[0]
        for ref in SLOT_REF.findall(path.read_text(encoding="utf-8")):
            section = ref.split(".", 1)[0]
            if not section.isdigit() or int(section) not in valid_sections:
                # No section in THIS room could own it — a date or a gross
                # typo either way, and worth a human's attention either
                # way. Surfaced as a WARN below, never silently dropped;
                # never treated as a hard, in-room dangling reference
                # either, since it does not name a section this room has.
                out_of_range.append(f"{path.name} -> {ref}")
                continue
            if ref == own or ref in known or ref in allowed:
                continue
            dangling.append(f"{path.name} -> {ref}")
    if dangling:
        detail = truncated(sorted(set(dangling)))
        if out_of_range:
            detail += "; also out-of-range token(s) worth checking: " + truncated(
                sorted(set(out_of_range))
            )
        return fail("9", "cross-reference resolution", detail)
    if out_of_range:
        return warn(
            "9",
            "cross-reference resolution",
            "out-of-range slot-shaped token(s), likely a date or a typo — no section in this room "
            "could own them: " + truncated(sorted(set(out_of_range))),
        )
    return ok("9", "cross-reference resolution", f"{len(known)} slots, {len(allowed)} allowlisted gaps")
