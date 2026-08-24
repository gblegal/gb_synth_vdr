"""Derive the flagged tree from the blind tree.

The ONLY writer of the flagged tree. Benign documents are byte-identical to
their blind twin; finding-carriers are the blind file plus one trailing
annotation block. Nothing else may write under the flagged tree.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .roomconf import RoomConf
from .schema import Finding, FindingSet


@dataclass(frozen=True)
class TwinReport:
    written: int
    carriers: int
    identical: int


def annotation_block(findings: List[Finding], flag_string: str) -> str:
    lines = ["", f"## {flag_string}", ""]
    for finding in findings:
        lines.append(f"- **{finding.id} ({finding.severity})** — {finding.substance.strip()}")
        if finding.corroboration:
            joined = ", ".join(f"`{p}`" for p in finding.corroboration)
            lines.append(f"  - Related documents: {joined}")
        if finding.cross_links:
            lines.append(f"  - Cross-links: {', '.join(finding.cross_links)}")
    lines.append("")
    return "\n".join(lines)


def derive_twin(blind_text: str, block: Optional[str]) -> str:
    if block is None:
        return blind_text
    return blind_text + block


def split_twin(flagged_text: str, flag_string: str) -> Tuple[str, Optional[str]]:
    marker = f"\n## {flag_string}\n"
    position = flagged_text.rfind(marker)
    if position == -1:
        return flagged_text, None
    # the block begins with the blank line preceding the heading
    return flagged_text[:position], flagged_text[position:]


def is_valid_twin(blind_text: str, flagged_text: str, flag_string: str) -> bool:
    if flagged_text == blind_text:
        return True
    body, block = split_twin(flagged_text, flag_string)
    return block is not None and body == blind_text


def build_flagged_tree(room: Path, conf: RoomConf, findings: FindingSet) -> TwinReport:
    blind_root = room / conf.get("BLIND_TREE")
    flagged_root = room / conf.get("FLAGGED_TREE")
    flag_string = conf.get("FLAG_STRING_1")

    carriers: Dict[str, List[Finding]] = {}
    for finding in findings.findings:
        for rel in finding.evidence_paths():
            carriers.setdefault(rel, []).append(finding)

    if flagged_root.exists():
        shutil.rmtree(flagged_root)

    written = carrier_count = identical = 0
    for source in sorted(blind_root.rglob("*")):
        if not source.is_file():
            continue
        rel = source.relative_to(blind_root).as_posix()
        target = flagged_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix != ".md" or rel not in carriers:
            shutil.copyfile(source, target)
            identical += 1
        else:
            block = annotation_block(sorted(carriers[rel], key=lambda f: f.id), flag_string)
            blind_text = source.read_text(encoding="utf-8")
            target.write_text(derive_twin(blind_text, block), encoding="utf-8")
            carrier_count += 1
        written += 1
    return TwinReport(written=written, carriers=carrier_count, identical=identical)
