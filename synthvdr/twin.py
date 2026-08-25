"""Derive the flagged tree from the blind tree.

The ONLY writer of the flagged tree. Benign documents are byte-identical to
their blind twin; finding-carriers are the blind file plus one trailing
annotation block. Nothing else may write under the flagged tree.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .roomconf import RoomConf
from .schema import Finding, FindingSet


class TwinError(Exception):
    """The flagged tree cannot be safely derived from the blind tree."""


@dataclass(frozen=True)
class TwinReport:
    """Tally from build_flagged_tree.

    `.carriers` counts *documents* that received an annotation block, not
    evidence paths. It is legitimately lower than
    `len(FindingSet.carrier_paths())` whenever an evidence path names a
    non-markdown file (those are copied byte-for-byte and never annotated)
    or two findings share the same evidence document (one combined block,
    one carrier). Neither case is a bug — don't read an undercount here as
    one when reconciling against EXPECTED_KDP_CARRIERS.
    """

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


def _casefolded_parts(path: Path) -> Tuple[str, ...]:
    """`path`'s parts, casefolded for a case-insensitive comparison —
    unconditionally, not just when the host filesystem happens to be
    case-insensitive. See roomconf._segments for why: macOS and Windows
    treat 'data-room' and 'DATA-ROOM' as the same directory regardless of
    what OS this check runs on, so the comparison must too.
    """
    return tuple(part.casefold() for part in path.parts)


def _is_inside(inner: Path, outer: Path) -> bool:
    """True if `inner` is `outer` itself, or nested under it, comparing
    case-insensitively (see _casefolded_parts). Both must already be
    resolved (absolute, symlink-free) paths.
    """
    inner_parts = _casefolded_parts(inner)
    outer_parts = _casefolded_parts(outer)
    return inner_parts[: len(outer_parts)] == outer_parts


def _overlaps(a: Path, b: Path) -> bool:
    """True if `a` and `b` name the same directory (case-insensitively) or
    one is nested under the other — i.e. they are not two genuinely
    separate trees by path alone. This is a string-level check: it cannot
    see a hardlink, bind mount, or symlink that makes two differently
    *spelled* paths the same directory on disk without differing only in
    case — that is what _same_file is for.
    """
    return _is_inside(a, b) or _is_inside(b, a)


def _same_file(a: Path, b: Path) -> bool:
    """True if `a` and `b` are the same file or directory on disk — same
    device and inode, per os.path.samefile. This catches every aliasing
    route a path-string comparison cannot see at all: case aliases on a
    case-insensitive filesystem, hardlinks, bind mounts, and symlinks —
    all without needing to know which of those is in play. False (not an
    error) if either path doesn't exist yet: nothing can be aliased to a
    path with nothing there.
    """
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def build_flagged_tree(room: Path, conf: RoomConf, findings: FindingSet) -> TwinReport:
    blind_root = room / conf.get("BLIND_TREE")
    flagged_root = room / conf.get("FLAGGED_TREE")
    flag_string = conf.get("FLAG_STRING_1")

    carriers: Dict[str, List[Finding]] = {}
    for finding in findings.findings:
        for rel in finding.evidence_paths():
            carriers.setdefault(rel, []).append(finding)

    # Belt-and-braces backstop before the destructive call: load_room_conf
    # already rejects an unsafe or overlapping FLAGGED_TREE, but a RoomConf
    # can also be constructed by hand (bypassing that check), so refuse
    # outright rather than deleting anything outside the room, the room
    # root itself, or the blind tree.
    room_resolved = room.resolve()
    blind_resolved = blind_root.resolve()
    flagged_resolved = flagged_root.resolve()

    if not _is_inside(flagged_resolved, room_resolved):
        raise TwinError(
            f"FLAGGED_TREE resolves outside the room root — refusing to delete "
            f"{flagged_resolved} (room is {room_resolved})"
        )
    if _casefolded_parts(flagged_resolved) == _casefolded_parts(room_resolved):
        raise TwinError(
            f"FLAGGED_TREE resolves to the room root itself — refusing to "
            f"delete {flagged_resolved}"
        )
    if _overlaps(flagged_resolved, blind_resolved):
        raise TwinError(
            f"FLAGGED_TREE resolves to or overlaps BLIND_TREE — refusing to "
            f"delete {flagged_resolved}, which would destroy or leak into "
            f"the blind room at {blind_resolved}"
        )

    # The checks above compare configured paths as strings (case-insensitively
    # normalised). They cannot see two differently-spelled paths that the
    # filesystem itself resolves to one physical directory by some other
    # route — a hardlink, a bind mount, or a symlink — nor a case alias that
    # somehow slipped past the string comparison. samefile compares device
    # and inode, so it catches all of those in one check, for whichever of
    # the three roots already exist. A root that doesn't exist yet can't be
    # aliased to anything, so _same_file treats that as "not the same file"
    # rather than raising.
    for label, a, b in (
        ("FLAGGED_TREE and the room root", flagged_resolved, room_resolved),
        ("FLAGGED_TREE and BLIND_TREE", flagged_resolved, blind_resolved),
        ("BLIND_TREE and the room root", blind_resolved, room_resolved),
    ):
        if _same_file(a, b):
            raise TwinError(
                f"{label} are the same file on disk (same device and inode) "
                "even though their configured paths differ — refusing to "
                f"delete {flagged_resolved}"
            )

    if flagged_root.exists():
        shutil.rmtree(flagged_root)

    written = carrier_count = identical = 0
    matched: Set[str] = set()
    for source in sorted(blind_root.rglob("*")):
        if not source.is_file():
            continue
        rel = source.relative_to(blind_root).as_posix()
        matched.add(rel)
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

    # A finding whose source or corroboration names a path with no matching
    # file under BLIND_TREE would otherwise be silently dropped — the
    # finding was never actually planted, and nothing would say so. Same
    # silent-failure shape as a stripped annotation block: catch it here
    # rather than let it surface later as an unexplained gap in the corpus.
    missing = sorted(set(carriers) - matched)
    if missing:
        raise TwinError(
            "finding evidence path(s) not found under BLIND_TREE: "
            + ", ".join(missing)
        )

    return TwinReport(written=written, carriers=carrier_count, identical=identical)
