"""Two-tier depth lint.

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

PLACEHOLDER_TOKENS = ("lorem ipsum", "todo", "tbd", "[insert", "xxx")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")


def wordcount(text: str) -> int:
    cjk = len(_CJK.findall(text))
    non_cjk = _CJK.sub(" ", text)
    return len(non_cjk.split()) + cjk // 2


def strip_annotation(text: str, flag_string: str) -> str:
    marker = f"\n## {flag_string}\n"
    position = text.rfind(marker)
    return text if position == -1 else text[:position]


def classify_archetype(filename: str, pack: DomainPack) -> str:
    lowered = filename.lower()
    best = None
    for name, archetype in pack.archetypes.items():
        for pattern in archetype.filename_patterns:
            if pattern in lowered:
                # longest matching pattern wins, so "trust-deed" beats "deed"
                if best is None or len(pattern) > best[1]:
                    best = (name, len(pattern))
    return best[0] if best else pack.default_archetype


def floor_for(slot_id: str, filename: str, tier: str, pack: DomainPack) -> int:
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
        text = strip_annotation(path.read_text(encoding="utf-8"), flag_string)
        lowered = text.lower()
        hit = next((t for t in PLACEHOLDER_TOKENS if t in lowered), None)
        if hit:
            problems.append(f"{slot_id}: placeholder token {hit!r}")
            continue
        count = wordcount(text)
        floor = floor_for(slot_id, path.name, tier, pack)
        if count < floor:
            problems.append(f"{slot_id}: {count} words, floor {floor} (tier {tier})")
    if problems:
        return fail("10", "depth lint", "; ".join(problems[:5]))
    return ok(
        "10",
        "depth lint",
        f"{len(files)} documents above their floors "
        "(metric: whitespace tokens, table pipes counted, CJK at half weight)",
    )
