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

import re
from pathlib import Path
from typing import List, Optional, Sequence, Set


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
