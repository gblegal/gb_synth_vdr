"""Parsing for room.conf — the single source of room constants.

Shell-sourceable KEY="VALUE" so tools/check.sh can source the same file.
"""

from __future__ import annotations

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


class RoomConfError(Exception):
    """room.conf is missing, malformed, or missing a required key."""


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
    return RoomConf(values=values, path=path)
