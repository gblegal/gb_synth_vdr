"""CLI: python3 -m synthvdr {score|score-classification|answerkey|corrupt|manifest} ...

`score <tool-output> --room PATH [--baseline FILE]` scores a findings report;
`score-classification <output> --room PATH [--key FILE]` scores a
classification run against `_key/answer-key.jsonl` (or the key named by
--key — e.g. the corrupted twin's); `answerkey` builds that key from
`_key/labels.yaml`; `corrupt` writes the deliberately dirtied twin under
`corrupted/` (or `--out`) so eval runs are not scored against a suspiciously
clean room, and gives the twin its own manifest so a run against it can be
verified rather than only assumed; `manifest` writes `_key/manifest.json`,
the clean room's content hash, which is /vdr-package's step 4.

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

from .manifest import MANIFEST_NAME
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


def _run_score_classification(args) -> int:
    """`score-classification <output> --room .` — the classification twin of
    `score`, graded against `_key/answer-key.jsonl` rather than
    findings.yaml. Same provenance discipline (a proven room_hash mismatch
    aborts before any scorecard prints; a missing hash scores UNVERIFIED),
    same exit-code convention: 2 groups every could-not-even-run failure —
    an unloadable room.conf or key, an unparseable output, and a coverage
    mismatch between output and key, because a score over a different
    document set than the key's is not a partial result, it is a wrong one.
    """
    from .classify_score import (
        ClassificationOutputError,
        ClassificationScoreError,
        load_classification_key,
        load_classification_output,
        render_classification_scorecard,
        score_classification,
    )

    try:
        conf = load_room_conf(args.room / "room.conf")
    except RoomConfError as exc:
        print(f"synthvdr score-classification: {exc}".replace("\n", " "), file=sys.stderr)
        return 2

    try:
        output = load_classification_output(args.tool_output)
    except OSError as exc:
        print(
            f"synthvdr score-classification: could not read {args.tool_output}: {exc}".replace("\n", " "),
            file=sys.stderr,
        )
        return 2
    except (ValueError, ClassificationOutputError) as exc:
        print(
            f"synthvdr score-classification: could not parse {args.tool_output}: {exc}".replace("\n", " "),
            file=sys.stderr,
        )
        return 2

    # A run scored against the corrupted twin is a run against a different
    # room: its documents were renamed, misfiled and noised, so its hash is
    # its own. Provenance is checked against the manifest beside the key in
    # use — `corrupted-heavy/manifest.json` for a `--key
    # corrupted-heavy/answer-key.jsonl` run — which is what makes a twin run
    # verifiable at all rather than permanently UNVERIFIED.
    manifest_path = args.key.parent / MANIFEST_NAME if args.key else None
    try:
        provenance = check_provenance(args.room, output, manifest_path=manifest_path)
    except ProvenanceError as exc:
        print(f"synthvdr score-classification: {exc}".replace("\n", " "), file=sys.stderr)
        return 2

    try:
        key = load_classification_key(args.room, conf, key_path=args.key)
        card = score_classification(output, key)
    except ClassificationScoreError as exc:
        print(f"synthvdr score-classification: {exc}".replace("\n", " "), file=sys.stderr)
        return 2
    except ClassificationOutputError as exc:
        print(f"synthvdr score-classification: {exc}".replace("\n", " "), file=sys.stderr)
        return 2

    print(render_classification_scorecard(card, output, provenance=provenance))
    return 0


def _run_corrupt(args) -> int:
    """`corrupt --room . [--seed N] [--profile light|heavy] [--out DIR]` —
    write the corrupted twin. The profile name is validated here rather than via
    argparse choices so an unknown name returns 2 through the same
    plain-stderr path as every other could-not-even-run failure, instead
    of argparse's SystemExit."""
    from .corrupt import PROFILES, CorruptError, corrupt_room

    if args.profile not in PROFILES:
        print(
            f"synthvdr corrupt: unknown profile {args.profile!r} — the "
            f"profiles are: {', '.join(sorted(PROFILES))}",
            file=sys.stderr,
        )
        return 2
    try:
        conf = load_room_conf(args.room / "room.conf")
        report = corrupt_room(
            args.room,
            conf,
            seed=args.seed,
            profile=PROFILES[args.profile],
            out_dir=args.out,
        )
    except (RoomConfError, CorruptError) as exc:
        print(f"synthvdr corrupt: {exc}".replace("\n", " "), file=sys.stderr)
        return 2
    print(
        f"{conf.get('ROOM_CODENAME')} — corrupted twin written to "
        f"{report.out} (seed {args.seed}, profile {args.profile}): "
        f"{report.documents} documents — {report.renamed} renamed, "
        f"{report.misfiled} misfiled, {report.noised} noised, "
        f"{report.truncated} truncated. The answer key follows the mess; "
        "the truth does not."
    )
    return 0


