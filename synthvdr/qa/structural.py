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

from ..twin import is_valid_twin, split_twin

SLOT_REF = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{1,3})\b")


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
    byte-identical to its blind twin, which is what a benign document looks like.
    Only counting carriers detects the destruction of a planted finding."""
    if not ctx.flagged_root.is_dir():
        return skip("8", "annotation-carrier census", f"{ctx.flagged_root} absent")
    flag_string = ctx.conf.get("FLAG_STRING_1")
    expected = ctx.conf.get_int("EXPECTED_KDP_CARRIERS")
    carriers = 0
    for path in ctx.flagged_root.rglob("*.md"):
        _, block = split_twin(path.read_text(encoding="utf-8"), flag_string)
        if block is not None:
            carriers += 1
    if carriers != expected:
        return fail(
            "8",
            "annotation-carrier census",
            f"{carriers} carriers, expected {expected} — a missing carrier is a destroyed finding",
        )
    return ok("8", "annotation-carrier census", f"{carriers} carriers")


def gate_09_xrefs(ctx):
    files = ctx.blind_files()
    if not files:
        return skip("9", "cross-reference resolution", f"{ctx.blind_root} absent or empty")
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
            if ref == own or ref in known or ref in allowed:
                continue
            dangling.append(f"{path.name} -> {ref}")
    if dangling:
        return fail("9", "cross-reference resolution", "; ".join(sorted(set(dangling))[:5]))
    return ok("9", "cross-reference resolution", f"{len(known)} slots, {len(allowed)} allowlisted gaps")
