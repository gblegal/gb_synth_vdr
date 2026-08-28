"""Parsing for room.conf — the single source of room constants.

Shell-sourceable KEY="VALUE" so tools/check.sh can source the same file.
"""

from __future__ import annotations

import itertools
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

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

# The room root is not a room.conf key, but it participates in every
# pairwise comparison as a first-class member of the tree set: no configured
# tree may BE the room, and every configured tree must live inside it.
ROOM_ROOT_LABEL = "the room root"

# The single sanctioned overlap between two configured trees, as
# (inner_key, outer_key). FLAGGED_TREE nested inside KEY_ROOT is the
# canonical, required layout (KEY_ROOT="_key", FLAGGED_TREE="_key/flagged"):
# the flagged twin IS answer-key material, and answer-key material lives
# under KEY_ROOT by this project's Global Constraints. It is safe only
# because Property 1 (see resolve_tree_map) pins the flagged tree to the
# literal declared path — it can be under KEY_ROOT, but nowhere else that a
# symlink might reach. Every other overlap between two configured trees, in
# either direction, is rejected.
SANCTIONED_NESTING = ("FLAGGED_TREE", "KEY_ROOT")


class RoomConfError(Exception):
    """room.conf is missing, malformed, or missing a required key."""


def _casefolded_parts(path: Path) -> Tuple[str, ...]:
    """`path`'s parts, casefolded — unconditionally, not gated on `os.name`
    or a runtime probe of the host filesystem.

    macOS and Windows treat 'data-room' and 'DATA-ROOM' as one directory,
    and `Path.resolve()` does NOT rewrite either spelling to match what is
    actually on disk, so a raw string comparison of two resolved paths sees
    two unrelated trees where the filesystem sees one. Folding
    unconditionally means the same room.conf is accepted or rejected
    identically on a case-sensitive host and a case-insensitive one. That is
    deliberately over-strict on Linux, where two trees differing only in
    case genuinely are separate — the safe direction, and nothing this
    project generates relies on the distinction.
    """
    return tuple(part.casefold() for part in path.parts)


def _is_inside(inner: Path, outer: Path) -> bool:
    """True if `inner` IS `outer`, or is nested under it, compared
    case-insensitively (see _casefolded_parts). Both must already be
    resolved — absolute and symlink-free."""
    inner_parts = _casefolded_parts(inner)
    outer_parts = _casefolded_parts(outer)
    return inner_parts[: len(outer_parts)] == outer_parts


def _overlaps(a: Path, b: Path) -> bool:
    """True if `a` and `b` name the same directory, or either is nested
    under the other — they are not two genuinely separate trees by path
    alone. This is a string comparison: it cannot see two differently
    *spelled* paths that the filesystem itself resolves to one directory by
    a route other than case (Unicode normalisation, a hardlink, a bind
    mount). That is what _same_file is for."""
    return _is_inside(a, b) or _is_inside(b, a)