def _run_answerkey(args) -> int:
    from .answer_key import (
        VOCABULARY_NAME,
        AnswerKeyError,
        build_answer_key,
        load_vocabulary,
    )
    from .domain import DEFAULT_DOMAIN_ROOT, DomainError, load_domain

    try:
        conf = load_room_conf(args.room / "room.conf")
    except RoomConfError as exc:
        print(f"synthvdr answerkey: {exc}".replace("\n", " "), file=sys.stderr)
        return 2

    # An explicit --vocabulary wins; otherwise the room's own copy is used when it
    # has one. Defaulting matters more than it looks: the check is the only thing
    # standing between a free-typed label and a key that scores a drift as a
    # classifier miss, and before this it was skipped entirely whenever the flag
    # was forgotten — silently, with a written key to show for it.
    vocabulary_path = args.vocabulary
    if vocabulary_path is None:
        candidate = args.room / conf.get_relative_path("KEY_ROOT") / VOCABULARY_NAME
        vocabulary_path = candidate if candidate.is_file() else None
    elif not vocabulary_path.is_file():
        print(
            f"synthvdr answerkey: no vocabulary file at {vocabulary_path}",
            file=sys.stderr,
        )
        return 2

    vocabulary = load_vocabulary(vocabulary_path) if vocabulary_path else None
    # Only an eval room is warned. An exemplar room has no classifier vocabulary
    # by definition — it teaches the classifier rather than scoring it — so the
    # warning would be noise there, and noise is how a real warning gets ignored.
    if vocabulary is None and conf.values.get("ROOM_ROLE") == "eval":
        print(
            "synthvdr answerkey: no classifier vocabulary given and none at "
            f"{conf.get('KEY_ROOT')}/{VOCABULARY_NAME} — this is an eval room, "
            "so its labels are going into the key unchecked against the "
            "classifier's document list.",
            file=sys.stderr,
        )

    try:
        pack = load_domain(DEFAULT_DOMAIN_ROOT)
        out = build_answer_key(args.room, conf, pack, vocabulary=vocabulary)
    except (RoomConfError, DomainError, AnswerKeyError) as exc:
        print(f"synthvdr answerkey: {exc}".replace("\n", " "), file=sys.stderr)
        return 2
    lines = out.read_text(encoding="utf-8").count("\n")
    print(f"{conf.get('ROOM_CODENAME')} — answer key written to {out}: {lines} documents.")
    return 0


