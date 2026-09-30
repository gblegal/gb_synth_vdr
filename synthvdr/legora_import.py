"""Turn a Legora review into a tool output `python3 -m synthvdr score` can mark.

Legora reviews a blind room with the `review-a-test-data-room` skill and saves
a report in the shape that skill's `2-what-to-hand-back.md` fixes: one issue
per `##` heading, a severity line first, every document cited by its path, and
a `# Files read` list last. That is close to what `parse_markdown_report`
reads, but not close enough to score as it stands:

- Legora cites the path it saw. That starts at a mount such as
  `/workspace/documents/projects/<project name>/`, often names the tree the
  room was uploaded as, and on a rendered cut ends `.pdf` or `.docx`.
  `prematch` joins on exact strings against `.md` evidence paths, so a run
  over `data-room-pdf/` scores zero until every citation is cut back to the
  room-relative `.md` form. The cut is made at the first folder that is one
  of the room's SECTION_DIRS, which copes with any mount, any tree name and a
  project name with spaces in it — a space the parser's own path pattern
  cannot read.
- The `# Files read` list must never reach the parser. A level-1 heading does
  not start a finding, so the list would be read as the tail of the last
  issue, which would then cite every document in the room and pre-match every
  planted finding.
- Word drops the backticks, so a bare path counts as a citation when it
  starts at a section folder. A bare file name in prose ("README.md") does
  not; a backticked one does, so that a path cited without its folders
  reaches the room check and is refused rather than silently lost.

All of that leniency lives here; `score` stays strict and reads the result
as it reads any tool's JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .manifest import MANIFEST_NAME, read_content_hash
from .roomconf import RoomConf, load_room_conf
from .schema import SEVERITIES
from .score import ToolOutputError, parse_markdown_report


class LegoraImportError(Exception):
    """The report cannot be imported against this room — a path the uploaded
    cut does not hold, a report with no issues, an unreadable file, or an
    `--out` that would write into the room. Always a refusal, never a
    partial import: a scorecard built on it would not be a measurement of
    this room."""


# What a citation can name. `.pdf` and `.docx` are renders of a room's `.md`
# documents (`data-room-pdf/`, `data-room-docx/`), so they map back to it;
# `.csv` is a document in its own right, as the parser's own pattern allows.
DOCUMENT_SUFFIXES = (".md", ".pdf", ".docx", ".csv")
RENDERED_SUFFIXES = (".pdf", ".docx")

# How many paths a summary or a refusal names before it says "and N more".
LIST_LIMIT = 20


def as_markdown(rel: str) -> str:
    """A rendered document's path mapped back to the `.md` it was rendered from."""
    stem, dot, suffix = rel.rpartition(".")
    if dot and f".{suffix.lower()}" in RENDERED_SUFFIXES:
        return f"{stem}.md"
    return rel


def normalise_path(raw: str, section_dirs: Sequence[str]) -> str:
    """The room-relative `.md` path a cited path names, or the path as written
    when no component of it is a section folder — which then fails the room
    check, as it should."""
    sections = set(section_dirs)
    parts = [p for p in raw.strip().replace("\\", "/").split("/") if p not in ("", ".")]
    for index, part in enumerate(parts):
        if part in sections:
            return as_markdown("/".join(parts[index:]))
    return raw.strip()


# A backticked span, or a bare path token. The bare token's edges exclude a
# trailing full stop so a path ending a sentence is still read, but not a dot
# followed by more name (".md.bak").
_CITATION = re.compile(
    r"`([^`\n]+)`"
    r"|(?<![\w./-])([\w./-]+\.(?:md|pdf|docx|csv))(?![\w/-]|\.\w)",
    re.IGNORECASE,
)


def _citation(match: "re.Match[str]", section_dirs: Sequence[str]) -> Optional[str]:
    """The normalised path one match cites, or None if it cites nothing."""
    backticked, bare = match.group(1), match.group(2)
    raw = backticked if backticked is not None else bare
    if not raw.strip().lower().endswith(DOCUMENT_SUFFIXES):
        return None
    path = normalise_path(raw, section_dirs)
    if bare is not None and path.split("/", 1)[0] not in set(section_dirs):
        return None
    return path


def normalise_citations(text: str, section_dirs: Sequence[str]) -> str:
    """`text` with every citation rewritten as its normalised path in backticks,
    the form `parse_markdown_report` reads. Everything else is left as it is."""

    def replace(match: "re.Match[str]") -> str:
        path = _citation(match, section_dirs)
        return match.group(0) if path is None else f"`{path}`"

    return _CITATION.sub(replace, text)


