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
from pathlib import Path
from typing import Dict, Iterable, List

from ..domain import DEFAULT_DOMAIN_ROOT, DomainPack, load_domain
from ..slots import read_anchors_csv
from .runner import fail, ok, skip, truncated

# Literal phrases and bracketed-instruction prefixes that read as a
# placeholder regardless of case. A LIST, not a property — deliberately.
#
# A previous round replaced the bracketed markers here with a general
# "[BRACKETED ALL-CAPS]" regex, on the reasoning that a property beats an
# enumeration. That reasoning is right in general and wrong here, and the
# difference is the SHAPE OF THE TWO SETS, not a preference for one
# technique over the other. The target set — author instructions left
# behind in a draft (INSERT, DRAFT, TBC, TBA, TODO, and the rest) — is
# closed and small. The false-positive set for any bracket-shaped property
# is NOT closed: this corpus generates a draft share purchase agreement in
# section 18 BY DESIGN, and ordinary contract drafting is full of
# legitimate bracketed defined terms — "[BUYER]", "[SELLER]", "[PARENT
# GUARANTEE]" — that no shape-based rule can tell apart from a real
# placeholder. A property that over-matches an open false-positive set is
# worse than a list that covers a closed target set. (The reverse holds
# where the TARGET set is open instead — e.g. any capitalised word before
# an entity name, or any filesystem mechanism that aliases a path — and a
# property is correct there because no list could enumerate it. This is
# not that case: do not "improve" this back into a regex.)
#
# Each bracketed entry matches on the opening-bracket prefix, not a closed
# form, so "[TBC]", "[TBC — pending]" and "[draft: not final]" all match
# while "[BUYER]" does not — the prefix itself is the discriminator.
PLACEHOLDER_TOKENS = (
    "lorem ipsum",
    "todo",
    "tbd",
    "xxx",
    "fixme",
    "placeholder",
    "[insert",
    "[draft",
    "[tbc",
    "[tba",
)

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

    A plain substring scan of the lower-cased text against PLACEHOLDER_TOKENS
    — see the comment above that tuple for why this is a closed list rather
    than a shape-based property.
    """
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


def depth_problems(
    paths: Iterable[Path], tiers: Dict[str, str], pack: DomainPack, flag_string: str
) -> List[str]:
    """What gate 10 would say about `paths`, as a list of problem strings.

    Split out of `gate_10_depth` so `/vdr-build` can run the same check over
    one wave's output, straight after the authors return and before anything
    is consolidated. It needs to, because a `vdr-author` subagent cannot: its
    frontmatter grants `Read, Write, Edit, Grep, Glob` and no Bash, so it has
    no way to run `wordcount()` and every depth figure it reports is a visual
    estimate. In the build that surfaced this, every estimate was HIGH — ~1,450
    for 1,190 against a 1,200 floor, ~3,050 for 2,447 against 2,500 — and seven
    of 40 documents landed under floor, costing a whole remediation wave.

    Sharing this function rather than putting the loop in the skill's own
    fenced example is the point: a reimplementation there would be free to
    drift from the gate it is meant to predict, which is how a wave could pass
    its own check and fail gate 10 anyway.

    `tiers` is `read_anchors_csv`'s mapping. `flag_string` is `FLAG_STRING_1`,
    used to strip a flagged twin's annotation block so it cannot pad a document
    over a floor its blind twin does not clear.
    """
    problems: List[str] = []
    for path in paths:
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
    return problems


def gate_10_depth(ctx):
    anchors_path = ctx.key_root / "anchors.csv"
    if not anchors_path.is_file():
        return skip("10", "depth lint", "_key/anchors.csv absent")
    files = [p for p in ctx.blind_files() if p.suffix == ".md"]
    if not files:
        return skip("10", "depth lint", f"{ctx.blind_root} absent or empty")

    problems = depth_problems(
        files,
        read_anchors_csv(anchors_path),
        load_domain(DEFAULT_DOMAIN_ROOT),
        ctx.conf.get("FLAG_STRING_1"),
    )

    metric_note = "(metric: whitespace tokens, table pipes counted, CJK at half weight)"
    if problems:
        return fail("10", "depth lint", truncated(problems) + " " + metric_note)
    return ok("10", "depth lint", f"{len(files)} documents above their floors {metric_note}")
