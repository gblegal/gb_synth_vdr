"""Render gates. These SKIP loudly: in a previous build the render gates printed
nothing at all when the render trees were absent, and three renderer defects —
one clipping answer-key evidence out of the PDFs — survived behind that silence
for two phases.

Parity is checked in BOTH directions, and this is load-bearing rather than a
nicety. `render_tree_docx` (see `synthvdr.render.docx`) is deliberately
non-destructive: it never deletes a render whose source document was since
renamed or removed. One-directional parity — "every source has a render" —
plus a writer that never deletes means an orphaned render can accumulate
silently forever, since nothing else in the pipeline would ever notice it.
Checking the reverse direction too — "every render has a source" — is what
catches that. The two directions are reported distinctly, never merged into
one count: "3 sources with no render" and "2 renders with no source" are
different problems (a renderer that hasn't run yet vs. a stale leftover) with
different fixes, and a single combined number tells a room author neither.
"""

from __future__ import annotations

from typing import Dict, List, Set

from .runner import fail, ok, skip

# suffix appended to BLIND_TREE's name -> file extension the render tree uses.
RENDER_SUFFIXES = {"-docx": ".docx", "-pdf": ".pdf"}


def gate_16_render_parity(ctx):
    blind_name = ctx.conf.get("BLIND_TREE")
    trees = [
        (ctx.room / f"{blind_name}{suffix}", extension)
        for suffix, extension in RENDER_SUFFIXES.items()
        if (ctx.room / f"{blind_name}{suffix}").is_dir()
    ]
    if not trees:
        return skip("16", "render parity", "no render tree present — renders are optional")

    # ctx.blind_files() returns every file under BLIND_TREE unfiltered by
    # design (marker dotfiles included) — the suffix filter is this gate's
    # own job, same as every other gate that walks blind_files().
    sources = [p for p in ctx.blind_files() if p.suffix == ".md"]
    source_rels: Set = {p.relative_to(ctx.blind_root) for p in sources}

    missing: Dict[str, List[str]] = {}
    orphaned: Dict[str, List[str]] = {}
    for tree, extension in trees:
        tree_missing = []
        for source in sources:
            rel = source.relative_to(ctx.blind_root).with_suffix(extension)
            if not (tree / rel).is_file():
                tree_missing.append(rel.stem)
        if tree_missing:
            missing[tree.name] = tree_missing

        tree_orphaned = []
        for render in sorted(p for p in tree.rglob(f"*{extension}") if p.is_file()):
            rel_source = render.relative_to(tree).with_suffix(".md")
            if rel_source not in source_rels:
                tree_orphaned.append(render.relative_to(tree).as_posix())
        if tree_orphaned:
            orphaned[tree.name] = tree_orphaned

    if missing or orphaned:
        parts = []
        if missing:
            total = sum(len(v) for v in missing.values())
            detail = "; ".join(f"{name}: missing {', '.join(v[:5])}" for name, v in missing.items())
            parts.append(f"{total} source(s) with no render — {detail}")
        if orphaned:
            total = sum(len(v) for v in orphaned.values())
            detail = "; ".join(f"{name}: orphaned {', '.join(v[:5])}" for name, v in orphaned.items())
            parts.append(f"{total} render(s) with no source — {detail}")
        return fail("16", "render parity", " | ".join(parts))

    names = ", ".join(tree.name for tree, _ in trees)
    return ok("16", "render parity", f"{len(sources)} documents mirrored in {names}")
