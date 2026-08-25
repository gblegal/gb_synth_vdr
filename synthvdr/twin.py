"""Derive the flagged tree from the blind tree.

The ONLY writer of the flagged tree. Benign documents are byte-identical to
their blind twin; finding-carriers are the blind file plus one trailing
annotation block. Nothing else may write under the flagged tree.

Symlinked trees are unsupported by design: every path-valued room.conf key
must resolve to the literal relative path it declares under the room root,
so no component of BLIND_TREE, FLAGGED_TREE or KEY_ROOT may be a symlink
that redirects. The room itself MAY be a symlink — it is resolved first and
every tree is then compared against the resolved room — so putting a large
room on another volume is done by symlinking the room, not a tree inside it.

The flagged tree is deleted and rebuilt in full on every build, so this
module only ever deletes a directory it can prove it created: the rebuild
writes a MARKER_NAME file at the root of the tree, and a non-empty target
without that marker is refused rather than deleted.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Set, Tuple

from .ownership import NotOwnedError
from .ownership import assert_target_is_ours as _assert_target_is_ours
from .ownership import write_marker
from .roomconf import (
    PATH_KEYS,
    RoomConf,
    RoomConfError,
    _is_inside,
    _overlaps,
    _same_file,
    check_tree_identity,
)
from .schema import Finding, FindingSet

# _is_inside / _overlaps / _same_file live in roomconf so that the
# config-level and filesystem-level guards share one implementation of "are
# these two paths the same tree?" — there is no second copy to drift out of
# step. They are re-exported here because this module is where they get
# applied to a destructive operation. _overlaps is not called by this module
# itself; it is part of that shared vocabulary and is used by the tests that
# prove the string-level comparison cannot see a same-inode alias.
__all__ = [
    "MARKER_NAME",
    "MARKER_TEXT",
    "SUBJECT_KEY",
    "TwinError",
    "TwinReport",
    "annotation_block",
    "assert_safe_delete_target",
    "assert_target_is_ours",
    "build_flagged_tree",
    "derive_twin",
    "is_valid_twin",
    "split_twin",
]


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


SUBJECT_KEY = "FLAGGED_TREE"

# Written at the root of the flagged tree on every build. Its presence is
# what licenses the next build to delete the tree: the guards above bound the
# delete to the *declared* path, but nothing in a path bounds it to data this
# tool owns, and FLAGGED_TREE may name any directory in the room —
# '_key/index-src', 'scratch', a mount point holding another volume. The
# marker turns "we delete where you told us" into "we only delete what we
# made", which is the contract this needs on a stranger's machine.
#
# "Presence" means a REAL file, checked case-sensitively, at exactly this
# name — never a symlink at this name (which could point at any unrelated
# file and read as "present"), and never a same-named entry differing only
# in case (which a case-insensitive filesystem, e.g. APFS by default,
# would otherwise treat as a match for a plain path lookup). Both were
# found to be genuine bypasses of an earlier version of this check, well
# after the five rounds that had already closed every other one — see
# synthvdr.ownership, which is what actually implements the check now.
MARKER_NAME = ".synthvdr-flagged-tree"

MARKER_TEXT = (
    "Generated by synthvdr (synthvdr/twin.py). This whole directory is "
    "deleted and rebuilt on every build, so nothing here is safe to edit or "
    "store. Removing this marker file makes the tree un-rebuildable in "
    "place: synthvdr refuses to delete a non-empty directory it cannot "
    "prove it created, and will stop rather than destroy your data.\n"
)


def assert_target_is_ours(flagged_root: Path) -> None:
    """TwinError-raising wrapper around the shared ownership guard in
    synthvdr.ownership (Task 7's own guard, extracted so synthvdr.subset
    can reuse the identical algorithm rather than re-deriving it — see
    that module's docstring for why). Kept as a named function here,
    rather than inlined at its one call site, because tests/test_twin.py
    imports and calls this name directly.
    """
    try:
        _assert_target_is_ours(flagged_root, MARKER_NAME)
    except NotOwnedError as exc:
        raise TwinError(str(exc)) from exc


def assert_safe_delete_target(
    subject: Path, resolved: Mapping[str, Path], subject_key: str = SUBJECT_KEY
) -> None:
    """The rmtree precondition: `subject` — the resolved flagged root about
    to be deleted — must not equal, and must not contain, ANY other member
    of `resolved`.

    It iterates the resolved tree map rather than a fixed list of pairs, so
    a path key added to roomconf.PATH_KEYS is protected here automatically.

    Two clauses, neither subsuming the other. _is_inside covers "other is the
    target, or sits under it" — equality included, since a path is a prefix
    of itself — by casefolded path comparison. _same_file covers the roots
    that are one directory on disk under two spellings a string comparison
    cannot reconcile (Unicode normalisation, a hardlink, a bind mount).

    Containment is checked in one direction only, and deliberately: a root
    *inside* the delete target is destroyed by the delete, which is what this
    guards. The subject being inside another root is not by itself a hazard —
    that is the sanctioned FLAGGED_TREE-under-KEY_ROOT layout, and
    check_tree_identity is what decides which nestings are legal.
    """
    for label, other in resolved.items():
        if label == subject_key:
            continue
        if _same_file(subject, other):
            raise TwinError(
                f"refusing to delete {subject}: it is the same directory on "
                f"disk as {label} ({other}), under a different spelling"
            )
        if _is_inside(other, subject):
            raise TwinError(
                f"refusing to delete {subject}: {label} ({other}) is that "
                "directory or sits inside it, and would be destroyed with it"
            )


def build_flagged_tree(room: Path, conf: RoomConf, findings: FindingSet) -> TwinReport:
    flag_string = conf.get("FLAG_STRING_1")

    carriers: Dict[str, List[Finding]] = {}
    for finding in findings.findings:
        for rel in finding.evidence_paths():
            carriers.setdefault(rel, []).append(finding)

    # Re-run the full config guard here, against the room this call was
    # actually handed. Not merely belt-and-braces for a hand-built RoomConf:
    # `room` need not be conf.path.parent, and a symlink planted after
    # load_room_conf returned would make the load-time result stale. Property
    # 1 (a tree resolves to its literal declared path) and Property 2 (every
    # pair of configured trees is distinct and non-overlapping) are both
    # re-established here, immediately before anything destructive.
    try:
        resolved = check_tree_identity(room, conf.values, PATH_KEYS, conf.path)
    except RoomConfError as exc:
        raise TwinError(str(exc)) from exc

    # Act on exactly the paths that were checked. Property 1 has just proved
    # these are the same directories as `room / conf.get(...)`, so this is not
    # a behaviour change — it removes the gap between the path validated and
    # the path handed to rmtree.
    blind_root = resolved["BLIND_TREE"]
    flagged_root = resolved[SUBJECT_KEY]
    assert_safe_delete_target(flagged_root, resolved)
    assert_target_is_ours(flagged_root)

    # The marker goes down before any document does, so a build that dies
    # part-way still leaves a tree the next build is allowed to clear.
    # Wrapping this in TwinError also catches the residue the guards above
    # cannot reach: a symlink loop resolves to itself, so Property 1 sees no
    # redirect and lets it through, and it then surfaces as a bare ELOOP or
    # FileExistsError out of mkdir. Nothing is destroyed either way — this is
    # about raising the module's own exception type.
    try:
        if flagged_root.exists():
            shutil.rmtree(flagged_root)
        flagged_root.mkdir(parents=True, exist_ok=True)
        write_marker(flagged_root, MARKER_NAME, MARKER_TEXT)
    except OSError as exc:
        raise TwinError(
            f"could not prepare FLAGGED_TREE at {flagged_root} "
            f"({exc.__class__.__name__}: {exc})"
        ) from exc

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