def _same_file(a: Path, b: Path) -> bool:
    """True if `a` and `b` are the same directory entry on disk — same
    device and inode, per os.path.samefile. Catches every aliasing route a
    path-string comparison cannot see at all, without having to enumerate
    which one is in play. False (not an error) if either path does not
    exist: nothing can be aliased to a path with nothing there, and nothing
    that does not exist can be destroyed."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


_FINDING_PREFIX_TOKEN = re.compile(r"^[A-Z][A-Z0-9]*$")


def _check_finding_prefixes(path: Path, key: str, value: str) -> None:
    """Reject a FINDING_PREFIXES value that would degrade
    synthvdr.qa.leakage.finding_id_pattern's regex into something
    dangerously broad.

    That function interpolates this value directly into
    `\\b(?:{prefixes})-\\d+\\b` — an empty value, an empty segment (from a
    leading, trailing, or doubled '|'), or a segment that is not a
    plausible prefix token collapses the alternation towards `-\\d+`, which
    then matches ordinary hyphenated text ("page 12-15", "2020-2021") as if
    it were a finding ID. Checked once, here, at load time — reachable by an
    authoring typo in room.conf, not just in theory — rather than trusted
    at every call site that builds a pattern from it.
    """
    if not value:
        raise RoomConfError(
            f"{path}: {key} is empty — it must be one or more prefix tokens separated by '|'"
        )
    for segment in value.split("|"):
        if not segment:
            raise RoomConfError(
                f"{path}: {key} {value!r} contains an empty segment — "
                "check for a leading, trailing, or doubled '|'"
            )
        if not _FINDING_PREFIX_TOKEN.match(segment):
            raise RoomConfError(
                f"{path}: {key} {value!r} contains {segment!r}, which is not "
                "a plausible prefix token — expected uppercase letters and "
                "digits only, starting with a letter"
            )


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
    if posixpath.normpath(value) == ".":
        raise RoomConfError(
            f"{path}: {key} {value!r} normalises to the room root itself — "
            "it must name a real subdirectory of the room"
        )


def _realpath(path: Path) -> Path:
    """`Path.resolve()` without the version-dependent behaviour on symlink loops.

    Python 3.12 and earlier raise `RuntimeError("Symlink loop from ...")` out of
    `Path.resolve(strict=False)`; 3.13 and later return the unresolved path
    instead. This package declares `requires-python = ">=3.9"`, so both are in
    scope, and the difference is not cosmetic: a room containing a symlink loop
    got a clean `TwinError` naming the tree on 3.13+ and a bare `RuntimeError`
    from inside pathlib on 3.11, escaping every handler this module has.

    `os.path.realpath` has the 3.13+ behaviour on every supported version, so
    using it here makes the room's own defect surface the same way everywhere —
    as the OSError the caller already handles, at the point it tries to use the
    path. Found by CI's first run, which is on 3.11.
    """
    return Path(os.path.realpath(path))


def resolve_tree_map(
    room: Path,
    values: Mapping[str, str],
    keys: Sequence[str] = PATH_KEYS,
    where: Path | None = None,
) -> Dict[str, Path]:
    """Property 1 — a configured tree must live exactly where it says it does.

    Returns {ROOM_ROOT_LABEL: <resolved room>, KEY: <resolved tree>, ...}
    for every key in `keys`, having proved for each that

        (room / value).resolve() is a proper subdirectory of room.resolve()
        (room / value).resolve() == room.resolve() / normalised(value)

    If any component of the configured path is a symlink that redirects —
    the final component or any ancestor — those two differ, and the value is
    rejected. That kills the whole symlink class in one rule rather than
    enumerating the shapes it can take, and it needs no separate handling
    for "the link points outside the room" versus "the link points at
    another configured tree": either way the tree is not where it says it
    is, so it is not usable.

    The containment half is not implied by the string checks in
    _check_relative_path, which are POSIX-shaped: on Windows a value like
    'C:\\Windows' or a UNC path is absolute without starting with '/', so it
    passes those and then resolves cleanly to itself — equal to its own
    declared path, and nowhere near the room. Requiring containment of the
    RESOLVED path states the rule in a way that does not depend on knowing
    what an absolute path looks like on the host.

    This is the only check here that touches the filesystem, and it is why
    it must run at build time as well as at load time: a symlink planted
    after load_room_conf returned would otherwise slip straight through.
    """
    where = room if where is None else where
    room_resolved = _realpath(room)
    resolved: Dict[str, Path] = {ROOM_ROOT_LABEL: room_resolved}
    for key in keys:
        if key not in values:
            raise RoomConfError(f"{where}: missing key {key}")
        value = values[key]
        _check_relative_path(where, key, value)
        declared = room_resolved.joinpath(*posixpath.normpath(value).split("/"))
        actual = _realpath(room / value)
        if not _is_inside(actual, room_resolved) or _casefolded_parts(actual) == _casefolded_parts(
            room_resolved
        ):
            raise RoomConfError(
                f"{where}: {key} {value!r} resolves outside the room root, or "
                f"to the room root itself — it lands on {actual}, and the room "
                f"is {room_resolved}. Every configured tree must be a proper "
                "subdirectory of the room."
            )
        if actual != declared:
            raise RoomConfError(
                f"{where}: {key} {value!r} does not resolve to where it says "
                f"it lives — it lands on {actual}, not {declared}. Some "
                "component of the path is a symlink that redirects it; a "
                "configured tree must be the literal relative path under the "
                "room root, with no component redirected elsewhere."
            )
        resolved[key] = declared
    return resolved


def check_tree_identity(
    room: Path,
    values: Mapping[str, str],
    keys: Sequence[str] = PATH_KEYS,
    where: Path | None = None,
) -> Dict[str, Path]:
    """Properties 1 and 2 together. Returns the resolved tree map.

    Property 2 — every pair is checked, generically. The pairs come from
    itertools.combinations over the resolved map, never from a hardcoded
    list, so a path key added to PATH_KEYS participates in every comparison
    automatically with no second place to remember to update:

      * two trees that are the same directory — by casefolded path, or by
        device and inode via os.path.samefile — are always rejected,
        including a tree that is the room root itself;
      * one tree nested inside another is rejected, EXCEPT where the outer
        member is the room root (every tree must live in the room) or the
        pair is SANCTIONED_NESTING (FLAGGED_TREE inside KEY_ROOT).

    Overlap in either direction is destructive or leaky: build_flagged_tree
    deletes and rebuilds FLAGGED_TREE, so a tree at or under it is destroyed,
    and a tree above it has answer-key material planted inside it.
    """
    where = room if where is None else where
    resolved = resolve_tree_map(room, values, keys, where)
    for (label_a, path_a), (label_b, path_b) in itertools.combinations(resolved.items(), 2):
        if _casefolded_parts(path_a) == _casefolded_parts(path_b) or _same_file(path_a, path_b):
            raise RoomConfError(
                f"{where}: {label_a} ({path_a}) and {label_b} ({path_b}) are "
                "the same directory — every configured tree must be distinct "
                "from every other and from the room root"
            )
        if _is_inside(path_a, path_b):
            inner, outer = (label_a, path_a), (label_b, path_b)
        elif _is_inside(path_b, path_a):
            inner, outer = (label_b, path_b), (label_a, path_a)
        else:
            continue
        if outer[0] == ROOM_ROOT_LABEL:
            continue
        if (inner[0], outer[0]) == SANCTIONED_NESTING:
            continue
        raise RoomConfError(
            f"{where}: {inner[0]} ({inner[1]}) sits inside {outer[0]} "
            f"({outer[1]}) — rebuilding the flagged tree deletes everything "
            "under it and writes answer-key material above it, so no "
            "configured tree may contain another (the one exception is "
            f"{SANCTIONED_NESTING[0]} inside {SANCTIONED_NESTING[1]})"
        )
    return resolved


def _parse_line(line: str) -> tuple[str, str] | None:
    """Parse one KEY=VALUE line the way bash would source it.

    Returns (key, value), or None if the line is not a KEY=VALUE assignment
    at all — `load_room_conf` turns that None into a RoomConfError naming
    the line number, so a line this function cannot read is never simply
    skipped. Raises RoomConfError directly for a line that IS an assignment
    but is malformed inside it: an unterminated quote, or text after the
    closing quote that is not a properly spaced comment.

    A quoted value may contain '#' literally; in a bare value a '#'
    preceded by whitespace starts a comment. Those are bash's rules, not a
    config format invented here: room.conf is "shell-sourceable KEY=VALUE so
    tools/check.sh can source the same file" (module docstring), so what
    bash would do with a line is the specification this has to meet, whether
    or not any shipped script sources it today.
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

                # After the closing quote, only whitespace and an optional
                # comment are allowed. The comment's '#' must be preceded by
                # whitespace; "value"#nospace is trailing text, not a comment.
                #
                # RAISING IS THE POINT, AND THE ALTERNATIVE IS THE FAILURE THIS
                # PROJECT IS BUILT AGAINST. "value"x and "value"#nospace are
                # things bash would read differently from any reading we could
                # choose here, so the only honest options are to reject the line
                # or to keep a value the room will not actually be configured
                # with. Silence must never equal a pass — qa/runner.py's module
                # docstring records that exact silence having already hidden real
                # defects for two phases of a previous build — so the line is
                # rejected, by number, while the author is still editing it.
                #
                # (This comment previously said the opposite: that the rejection
                # was there because "we use silence-equals-pass discipline to
                # catch typos early". That named the project's discipline
                # backwards, in the one place a reader is most likely to be
                # learning the vocabulary from.)
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
    # Where each key was FIRST set, so a repeat can name both lines rather
    # than just the one it happened to be standing on.
    set_at: Dict[str, int] = {}
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
        # A key set twice used to take the last value, silently — the only
        # malformation in this file that was not rejected by line number.
        # Every other reader of room.conf is downstream of this dict, so the
        # room would then be built and gated against a value the author can
        # see in the file and did not intend, with nothing anywhere able to
        # notice: silence standing in for a pass, in the loader for the file
        # that decides what every gate is checking against.
        #
        # Rejected whether or not the two values agree. An identical repeat is
        # not the harmless case it looks like — room.conf is a dozen
        # hand-written lines, so a key appearing twice means the author edited
        # one copy and left the other, and whether the values happen to match
        # is luck about which copy was edited, not evidence of intent. Contrast
        # namecheck._declared_candidates, which does let an identical repeat
        # through: there the two sides carry the same meaning and there is
        # nothing to arbitrate.
        if key in values:
            raise RoomConfError(
                f"{path}: line {line_num}: {key} is set again here to {value!r}, "
                f"having already been set to {values[key]!r} on line {set_at[key]} "
                f"— the later value would silently win; remove one of the two lines"
            )
        values[key] = value
        set_at[key] = line_num

    missing = [k for k in REQUIRED_KEYS if k not in values]
    if missing:
        raise RoomConfError(f"{path}: missing required keys: {', '.join(missing)}")

    _check_finding_prefixes(path, "FINDING_PREFIXES", values["FINDING_PREFIXES"])

    # Property 1 + Property 2 over every path-valued key at once. The room
    # root is path.parent — room.conf sits at the top of the room it
    # describes. This is a load-time snapshot: build_flagged_tree runs the
    # identical check again against the room it is actually handed, because
    # a symlink planted between the two would otherwise go unseen.
    check_tree_identity(path.parent, values, PATH_KEYS, path)

    return RoomConf(values=values, path=path)
