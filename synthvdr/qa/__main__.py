"""CLI: python3 -m synthvdr.qa [--room PATH] [--strict]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..roomconf import load_room_conf
from ..schema import FindingSet, load_distractors, load_findings
from . import ALL_GATES
from .runner import GateContext, run_gates


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="synthvdr.qa", description="Run the room QA gates.")
    parser.add_argument("--room", type=Path, default=Path("."))
    parser.add_argument("--strict", action="store_true", help="treat skipped gates as failures")
    args = parser.parse_args(argv)

    conf = load_room_conf(args.room / "room.conf")
    key_root = args.room / conf.get("KEY_ROOT")
    findings_path = key_root / "findings.yaml"
    distractors_path = key_root / "distractors.yaml"
    findings = load_findings(findings_path) if findings_path.is_file() else FindingSet([], "")
    distractors = load_distractors(distractors_path) if distractors_path.is_file() else []

    print(f"{conf.get('ROOM_CODENAME')} — QA check")
    print()
    ctx = GateContext(
        room=args.room, conf=conf, findings=findings, distractors=distractors, strict=args.strict
    )
    return run_gates(ctx, ALL_GATES)


if __name__ == "__main__":
    sys.exit(main())
