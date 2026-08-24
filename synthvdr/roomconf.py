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

_LINE = re.compile(r'^([A-Z][A-Z0-9_]*)=(?:"(.*)"|(.*))$')


class RoomConfError(Exception):
    """room.conf is missing, malformed, or missing a required key."""


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
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        key, quoted, bare = match.groups()
        values[key] = quoted if quoted is not None else bare.strip()
    missing = [k for k in REQUIRED_KEYS if k not in values]
    if missing:
        raise RoomConfError(f"{path}: missing required keys: {', '.join(missing)}")
    return RoomConf(values=values, path=path)
