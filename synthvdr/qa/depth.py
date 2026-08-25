"""Two-tier depth lint.

This gate catches accidental thinness (nobody finished writing the
document) and residual template artifacts (a placeholder marker left
behind). It does NOT catch deliberate padding: repeated boilerplate, YAML
front matter, fenced code blocks and long cross-reference lists all
inflate wordcount() and all pass. It is a floor beneath which a document
cannot credibly be finished — not a quality signal above that floor, and
not a general anti-padding guarantee.

METRIC CAVEATS, which any band quoted against wordcount() must state:
  1. Markdown table pipes tokenise as words, so table-heavy documents read
     15-25% longer than their prose.
  2. CJK characters are counted at half weight, not as a single token, so
     Japanese and Chinese documents are not systematically under-counted.
"""

from __future__ import annotations

import re

from ..domain import DEFAULT_DOMAIN_ROOT, DomainPack, load_domain
from ..slots import read_anchors_csv
from .runner import fail, ok, skip

# Literal phrases that read as a placeholder regardless of case or
# brackets. This list PINS THE KNOWN SHAPES (a prior round nearly dropped
# "todo"/"tbd"/"[insert"/"xxx" here, mistaking the list itself for the
# enumeration anti-pattern the bracket-idiom property below exists to
# extend beyond — it does not replace them: TODO is the single most common
# placeholder shape in generated text, and "[insert amount]" is exactly
# what a half-finished contract leaves behind, and neither is catchable by
# the all-caps bracket property below). The property adds coverage for
# shapes this list cannot enumerate; it does not supersede the list.
PLACEHOLDER_TOKENS = ("lorem ipsum", "todo", "tbd", "[insert", "xxx", "fixme", "placeholder")

# The "[BRACKETED ALL-CAPS]" drafting idiom — [INSERT], [DRAFT], [TBC], and
# any future marker of that shape, without naming each one here. Requires
# at least two characters inside the brackets so genuine single-letter
# schedule/appendix references ("Schedule [A]") don't false-positive, and
# requires every character to be upper-case so a mixed-case cross-reference
# like "[see clause 4.2]" or "[Note 3]" cannot match.
_BRACKET_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9 _-]+\]")

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")


class DepthLintError(Exception):
    """A slot's tier in anchors.csv is not a recognised tier ('A' or 'F')."""


def wordcount(text: str) -> int:
    cjk = len(_CJK.findall(text))
    non_cjk = _CJK.sub(" ", text)
    return len(non_cjk.split()) + cjk // 2


def strip_annotation(text: str, flag_string: str) -> str:
    marker = f"\n## {flag_string}\n"
    position = text.rfind(marker)
    return text if position == -1 else text[:position]


def _placeholder_hit(text: str) -> str | None:
    """The first placeholder marker in `text`, or None if there is none.

    Run against the original-case text, not a lower-cased copy: lower-casing
    first would destroy the "all-caps" signal the bracket check relies on.
    The literal-token check still lower-cases its own copy, since those
    phrases are placeholders regardless of case.
    """
    bracket = _BRACKET_PLACEHOLDER.search(text)
    if bracket:
        return bracket.group(0)
    lowered = text.lower()
    return next((t for t in PLACEHOLDER_TOKENS if t in lowered), None)


def classify_archetype(filename: str, pack: DomainPack) -> str:
    lowered = filename.lower()
    best = None  # (pattern_length, floor, name)
    for name, archetype in pack.archetypes.items():
        for pattern in archetype.filename_patterns:
            if pattern in lowered:
                # Longest matching pattern wins ("trust-deed" beats "deed").
                # On an equal-length tie across two archetypes, the HIGHER
                # floor wins — the safe direction, and one that does not
                # depend on which archetype happens to be declared first in
                # archetypes.yaml (an alphabetise-the-file edit must never
                # silently change a real document's floor).
                candidate = (len(pattern), archetype.floor, name)
                if best is None or candidate[:2] > best[:2]:
                    best = candidate
    return best[2] if best else pack.default_archetype


def floor_for(slot_id: str, filename: str, tier: str, pack: DomainPack) -> int:
    if tier not in ("A", "F"):
        raise DepthLintError(f"{slot_id}: invalid tier {tier!r} in anchors.csv, expected 'A' or 'F'")
    if tier == "F":
        return pack.tier_f_floor
    return pack.archetypes[classify_archetype(filename, pack)].floor


def gate_10_depth(ctx):
    anchors_path = ctx.key_root / "anchors.csv"
    if not anchors_path.is_file():
        return skip("10", "depth lint", "_key/anchors.csv absent")
    files = [p for p in ctx.blind_files() if p.suffix == ".md"]
    if not files:
        return skip("10", "depth lint", f"{ctx.blind_root} absent or empty")

    tiers = read_anchors_csv(anchors_path)
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    flag_string = ctx.conf.get("FLAG_STRING_1")
    problems = []
    for path in files:
        slot_id = path.stem.split("_", 1)[0]
        tier = tiers.get(slot_id)
        if tier is None:
            problems.append(f"{slot_id}: absent from anchors.csv")
            continue
        try:
            floor = floor_for(slot_id, path.name, tier, pack)
        except DepthLintError as exc:
            problems.append(str(exc))
            continue
        text = strip_annotation(path.read_text(encoding="utf-8"), flag_string)
        hit = _placeholder_hit(text)
        if hit:
            problems.append(f"{slot_id}: placeholder token {hit!r}")
            continue
        count = wordcount(text)
        if count < floor:
            problems.append(f"{slot_id}: {count} words, floor {floor} (tier {tier})")

    metric_note = "(metric: whitespace tokens, table pipes counted, CJK at half weight)"
    if problems:
        return fail("10", "depth lint", "; ".join(problems[:5]) + " " + metric_note)
    return ok("10", "depth lint", f"{len(files)} documents above their floors {metric_note}")
