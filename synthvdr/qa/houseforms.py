"""Gate 21: house forms are benign, have blanks, and are genuinely derived from.

Long rooms only. A short room — or a long one in which no target-paper
subfolder has three slots — PASSes with the reason, following gate 19: under
--strict a SKIP is a failure, and a gate about an artefact a room does not have
must not fail it. Spec §10.4.
"""

from __future__ import annotations

from ..domain import DEFAULT_DOMAIN_ROOT, load_domain
from ..houseforms import blanks, derivation_problems, families, house_form_paths
from ..lengths import load_lengths
from ..roomconf import doc_length
from ..schema import load_bearing_paths
from ..slots import read_slot_manifest
from .runner import fail, ok, skip, truncated

NAME = "house forms"


def gate_21_house_forms(ctx):
    if doc_length(ctx.conf) != "long":
        return ok("21", NAME, "not applicable — DOC_LENGTH is short")
    anchors = ctx.key_root / "anchors.csv"
    if not anchors.is_file():
        return skip("21", NAME, "_key/anchors.csv absent")
    lengths = load_lengths(DEFAULT_DOMAIN_ROOT, load_domain(DEFAULT_DOMAIN_ROOT))
    fams = families(read_slot_manifest(anchors), lengths)
    if not fams:
        return ok("21", NAME, "not applicable — no target-paper subfolder in this room has three or more slots")

    problems = [
        f"{path}: answer-key evidence on a house form"
        for path in sorted(load_bearing_paths(ctx.findings, ctx.distractors) & house_form_paths(fams))
    ]
    derived_checked = 0
    for family in fams:
        house_path = ctx.blind_root / family.house_form.rel_path
        if not house_path.is_file():
            continue
        house_text = house_path.read_text(encoding="utf-8")
        if not blanks(house_text):
            problems.append(f"{family.house_form.slot_id}: house form has no bracketed blanks")
        for slot in family.derived:
            path = ctx.blind_root / slot.rel_path
            if not path.is_file():
                continue
            derived_checked += 1
            for problem in derivation_problems(house_text, path.read_text(encoding="utf-8")):
                problems.append(f"{slot.slot_id}: {problem}")
    if problems:
        return fail("21", NAME, truncated(problems))
    return ok("21", NAME, f"{len(fams)} families, {derived_checked} derived contract(s) checked")
