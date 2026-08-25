"""CLI: python3 -m synthvdr score <tool-output> --room PATH [--baseline FILE]

This is the package-level entry point (`python3 -m synthvdr ...`), distinct
from `synthvdr/qa/__main__.py` (`python3 -m synthvdr.qa`, the room QA gate
runner). `python -m synthvdr.qa` never executes this module — it runs
straight to `synthvdr/qa/__main__.py` because `synthvdr.qa` is itself a
package with its own `__main__.py`; this file only matters for `python -m
synthvdr` directly. The two CLIs do not share process, argv, or exit-code
conventions beyond the general shape borrowed below.

`_key/adjudications.yaml` is auto-loaded from the room, the same way
findings.yaml and distractors.yaml already are — there is no --adjudications
flag, deliberately, so there is only one convention for how an answer-key
artefact reaches this CLI. Applied only to the primary tool output being
scored, never to --baseline: adjudications reference tool_index positions
in one specific ToolOutput.findings list, and a baseline run is ordinarily a
different tool output entirely.

Exit codes: 0 on success — including a run whose provenance could not be
verified, because the usual reason `_key/manifest.json` is missing is that
this room has not been through /vdr-package yet, and that is reported
plainly inside the rendered scorecard rather than being treated as a
failure. 2 if room.conf or the answer key could not be loaded, if the tool
output (or --baseline file) could not be read or parsed — including a
structurally invalid tool-output file (synthvdr.score.ToolOutputError:
e.g. a JSON root that is not an object, or a markdown report with no
parseable findings at all) — if the tool output's room_hash provably names
a different room than the one being scored (synthvdr.score.ProvenanceError),
or if `_key/adjudications.yaml` exists but is malformed or cannot be
reconciled against this run (synthvdr.score.AdjudicationError) — grouped
with the other could-not-even-run failures, not with a scoring result,
because in every one of these cases no trustworthy scorecard was produced
at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .roomconf import RoomConfError, load_room_conf
from .schema import FindingSet, SchemaError, load_distractors, load_findings
from .score import (
    AdjudicationError,
    ProvenanceError,
    ToolOutput,
    ToolOutputError,
    check_provenance,
    diff_scorecards,
    load_adjudications_for_room,
    load_tool_output,
    render_scorecard,
    score,
)


def _load_room(room: Path):
    conf = load_room_conf(room / "room.conf")
    key_root = room / conf.get("KEY_ROOT")
    findings_path = key_root / "findings.yaml"
    distractors_path = key_root / "distractors.yaml"
    findings = load_findings(findings_path) if findings_path.is_file() else FindingSet([], "")
    distractors = load_distractors(distractors_path) if distractors_path.is_file() else []
    return conf, findings, distractors


def _load_output(path: Path) -> tuple[ToolOutput | None, str | None]:
    try:
        return load_tool_output(path), None
    except OSError as exc:
        return None, f"could not read {path}: {exc}"
    except (ValueError, ToolOutputError) as exc:
        return None, f"could not parse {path}: {exc}"


def _run_score(args: argparse.Namespace) -> int:
    try:
        conf, findings, distractors = _load_room(args.room)
    except (RoomConfError, SchemaError) as exc:
        print(f"synthvdr score: {exc}".replace("\n", " "), file=sys.stderr)
        return 2
    del conf  # not needed beyond locating findings/distractors above

    output, error = _load_output(args.tool_output)
    if error is not None:
        print(f"synthvdr score: {error}".replace("\n", " "), file=sys.stderr)
        return 2

    try:
        provenance = check_provenance(args.room, output)
    except ProvenanceError as exc:
        print(f"synthvdr score: {exc}".replace("\n", " "), file=sys.stderr)
        return 2

    try:
        adjudications, adjudication_summary = load_adjudications_for_room(args.room, output, findings)
    except AdjudicationError as exc:
        print(f"synthvdr score: {exc}".replace("\n", " "), file=sys.stderr)
        return 2

    card = score(output, findings, distractors, adjudications=adjudications)
    print(
        render_scorecard(
            card, output, findings, provenance=provenance, adjudication_summary=adjudication_summary
        )
    )

    if args.baseline is not None:
        baseline_output, error = _load_output(args.baseline)
        if error is not None:
            print(f"synthvdr score: baseline {error}".replace("\n", " "), file=sys.stderr)
            return 2
        baseline_card = score(baseline_output, findings, distractors)
        print()
        print(diff_scorecards(baseline_card, card))

    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="synthvdr", description="synth-vdr tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    score_parser = subparsers.add_parser(
        "score", help="Score a tool's output against the room's answer key."
    )
    score_parser.add_argument("tool_output", type=Path)
    score_parser.add_argument("--room", type=Path, default=Path("."))
    score_parser.add_argument("--baseline", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.command == "score":
        return _run_score(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover - argparse exits first
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