def citations_in(text: str, section_dirs: Sequence[str]) -> List[str]:
    """Every document `text` cites, normalised, in the order cited."""
    found = (_citation(match, section_dirs) for match in _CITATION.finditer(text))
    return [path for path in found if path is not None]


def cut_documents(cut_root: Path) -> Set[str]:
    """The documents in the uploaded cut, as room-relative `.md` paths.

    Dotfiles and dotted folders are not documents: the subset and flagged
    trees carry an ownership marker at their root, and macOS leaves
    `.DS_Store` wherever a folder was opened."""
    documents: Set[str] = set()
    for path in cut_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(cut_root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        documents.add(as_markdown(rel.as_posix()))
    return documents


# The files-read heading, at any level from 1 to 4, in any case, with or
# without a colon. The section runs to the next heading of the same level or
# higher, so a `## Files read` placed among the issues ends at the next one.
_FILES_READ = re.compile(r"^(#{1,4})[ \t]*files[ \t]+read[ \t]*:?[ \t]*$", re.IGNORECASE | re.MULTILINE)
_FIRST_ISSUE = re.compile(r"^#{2,4}(?!#)", re.MULTILINE)
# `Model: x`, `**Model:** x`, `- **Skill**: x` — the hand-back asks for the
# plain form, and a model reaching for emphasis should not lose the line.
_FIELD = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?[*_]*(model|skill)[*_]*[ \t]*:[*_]*[ \t]*(.*?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_HEADING_STYLE = re.compile(r"^heading\s+([1-9])$", re.IGNORECASE)

# Model lines that mean the agent did not know. The skill asks for "not known":
# the probe found that an example identifier in a skill is handed straight back.
_NOT_STATED = {"", "not known", "unknown", "not stated", "n/a", "none"}


@dataclass(frozen=True)
class SplitReport:
    """A report cut into the parts the importer treats differently.

    `issues` runs from the first `##`–`####` heading and is all the parser
    sees. `files_read` is None when the report has no files-read section,
    which the summary says out loud rather than treating as "read nothing".
    """

    preamble: str
    issues: str
    files_read: Optional[str]
    model: str
    skill: str


def split_report(text: str) -> SplitReport:
    files_read = None
    heading = _FILES_READ.search(text)
    if heading:
        level = len(heading.group(1))
        following = re.compile(rf"^#{{1,{level}}}(?!#)", re.MULTILINE).search(text, heading.end())
        end = following.start() if following else len(text)
        files_read = text[heading.end():end]
        text = text[: heading.start()] + text[end:]
    first = _FIRST_ISSUE.search(text)
    preamble = text[: first.start()] if first else text
    issues = text[first.start():] if first else ""
    fields = {
        match.group(1).lower(): match.group(2).strip().strip("*_").strip()
        for match in _FIELD.finditer(preamble)
    }
    return SplitReport(
        preamble=preamble,
        issues=issues,
        files_read=files_read,
        model=fields.get("model", ""),
        skill=fields.get("skill", ""),
    )


def _docx_to_markdown(path: Path) -> Tuple[str, List[str]]:
    """A Word report as Markdown: `Title` and `Heading 1`–`9` become `#` runs
    (5 and deeper stay too deep for the parser, as in Markdown), list
    paragraphs become `- ` lines, and everything else is its text. Tables are
    not read — the hand-back asks for none — and are counted in a note so
    their loss is said, not silent."""
    try:
        import docx
    except ImportError:
        raise LegoraImportError(
            "reading a Word report needs python-docx — install the docx extra: "
            "pip install -e '.[docx]'"
        ) from None
    try:
        document = docx.Document(str(path))
    except Exception as exc:  # python-docx raises its own types for a bad package
        raise LegoraImportError(f"{path} could not be read as a Word document: {exc}") from None
    lines: List[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        heading = _HEADING_STYLE.match(style.strip())
        if not text:
            lines.append("")
        elif style.strip().lower() == "title":
            lines.append(f"# {text}")
        elif heading:
            lines.append("#" * int(heading.group(1)) + f" {text}")
        elif "list" in style.lower():
            lines.append(f"- {text}")
        else:
            lines.append(text)
    notes = []
    if document.tables:
        notes.append(
            f"the Word report has {len(document.tables)} table(s); their contents were "
            "not read — the hand-back asks for none"
        )
    return "\n".join(lines) + "\n", notes


def read_report(path: Path) -> Tuple[str, List[str]]:
    """The report as Markdown with LF line endings, and any reader notes.

    `utf-8-sig` drops a byte-order mark, which would otherwise sit in front of
    a first `##` and hide it; line endings are unified because the heading
    patterns anchor on `$`, which does not match before a `\\r`."""
    suffix = path.suffix.lower()
    if suffix == ".md":
        text, notes = path.read_text(encoding="utf-8-sig"), []
    elif suffix == ".docx":
        text, notes = _docx_to_markdown(path)
    else:
        raise LegoraImportError(
            f"{path.name}: a Legora review is read as Markdown (.md) or Word (.docx), "
            f"not {path.suffix or 'a file with no extension'}"
        )
    return text.replace("\r\n", "\n").replace("\r", "\n"), notes


def tool_name(model: str, skill: str, override: Optional[str] = None) -> str:
    """The `tool` field: `--tool` when given, else `legora/<model>` from the
    report's Model line, with the skill build in brackets when the report
    names it — the only record in the output of which build ran."""
    if override:
        return override
    stated = model.strip().rstrip(".").strip()
    name = "legora/" + (stated if stated.lower() not in _NOT_STATED else "model-not-stated")
    return f"{name} ({skill})" if skill else name


@dataclass(frozen=True)
class ImportResult:
    """The tool output an import produced, and the facts its summary reports."""

    output: dict
    report: Path
    room: Path
    cut: str
    cut_total: int
    by_severity: Dict[str, int]
    documents_cited: int
    files_read: Optional[int]
    not_read: int
    unexplained: List[str]
    cited_not_read: List[str]
    ignored: List[str]
    dropped: List[str]
    provenance: str
    notes: List[str]

    @property
    def findings(self) -> int:
        return len(self.output["findings"])


def _named(paths: Sequence[str]) -> List[str]:
    lines = [f"  {path}" for path in paths[:LIST_LIMIT]]
    if len(paths) > LIST_LIMIT:
        lines.append(f"  … and {len(paths) - LIST_LIMIT} more")
    return lines


def _unknown_message(unknown: Sequence[str], cut: str, room: Path) -> str:
    return "\n".join(
        [f"the report names {len(unknown)} path(s) that {cut}/ in {room} does not hold:"]
        + _named(unknown)
        + [
            "Nothing was written. The usual causes, most likely first: --cut names a "
            "different folder from the one uploaded to Legora; the Legora project holds "
            "a stale copy of the room; or the agent invented or mistyped a path. If it is "
            "the last, --drop-unknown imports the rest and leaves the run UNVERIFIED."
        ]
    )


def _provenance(room: Path, conf: RoomConf, unknown: Sequence[str]) -> Tuple[str, str]:
    """The room_hash to stamp, and one sentence saying why or why not.

    The stamp vouches that this report was made on this room. Legora never saw
    the manifest, so what vouches is the path check: every path the report
    names is in the room. When any was dropped, nothing vouches."""
    manifest = PurePosixPath(conf.get_relative_path("KEY_ROOT")) / MANIFEST_NAME
    if unknown:
        return "", (
            f"not stamped — {len(unknown)} path(s) the report names are not in the cut "
            "and were dropped, so it cannot be vouched for as a run on this room. The "
            "scorecard will say UNVERIFIED."
        )
    content_hash = read_content_hash(room / manifest)
    if not content_hash:
        return "", (
            f"not stamped — no content_hash in {manifest} (it is written by /vdr-package). "
            "The scorecard will say UNVERIFIED."
        )
    return content_hash, f"room_hash stamped from {manifest}: every path the report names is in this room."


def import_review(
    report: Path,
    room: Path,
    cut: Optional[str] = None,
    tool: Optional[str] = None,
    drop_unknown: bool = False,
) -> ImportResult:
    """Read a Legora review and build the tool output for `score`.

    `cut` is the folder uploaded to Legora, relative to the room; it defaults
    to BLIND_TREE and cannot be inferred, because a subset run and a
    full-room run cite identical paths. Every path the report names — in an
    issue or in the files-read list — must be a document in the cut, or the
    import is refused (LegoraImportError), unless `drop_unknown`. Documents
    the files-read list leaves out are reported, never refused: the output is
    a true record of what was found either way."""
    conf = load_room_conf(room / "room.conf")
    section_dirs = conf.get_list("SECTION_DIRS")
    cut_name = (cut or conf.get("BLIND_TREE")).strip("/")
    cut_root = room / cut_name
    if not cut_root.is_dir():
        raise LegoraImportError(
            f"there is no folder {cut_name!r} in {room}. --cut names the folder that was "
            "uploaded to Legora, relative to the room: data-room, subset, data-room-pdf "
            "or data-room-docx."
        )

    text, notes = read_report(report)
    split = split_report(text)
    issues = normalise_citations(split.issues, section_dirs)
    try:
        parsed = parse_markdown_report(issues)
    except ToolOutputError:
        raise LegoraImportError(
            f"{report.name} has no issues under '##' headings, so there is nothing to "
            "score. The hand-back puts each issue under its own '##' heading; a report "
            "in another shape has to be fixed at the skill, not guessed at here."
        ) from None

    documents = cut_documents(cut_root)
    sections = set(section_dirs)
    cited = citations_in(issues, section_dirs)
    read_list: List[str] = []
    ignored: Set[str] = set()
    if split.files_read is not None:
        for path in citations_in(split.files_read, section_dirs):
            if path.split("/", 1)[0] in sections:
                read_list.append(path)
            else:
                ignored.add(path)
    unknown = sorted({path for path in cited + read_list if path not in documents})
    if unknown and not drop_unknown:
        raise LegoraImportError(_unknown_message(unknown, cut_name, room))

    findings = []
    for finding in parsed.findings:
        kept = [d for d in dict.fromkeys(finding.documents) if d in documents]
        findings.append(
            {
                "title": finding.title,
                "severity": finding.severity,
                "documents": kept,
                "summary": finding.summary,
            }
        )
    cited_known = {d for finding in findings for d in finding["documents"]}

    if split.files_read is None:
        files_read, not_read, unexplained, cited_not_read = None, 0, [], []
    else:
        read = {path for path in read_list if path in documents}
        missed = sorted(documents - read)
        reached = {str(PurePosixPath(path).parent) for path in read}
        unexplained = [path for path in missed if str(PurePosixPath(path).parent) in reached]
        cited_not_read = sorted(cited_known - read)
        files_read, not_read = len(read), len(missed)

    room_hash, provenance = _provenance(room, conf, unknown)
    output = {
        "tool": tool_name(split.model, split.skill, tool),
        "room_hash": room_hash,
        "findings": findings,
    }
    return ImportResult(
        output=output,
        report=report,
        room=room,
        cut=cut_name,
        cut_total=len(documents),
        by_severity={s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES},
        documents_cited=len(cited_known),
        files_read=files_read,
        not_read=not_read,
        unexplained=unexplained,
        cited_not_read=cited_not_read,
        ignored=sorted(ignored),
        dropped=unknown,
        provenance=provenance,
        notes=notes,
    )


def write_output(result: ImportResult, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def render_summary(result: ImportResult, out: Path) -> str:
    counts = ", ".join(f"{s} {result.by_severity.get(s, 0)}" for s in SEVERITIES)
    lines = [
        f"Imported {result.findings} issue(s) from {result.report.name} ({counts}), "
        f"citing {result.documents_cited} document(s).",
        f"Cut: {result.cut}/, {result.cut_total} documents.",
    ]
    if result.files_read is None:
        lines.append(
            "Files read: the report has no '# Files read' section, so nothing can be said "
            "about what Legora did not read."
        )
    else:
        lines.append(
            f"Files read: {result.files_read} of {result.cut_total} named; {result.not_read} not named."
        )
        if result.unexplained:
            lines.append(
                f"{len(result.unexplained)} of those sit in a folder the list otherwise "
                "reaches, so a part-run does not explain them:"
            )
            lines += _named(result.unexplained)
        if result.cited_not_read:
            lines.append(f"Cited but not in the files-read list: {len(result.cited_not_read)}")
            lines += _named(result.cited_not_read)
        if result.ignored:
            lines.append(
                f"Files-read entries outside the room's section folders, ignored: {len(result.ignored)}"
            )
            lines += _named(result.ignored)
    if result.dropped:
        lines.append(f"Dropped under --drop-unknown, not in {result.cut}/: {len(result.dropped)}")
        lines += _named(result.dropped)
    lines.append(f"Provenance: {result.provenance}")
    lines.append(f"Tool: {result.output['tool']}")
    lines += [f"Note: {note}" for note in result.notes]
    lines += [
        f"Written to {out}. Score it with:",
        f"  python3 -m synthvdr score {out} --room {result.room}",
    ]
    return "\n".join(lines)
