"""Parsing for room.conf — the single source of room constants.

Shell-sourceable KEY="VALUE" so tools/check.sh can source the same file.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

REQUIRED_KEYS = (
    "ROOM_CODENAME",
    "INDEX_TOTAL",
    "BLIND_TOTAL",
    "FLAGGED_TOTAL",
    "BLIND_TREE",
    "FLAGGED_TREE",
    "KEY_ROOT",
    "FLAG_STRING_1",
    "FLAG_STRING_2",
    "FINDING_PREFIXES",
    "SECTION_DIRS",
    "EXPECTED_KDP_CARRIERS",
)

# Keys whose values are relative filesystem paths under the room root. Tools
# that turn these into real paths (e.g. synthvdr.twin.build_flagged_tree)
# call shutil.rmtree on the result, so a bad value here is a destructive-path
# risk, not just a cosmetic one — every one of them is checked at load time,
# both individually and for how they sit relative to each other.
PATH_KEYS = (
    "BLIND_TREE",
    "FLAGGED_TREE",
    "KEY_ROOT",
)


class RoomConfError(Exception):
    """room.conf is missing, malformed, or missing a required key."""


def _segments(value: str) -> List[str]:
    """The real path segments of `value`, casefolded for a case-insensitive
    comparison, with '.' components normalised away. Returns [] if `value`
    normalises to the room root itself (e.g. '.', './', './.'). Callers
    must already have rejected an absolute value and a raw '..' segment —
    normalising a value that still contains '..' would silently walk it
    out of the room instead of catching it.

    Casefolding is unconditional, not conditional on the host filesystem:
    macOS and Windows are case-insensitive by default regardless of what OS
    this check runs on, so 'data-room' and 'DATA-ROOM' are the same
    directory there even though they're spelled differently in room.conf —
    a pairwise-overlap check that compares raw strings would miss that
    entirely. A room.conf must be rejected or accepted identically no
    matter which of those filesystems it runs on. This is deliberately
    over-strict on a case-sensitive filesystem, where two trees differing
    only in case genuinely are separate — that's the safe direction, and
    nothing this project generates ever relies on the distinction.

    The returned segments are for structural comparison only (emptiness,
    containment, equality) — callers needing the original spelling (e.g.
    for an error message) must use the raw value, not this.
    """
    normalised = posixpath.normpath(value)
    if normalised == ".":
        return []
    return [segment.casefold() for segment in normalised.split("/")]


def _is_inside_or_equal(inner: List[str], outer: List[str]) -> bool:
    """True if `inner` names the same tree as `outer`, or a tree nested
    under it. Segments must already be casefolded (see _segments)."""
    return inner[: len(outer)] == outer


def _overlaps(a: List[str], b: List[str]) -> bool:
    """True if `a` and `b` name the same tree, or either is nested under
    the other — in other words, they are not two genuinely separate trees.
    Segments must already be casefolded (see _segments)."""
    return _is_inside_or_equal(a, b) or _is_inside_or_equal(b, a)


def _check_relative_path(path: Path, key: str, value: str) -> None:
    """Reject a path-valued room.conf entry that could escape the room root.

    A value must be non-empty, must not be absolute, must not contain a
    '..' segment, and must not normalise to the room root itself (e.g. '.'
    or './' — those name the room, not a subdirectory of it, and a tool
    that deletes-and-rebuilds "the tree at this path" would delete the
    whole room). Segment membership is checked after splitting on '/', not
    by substring search — subsection directories legitimately contain dots
    (e.g. '11.2_site-reports'), and a substring check would misfire on them.
    """
    if not value:
        raise RoomConfError(f"{path}: {key} is empty — it must be a relative path")
    if value.startswith("/"):
        raise RoomConfError(
            f"{path}: {key} {value!r} is an absolute path — "
            "it must be relative to the room root"
        )
    if ".." in value.split("/"):
        raise RoomConfError(
            f"{path}: {key} {value!r} contains a '..' segment — "
            "it must stay inside the room root"
        )
    if not _segments(value):
        raise RoomConfError(
            f"{path}: {key} {value!r} normalises to the room root itself — "
            "it must name a real subdirectory of the room"
        )


def _check_tree_layout(path: Path, values: Dict[str, str]) -> None:
    """BLIND_TREE, FLAGGED_TREE and KEY_ROOT must sit far enough apart that
    build_flagged_tree's delete-and-rebuild of FLAGGED_TREE can never
    destroy or leak into the wrong tree.

    BLIND_TREE must not equal, contain, or be contained by either of the
    other two: overlapping with FLAGGED_TREE means rebuilding the flagged
    tree could delete the blind room handed to the tool under test, or —
    if FLAGGED_TREE is nested inside BLIND_TREE — plant answer-key
    material inside it, which is a leak, not just a delete. Overlapping
    with KEY_ROOT means rebuilding the flagged tree could delete other
    answer-key material (findings.yaml, the subset manifest, ...) that
    happens to live above it.

    FLAGGED_TREE nested *inside* KEY_ROOT is the intended layout, not a
    hazard — the flagged twin is answer-key material, and answer-key
    material lives under KEY_ROOT by project convention (e.g.
    KEY_ROOT="_key", FLAGGED_TREE="_key/flagged"). The dangerous direction
    is the reverse: KEY_ROOT at or under FLAGGED_TREE, which would mean
    rebuilding the flagged tree deletes KEY_ROOT — and everything else
    under it — right along with it.
    """
    blind = _segments(values["BLIND_TREE"])
    flagged = _segments(values["FLAGGED_TREE"])
    key = _segments(values["KEY_ROOT"])

    if _overlaps(blind, flagged):
        raise RoomConfError(
            f"{path}: BLIND_TREE {values['BLIND_TREE']!r} and FLAGGED_TREE "
            f"{values['FLAGGED_TREE']!r} must be separate trees — neither "
            "may equal, contain, or be contained by the other"
        )
    if _overlaps(blind, key):
        raise RoomConfError(
            f"{path}: BLIND_TREE {values['BLIND_TREE']!r} and KEY_ROOT "
            f"{values['KEY_ROOT']!r} must be separate trees — neither may "
            "equal, contain, or be contained by the other"
        )
    if _is_inside_or_equal(key, flagged):
        raise RoomConfError(
            f"{path}: KEY_ROOT {values['KEY_ROOT']!r} must not equal or sit "
            f"inside FLAGGED_TREE {values['FLAGGED_TREE']!r} — rebuilding "
            "the flagged tree would delete the rest of the room's "
            "answer-key material along with it (FLAGGED_TREE nested inside "
            "KEY_ROOT is fine; the reverse is not)"
        )


def _parse_line(line: str) -> tuple[str, str] | None:
    """Parse KEY=VALUE, handling quoted and bare values plus comments.

    Raises RoomConfError if the line has a syntax error (e.g., unterminated quote).
    Returns (key, value) or None if the line is malformed but not a syntax error.

    For quoted values: KEY="VALUE" where VALUE can contain # literally.
    For bare values: KEY=VALUE where a # preceded by whitespace starts a comment.
    """
    match = re.match(r'^([A-Z][A-Z0-9_]*)=(.*)$', line)
    if not match:
        return None

    key, remainder = match.groups()

    if remainder.startswith('"'):
        # Quoted value - find closing quote
        i = 1
        while i < len(remainder):
            if remainder[i] == '"':
                # Found closing quote
                value = remainder[1:i]
                after_quote = remainder[i+1:]

                # After the closing quote, only whitespace and optional # comment are allowed.
                # A # must be preceded by whitespace to start a comment; #nospace is trailing text.
                # We reject input like "value"x or "value"#nospace because they cannot
                # be faithfully represented in bash, and we use silence-equals-pass
                # discipline to catch typos early rather than lose data silently.
                trailing = after_quote.lstrip()
                if trailing:
                    # There's non-whitespace content after the quote.
                    # It's only allowed if it's a properly-spaced comment (whitespace then #).
                    if not (after_quote[0] in ' \t' and trailing.startswith('#')):
                        # Either no whitespace before content, or content is not a comment
                        raise RoomConfError(f"unexpected trailing text after quoted value: {after_quote}")

                return key, value
            i += 1
        # No closing quote found - syntax error
        raise RoomConfError("unterminated quoted value")
    else:
        # Bare value - strip comment (# preceded by whitespace) and trailing whitespace
        comment_match = re.search(r'\s+#', remainder)
        if comment_match:
            value = remainder[:comment_match.start()].rstrip()
        else:
            value = remainder.rstrip()
        return key, value


@dataclass(frozen=True)
class RoomConf:
    values: Dict[str, str]
    path: Path

    def get(self, key: str) -> str:
        try:
            return self.values[key]
        except KeyError:
            raise RoomConfError(f"{self.path}: missing key {key}") from None

    def get_int(self, key: str) -> int:
        raw = self.get(key)
        try:
            return int(raw)
        except ValueError:
            raise RoomConfError(f"{self.path}: {key} is not an integer: {raw!r}") from None

    def get_list(self, key: str) -> List[str]:
        return self.get(key).split()

    def get_pattern(self, key: str) -> str:
        return self.get(key)

    def get_relative_path(self, key: str) -> str:
        """Like get(), but for a key whose value must be a safe relative
        path. Applies the same non-empty / non-absolute / no-'..' / not-the-
        room-root rule as the required PATH_KEYS, on demand — for
        path-valued keys a later tool reads that aren't in REQUIRED_KEYS
        and so aren't checked by load_room_conf automatically.
        """
        value = self.get(key)
        _check_relative_path(self.path, key, value)
        return value


def load_room_conf(path: Path) -> RoomConf:
    if not path.is_file():
        raise RoomConfError(f"no room.conf at {path}")
    values: Dict[str, str] = {}
    for line_num, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        try:
            result = _parse_line(line)
        except RoomConfError as e:
            # Re-raise with file and line info
            raise RoomConfError(f"{path}: line {line_num}: {e}") from None

        if result is None:
            # Malformed line that doesn't match KEY=VALUE pattern
            raise RoomConfError(f"{path}: line {line_num}: malformed: {raw}")

        key, value = result
        values[key] = value

    missing = [k for k in REQUIRED_KEYS if k not in values]
    if missing:
        raise RoomConfError(f"{path}: missing required keys: {', '.join(missing)}")

    for key in PATH_KEYS:
        _check_relative_path(path, key, values[key])
    _check_tree_layout(path, values)

    return RoomConf(values=values, path=path)