def _run_manifest(args) -> int:
    """`manifest --room . [--built YYYY-MM-DD]` — write `_key/manifest.json`.

    Step 4 of /vdr-package. The hash construction used to be a code block in
    that skill's markdown, run by the agent with an instruction never to
    retype it; it lives in `synthvdr.manifest` now, tested, with the skill
    running this command. `check_provenance` compares the result as a plain
    string, so a hash built even slightly differently does not fail loudly —
    it reports that a correct tool output came from a different room.

    The clock is read HERE and nowhere deeper: `synthvdr.manifest` takes
    `built` as a value, because determinism is a project-wide rule and the
    corrupted twin's manifest (which must stay byte-identical for a given
    room, seed and profile) carries no date at all. `--built` makes this
    command reproducible for anyone who needs it to be.
    """
    from datetime import date

    from .manifest import build_room_manifest, compute_content_hash, write_manifest
    from .schema import SchemaError, load_findings

    try:
        conf = load_room_conf(args.room / "room.conf")
    except RoomConfError as exc:
        print(f"synthvdr manifest: {exc}".replace("\n", " "), file=sys.stderr)
        return 2

    blind_root = args.room / conf.get_relative_path("BLIND_TREE")
    if not blind_root.is_dir():
        print(
            f"synthvdr manifest: no blind tree at {blind_root} — there is "
            "nothing to hash, so a manifest here would certify an empty room",
            file=sys.stderr,
        )
        return 2

    key_root = args.room / conf.get_relative_path("KEY_ROOT")
    findings_count = 0
    findings_path = key_root / "findings.yaml"
    if findings_path.is_file():
        try:
            findings_count = len(load_findings(findings_path).findings)
        except SchemaError as exc:
            print(f"synthvdr manifest: {exc}".replace("\n", " "), file=sys.stderr)
            return 2

    content_hash, documents = compute_content_hash(blind_root)
    manifest = build_room_manifest(
        room_codename=conf.get("ROOM_CODENAME"),
        content_hash=content_hash,
        documents=documents,
        findings=findings_count,
        built=args.built or date.today().isoformat(),
    )
    out = key_root / MANIFEST_NAME
    write_manifest(out, manifest)
    print(
        f"{manifest['room']} — manifest written to {out}: {documents} documents, "
        f"{findings_count} findings.\n"
        f"content_hash: {content_hash}\n"
        "Hand that hash to whoever produces the tool output scored against this "
        "room; it belongs in the output's own room_hash field."
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="synthvdr", description="synth-vdr tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    answerkey_parser = subparsers.add_parser(
        "answerkey",
        help="Write _key/answer-key.jsonl from _key/labels.yaml and the "
        "domain pack's classifier vocabulary.",
    )
    answerkey_parser.add_argument("--room", type=Path, default=Path("."))
    answerkey_parser.add_argument(
        "--vocabulary",
        type=Path,
        default=None,
        help="File of legitimate document-type names, one per line — the "
        "classifier's own list; labels outside it are refused. Defaults to "
        "<KEY_ROOT>/classifier-vocab.txt when the room has one.",
    )

    score_parser = subparsers.add_parser(
        "score", help="Score a tool's output against the room's answer key."
    )
    score_parser.add_argument("tool_output", type=Path)
    score_parser.add_argument("--room", type=Path, default=Path("."))
    score_parser.add_argument("--baseline", type=Path, default=None)

    classify_parser = subparsers.add_parser(
        "score-classification",
        help="Score a tool's classification output against "
        "_key/answer-key.jsonl — document type, primary pile, the "
        "not-sure count and a confusion table.",
    )
    classify_parser.add_argument("tool_output", type=Path)
    classify_parser.add_argument("--room", type=Path, default=Path("."))
    classify_parser.add_argument(
        "--key",
        type=Path,
        default=None,
        help="Score against this answer key instead of the room's own — "
        "e.g. corrupted/answer-key.jsonl for a run over the corrupted twin.",
    )

    corrupt_parser = subparsers.add_parser(
        "corrupt",
        help="Write corrupted/ — a deliberately dirtied twin of the blind "
        "tree with a rewritten answer key, for eval runs that should not "
        "be scored against a suspiciously clean room.",
    )
    corrupt_parser.add_argument("--room", type=Path, default=Path("."))
    corrupt_parser.add_argument("--seed", type=int, default=1)
    corrupt_parser.add_argument(
        "--profile",
        default="light",
        help="Corruption intensity: 'light' (a well-run modern deal) or "
        "'heavy' (the messy carve-out room).",
    )
    corrupt_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Where to write the twin (default: corrupted/ at the room root). "
        "The default is one fixed name, so pass one directory per profile — "
        "corrupted-light/, corrupted-heavy/ — to keep both twins of a room. "
        "Deleted and rebuilt on every run; refused if it is or touches a "
        "configured tree or the room root.",
    )

    manifest_parser = subparsers.add_parser(
        "manifest",
        help="Write _key/manifest.json — the room's content hash, which is "
        "what makes a scored run's provenance checkable.",
    )
    manifest_parser.add_argument("--room", type=Path, default=Path("."))
    manifest_parser.add_argument(
        "--built",
        default=None,
        help="Build date to record (default: today). Pass one to make the "
        "manifest reproducible.",
    )

    args = parser.parse_args(argv)

    if args.command == "answerkey":
        return _run_answerkey(args)
    if args.command == "score":
        return _run_score(args)
    if args.command == "score-classification":
        return _run_score_classification(args)
    if args.command == "corrupt":
        return _run_corrupt(args)
    if args.command == "manifest":
        return _run_manifest(args)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover - argparse exits first
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
