"""CLI: python3 -m synthvdr.qa [--room PATH] [--strict]

Exit codes: 0 clean; 1 on any gate FAIL, or any SKIP under --strict; 2 if the
room could not even be loaded (a missing or malformed room.conf, or a
malformed answer key) — distinct from 1 so a caller can tell "the checks
found a problem" apart from "the checks never ran". room.conf and the answer
key are user-authored, and load_room_conf validates path-valued keys and
touches the filesystem, so both are expected to fail sometimes; this is the
most user-facing surface in the project, so those failures print one
readable line, never a traceback.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..roomconf import RoomConfError, load_room_conf
from ..schema import FindingSet, SchemaError, load_distractors, load_findings
from . import ALL_GATES
from .runner import GateContext, run_gates


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="synthvdr.qa", description="Run the room QA gates.")
    parser.add_argument("--room", type=Path, default=Path("."))
    parser.add_argument("--strict", action="store_true", help="treat skipped gates as failures")
    args = parser.parse_args(argv)

    try:
        conf = load_room_conf(args.room / "room.conf")
        # Computed directly from `conf`, not via a GateContext, because
        # `ctx` cannot exist yet: GateContext needs `findings`/`distractors`
        # as constructor arguments, and those need `key_root` first to find
        # findings.yaml/distractors.yaml. Do not "simplify" this to
        # `ctx.key_root` — ctx isn't built until after this block.
        key_root = args.room / conf.get("KEY_ROOT")
        findings_path = key_root / "findings.yaml"
        distractors_path = key_root / "distractors.yaml"
        findings = load_findings(findings_path) if findings_path.is_file() else FindingSet([], "")
        distractors = load_distractors(distractors_path) if distractors_path.is_file() else []
    except (RoomConfError, SchemaError) as exc:
        print(f"synthvdr.qa: {exc}".replace("\n", " "), file=sys.stderr)
        return 2

    print(f"{conf.get('ROOM_CODENAME')} — QA check")
    print()
    ctx = GateContext(
        room=args.room, conf=conf, findings=findings, distractors=distractors, strict=args.strict
    )
    return run_gates(ctx, ALL_GATES)


if __name__ == "__main__":
    sys.exit(main())
