"""Integrity gates: subset reconciliation, fact-sheet reconciliation, discoverability."""

from __future__ import annotations

from ..subset import check_subset
from .runner import fail, ok, skip


def gate_11_subset(ctx):
    subset_dir = ctx.room / "subset"
    if not subset_dir.is_dir():
        return skip("11", "subset reconciliation", "subset/ not built")
    report = check_subset(ctx.room, ctx.conf, ctx.findings, out_dir=subset_dir)
    if not report.complete:
        return fail("11", "subset reconciliation", "; ".join(report.errors[:5]))
    return ok(
        "11",
        "subset reconciliation",
        f"{report.total} documents, {report.findings_covered}/{report.findings_total} findings covered",
    )
