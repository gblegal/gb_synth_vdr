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
