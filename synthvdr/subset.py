"""Deterministic subset builder.

Every planted finding survives into the subset with its full evidence chain;
filler is chosen by hash so repeat runs are byte-identical. check_subset()
reconciles an existing subset and writes nothing — it is gate 11.
"""

from __future__ import annotations

import csv
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

from .roomconf import (
    PATH_KEYS,
    ROOM_ROOT_LABEL,
    RoomConf,
    RoomConfError,
    _is_inside,
    _same_file,
    resolve_tree_map,
)
from .schema import FindingSet


class SubsetError(Exception):
    """The requested subset output directory cannot be used safely."""


@dataclass
class SubsetReport:
    total: int = 0
    evidence: int = 0
    filler: int = 0
    findings_covered: int = 0
    findings_total: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.errors and self.findings_covered == self.findings_total


def _hash_key(rel: str) -> str:
    return hashlib.sha256(rel.encode("utf-8")).hexdigest()


def select_subset(room: Path, conf: RoomConf, findings: FindingSet, total: int) -> List[str]:
    blind_root = room / conf.get("BLIND_TREE")
    everything = sorted(
        p.relative_to(blind_root).as_posix() for p in blind_root.rglob("*") if p.is_file()
    )
    evidence = sorted(findings.all_evidence_paths() & set(everything))
    filler_pool = [rel for rel in everything if rel not in set(evidence)]
    filler_pool.sort(key=_hash_key)
    shortfall = max(0, total - len(evidence))
    return evidence + filler_pool[:shortfall]


def _assert_safe_out_dir(out_dir: Path, room: Path, conf: RoomConf) -> None:
    """out_dir is a build_subset PARAMETER, not a room.conf key, so
    load_room_conf's PATH_KEYS validation never runs over it — and
    build_subset deletes-and-rebuilds out_dir with shutil.rmtree. Task 7
    found four separate ways an equivalently unguarded destructive path
    could be pointed somewhere it shouldn't; this closes the same class of
    gap here.

    Re-resolves BLIND_TREE, FLAGGED_TREE, KEY_ROOT and the room root
    exactly as roomconf.check_tree_identity does before a destructive
    write (this also re-catches a symlink planted after load_room_conf
    returned, same as that check), then refuses out_dir if it equals,
    contains, or sits inside any configured tree, or is the same directory
    on disk (by inode, see _same_file) as one. Both containment directions
    matter for a configured tree: out_dir UNDER it corrupts that tree on
    write, and it UNDER out_dir is destroyed outright when out_dir is
    rmtree'd on the next build.

    The room root gets only the "contains" half of that: out_dir living
    INSIDE the room (e.g. `room / "subset"`, the normal case) is fine and
    expected — nothing here should forbid it. What must be refused is
    out_dir BEING the room root, or an ancestor of it: rmtree(out_dir)
    would then take the whole room, or more than the room, out with it.

    Runs first, before anything is written, so a bad out_dir fails loudly
    instead of deleting the caller's data and only then raising.
    """
    try:
        resolved = resolve_tree_map(room, conf.values, PATH_KEYS, conf.path)
    except RoomConfError as exc:
        raise SubsetError(str(exc)) from exc

    out_resolved = out_dir.resolve()
    for label, other in resolved.items():
        if label == ROOM_ROOT_LABEL:
            unsafe = _same_file(out_resolved, other) or _is_inside(other, out_resolved)
        else:
            unsafe = (
                _same_file(out_resolved, other)
                or _is_inside(out_resolved, other)
                or _is_inside(other, out_resolved)
            )
        if unsafe:
            raise SubsetError(
                f"refusing to use {out_dir} as the subset output directory: it "
                f"is, contains, or sits inside {label} ({other}). build_subset "
                "deletes and rebuilds this directory on every call, so it must "
                "be distinct from every configured tree, and must not be or "
                "contain the room root."
            )


def build_subset(
    room: Path, conf: RoomConf, findings: FindingSet, total: int, out_dir: Path
) -> SubsetReport:
    _assert_safe_out_dir(out_dir, room, conf)

    blind_root = room / conf.get("BLIND_TREE")
    key_root = room / conf.get("KEY_ROOT")
    selected = select_subset(room, conf, findings, total)
    evidence = findings.all_evidence_paths()

    if out_dir.exists():
        shutil.rmtree(out_dir)
    for rel in selected:
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(blind_root / rel, target)

    key_root.mkdir(parents=True, exist_ok=True)
    with (key_root / "subset-manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rel_path", "role"])
        for rel in selected:
            writer.writerow([rel, "evidence" if rel in evidence else "filler"])

    covered = [f for f in findings.findings if set(f.evidence_paths()) <= set(selected)]
    (key_root / "subset-key.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "room": findings.room,
                "subset_documents": len(selected),
                "findings": [
                    {"id": f.id, "severity": f.severity, "evidence": f.evidence_paths()}
                    for f in covered
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return check_subset(room, conf, findings, out_dir=out_dir)


def check_subset(
    room: Path, conf: RoomConf, findings: FindingSet, out_dir: Optional[Path] = None
) -> SubsetReport:
    out_dir = out_dir or (room / "subset")
    report = SubsetReport(findings_total=len(findings.findings))
    if not out_dir.is_dir():
        report.errors.append(f"{out_dir} does not exist")
        return report

    present = {p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*") if p.is_file()}
    report.total = len(present)
    evidence_paths = findings.all_evidence_paths()
    report.evidence = len(evidence_paths & present)
    report.filler = report.total - report.evidence

    if not findings.findings:
        report.errors.append(
            "no findings parsed from the answer key — refusing to report a reconciled subset"
        )
        return report

    for finding in findings.findings:
        missing = [p for p in finding.evidence_paths() if p not in present]
        if missing:
            report.errors.append(f"{finding.id}: evidence missing from subset: {', '.join(missing)}")
        else:
            report.findings_covered += 1
    return report
