"""Gate 20: evidence tells — form, rather than words, that says which documents
carry the answer.

Every other leakage gate checks that the answer key never reaches the blind tree
as WORDS. None checks whether it reaches it as FORM. On 2026-09-29 a blind judge
ranked Project Frithcombe's section-05 documents last on 11 of 16 slots, and all
nineteen gates passed: nothing had leaked, and the room still pointed at its own
findings. The fixes (ll_vdr_08, branch fix/section-05-tells) named three tells,
and this gate looks for each.

1. Selective emphasis. Bold landing on the planted sentence and on nothing
   comparable: 7 of 62 Q&A responses bold, and exactly the false ones; a W&I
   draft bolding exclusions 3, 7, 9 and 12 and no others. Bold as such is not a
   tell — headings, run-in labels, defined terms, ALL-CAPS party and signature
   labels and totals rows are structure, and a document that bolds every item
   of a kind is showing house style. The tell is bold on SOME items of a kind,
   on the words the finding turns on.
2. Pointer notes. An italic note citing an index number that walks the reader
   from one document of a finding or distractor to another. A FORWARD pointer —
   to the later correspondence, claim or resolution — is the tell. A BACKWARD
   pointer, from correspondence to the contract it arises under, is what a real
   data room does; it is reported and not counted. So is a pointer to a document
   most of the section's notes cite anyway, like the IP register: that is the
   section's habit, and says nothing about the document it sits on.
3. Note-presence asymmetry. Where a section's evidence documents carry an
   index-citing note and their neighbours do not, or the reverse, the note's
   presence alone sorts the section. The reverse is not hypothetical: removing
   five pointer notes outright made "no note" a tell of its own, 9 of 15
   evidence documents against 61 of 72 others.

The gate WARNs, never FAILs, while THRESHOLDS rest on one room's calibration: a
room already frozen must not start failing --strict over a check it was never
built against. REPORT is the one line to change when the thresholds have held
on enough rooms to promote it.

What it cannot see is in TECHNICAL-NOTES.md §6. The short version: it reads
form, never meaning, so a planted sentence written in plain roman text with no
pointer to it passes, as it should, and a counterparty's own demand bolded in
its own letter is reported alongside the real tells for a human to judge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Set, Tuple

from ..names import ENTITY_SUFFIXES
from .runner import ok, skip, truncated, warn
from .structural import SLOT_REF

NAME = "evidence tells"


@dataclass(frozen=True)
class Thresholds:
    """What counts as a tell. One place to tune, and the only one.

    `bold_overlap` is the number of distinct content words a selective bold span
    must share with its finding's title and substance (a distractor's title).
    Overlap alone separates nothing — of Frithcombe's 4,786 bold spans in
    evidence documents, the 145 the fix removed and the 4,641 it kept both run
    from zero to twenty shared words — so the structural rules do the work and
    overlap only says the bold landed on the finding. Scored against the 111
    lines the fix took bold off (5.2.5 set aside, as rewritten outright):

        overlap >= 2   262 flagged   35% precise   82% of removals caught
        overlap >= 3   123 flagged   60% precise   67% of removals caught
        overlap >= 4    94 flagged   71% precise   60% of removals caught

    Three, because a warning a human reads should err towards the tell; four if
    the residue gets in the way. Precision is understated: the fix was not
    exhaustive — it left the general counsel's memorandum on the joint venture
    bolding "No waiver has been sought", for one — so some of the "false"
    flags are tells nobody took out.

    `note_gap` is the gap, in share of documents, between a section's evidence
    and other documents carrying an index-citing note. Frithcombe's three
    lopsided sections sit at 69, 82 and 100 points; the next widest, 06, at 39.
    `note_min_docs` is the fewest documents on EACH side for a section to be
    judged at all: one evidence document with a note against none without is
    100% against 0% and says nothing. It is also the fewest documents that must
    cite a target before citing it can be a section's habit.

    `hub_share` is the share of a section's documents whose notes must cite a
    document before a forward pointer to it counts as the section's habit, not
    a signpost (see `section_habits`). On Frithcombe as frozen at v2.0.0 it
    discounts 4 of 33 forward pointers — the IP register and the draft SPA,
    cited by 78% of section 06 and 60% of section 18 — and 40% or 60% would
    discount the same four within one. What it costs: on the pre-fix room it
    also discounts the register half of the trade mark notice's note, which
    the fix removed. The note is still flagged, by its other half, a pointer to
    the Irish licence.
    """

    bold_overlap: int = 3
    note_gap: float = 0.5
    note_min_docs: int = 3
    hub_share: float = 0.5


THRESHOLDS = Thresholds()

# How a room with tells is reported. `warn` until THRESHOLDS have held on more
# than the one room they were calibrated on; then `runner.fail`.
REPORT = warn


# --- The answer key, as the gate needs it -------------------------------------


class Role(NamedTuple):
    """One document's part in one finding's or distractor's evidence."""

    owner: str  # the finding or distractor ID
    root: bool  # the finding's source, or the distractor's location
    words: FrozenSet[str]  # the owner's content words, for bold overlap
    distractor: bool = False


def evidence_roles(findings, distractors) -> Dict[str, List[Role]]:
    """Every evidence path — finding source and corroboration, distractor location
    and resolution — mapped to the part it plays in each owner's chain.

    The ROOT of a chain is the finding's `source` or the distractor's
    `location`. For a distractor that fixes a pointer's direction outright: the
    resolution comes after the alarm by construction. For a finding it is only
    the fallback for undated documents (see `_direction`), because a finding's
    source is where the issue is planted, and that is as often the later letter
    as the contract it arises under.
    """
    roles: Dict[str, List[Role]] = {}
    for finding in findings.findings:
        words = content_words(f"{finding.title} {finding.substance}")
        for rel in finding.evidence_paths():
            roles.setdefault(rel, []).append(Role(finding.id, rel == finding.source, words))
    for distractor in distractors:
        words = content_words(distractor.title)
        roles.setdefault(distractor.location, []).append(Role(distractor.id, True, words, True))
        roles.setdefault(distractor.resolution, []).append(Role(distractor.id, False, words, True))
    return roles


# --- Content words ------------------------------------------------------------

_WORD = re.compile(r"[a-z]+|\d+(?:[.,]\d+)*")

# Function words only. Legal vocabulary ("clause", "agreement", "supplier") is
# kept deliberately: it is exactly what a planted sentence and its finding's
# substance share, and stoplisting it would lower overlap on real tells as much
# as on noise.
_STOPWORDS = frozenset(
    """
    and any are but can did does for from had has have her him his its may not now
    our out per she than that the their them then there these they this those was
    were what when where which who whom whose why will with would should could shall
    all each such other into onto upon under over also been being only very more
    most some same own nor too yet your you
    """.split()
)


def content_words(text: str) -> FrozenSet[str]:
    """Lower-cased words of three or more characters, less function words, with a
    plain plural folded ("exclusions" and "exclusion" are one word). Numbers are
    kept with their commas dropped, so £1,200,000 and 1200000 agree.
    """
    words = set()
    for token in _WORD.findall(text.lower()):
        token = token.replace(",", "")
        if len(token) < 3 or token in _STOPWORDS:
            continue
        if token.isalpha() and len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        words.add(token)
    return frozenset(words)


# --- Markdown shapes ----------------------------------------------------------

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_THEMATIC_BREAK = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?(\s*:?-{3,}:?\s*\|)+\s*(:?-{3,}:?\s*)?$")

# A clause number ("2.2", "3."), a bracketed item ("(a)", "(iv)"), a bullet, or
# "12)". A bare number with no dot is not a marker: "31 March 2027 ..." is prose.
_MARKER = re.compile(
    r"^\s*(?:(?P<clause>\d+(?:\.\d+)+\.?|\d+\.)|\((?P<paren>[a-zA-Z]{1,4}|\d{1,3})\)|(?P<bullet>[-*+])|(?P<closer>\d+)\))\s+"
)

# `**`-delimited only. `__` is legal markdown for bold, but no author in this
# pipeline writes it, and a signature line of underscores would otherwise read
# as a run of empty bold spans.
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")

# The most words a label or a heading has. A span no longer than this, at the
# start of a line, ending in "." or ":", with more text after it, is a run-in
# label ("**Response.**", "**3. Disclosed matters.**"); a line that is nothing
# but a bold span this short, with no full stop, is a heading. Longer, and
# punctuated like a clause, is a bolded sentence: the removed tell "2.2 **The
# aggregate liability of the Sellers under this paragraph 2 is limited to
# £1,200,000.**" starts a line and ends in a full stop, and "(d) **Financial
# Indebtedness under finance leases and hire purchase arrangements ...;**" is a
# whole line that stops on a semicolon. Neither is a label or a heading.
_LABEL_MAX_WORDS = 12

_QUOTES = "\"'“”‘’"
# A defined term without quotes: "**Debt Service Cover Ratio** means ...", or
# "serve written notice (a **Compulsory Transfer Notice**) on ...".
_DEFINES = re.compile(r"^\s*(means|shall mean|has the meaning|have the meaning|includes)\b", re.I)
_OPENS_DEFINITION = re.compile(r"\((?:(?:a|an|the|each|together|each a|together the)\s+)?$", re.I)
_SIGNATURE = re.compile(r"^(signed|executed|accepted|agreed|witness|for and on behalf)\b", re.I)
_ENTITY_SUFFIXES = frozenset(s.lower() for s in ENTITY_SUFFIXES)
_TOTAL_LABEL = re.compile(r"\b(sub-?)?totals?\b", re.I)
_FIGURE = re.compile(r"^(?:[\s£$€%()+\-–—.,\dm]|nil|none|n/a)*$", re.I)

# A whole-line bold span in a document's title block is part of its title —
# "**Farrowmere Ingredients Limited — technical department file extract,
# prepared for the data room, August 2026.**" under the document's "#" heading —
# however it is punctuated.
_TITLE_BLOCK_LINES = 5


def _normalise(raw: str) -> str:
    """A source line with blockquote markers and leading indentation gone, and
    `&nbsp;` read as the space it renders as — authors indent sub-clauses with it
    ("&nbsp;&nbsp;(a) **Debt Service Cover Ratio.** ..."), which would otherwise
    hide the item marker behind it."""
    return raw.replace("&nbsp;", " ").lstrip(" >")


def _marker_shape(line: str) -> Tuple[str, int]:
    """(shape, offset) — the item marker's shape, and where the text after it starts."""
    match = _MARKER.match(line)
    if not match:
        return "", len(line) - len(line.lstrip())
    if match.group("clause"):
        depth = len([part for part in match.group("clause").split(".") if part])
        shape = ".".join(["n"] * depth)
    elif match.group("paren"):
        shape = "(x)"
    elif match.group("bullet"):
        shape = "-"
    else:
        shape = "n)"
    return shape, match.end()


def _is_all_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 2 and sum(c.isupper() for c in letters) >= 0.75 * len(letters)


def _is_structure(match: re.Match, line: str) -> bool:
    """A bold span that is structure rather than emphasis, wherever it sits: a
    defined term (in quotes, in brackets, or followed by "means"), a party or
    signature label (ALL-CAPS, or a company name alone), or a short label
    closing on a colon ("**Raised:**").
    """
    text = match.group(1)
    before, after = line[:match.start()], line[match.end():]
    if (before and before[-1] in _QUOTES) or text[0] in _QUOTES:
        return True
    if _DEFINES.match(after) or (_OPENS_DEFINITION.search(before) and after.startswith(")")):
        return True
    if _is_all_caps(text) or _SIGNATURE.match(text):
        return True
    words = text.split()
    if len(words) <= 6 and words[-1].strip(",;:()").replace(".", "").lower() in _ENTITY_SUFFIXES:
        return True
    return len(words) <= _LABEL_MAX_WORDS and (text.endswith(":") or after.startswith(":"))


def _cells(row: str) -> List[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return re.split(r"(?<!\\)\|", row)


def _is_totals_row(cells: List[str]) -> bool:
    """A totals row: its label names a total, or it is bold throughout with a
    figure in every cell after the label — the subtotal lines of a financial
    table ("Net current liabilities", "EBITDA for the Relevant Period").

    A row bold throughout with PROSE in it is not a totals row: that was the
    Frithcombe NCR log's one bold row, a near miss, in a table where nothing
    else was bold. What the figures rule gives up is a bold line of figures
    that is a spotlight rather than a subtotal — the same room's trading update
    bolded "Other income — insurance recovery on the 2025 recall" and its 1.6,
    and this rule reads that as a subtotal. Twelve subtotal rows the fix kept
    were being flagged without it.
    """
    filled = [c.strip() for c in cells if c.strip()]
    if not filled:
        return False
    if _TOTAL_LABEL.search(filled[0].replace("*", "")):
        return True
    bold_throughout = all(_BOLD.fullmatch(c) for c in filled)
    figures = all(_FIGURE.match(c.replace("*", "")) for c in filled[1:])
    return bold_throughout and len(filled) > 1 and figures


def _cell_value(cell: str) -> str:
    return " ".join(cell.replace("*", "").lower().split())


# --- Selective bold -----------------------------------------------------------


class _Item(NamedTuple):
    """One comparable item — a table cell, a labelled line, a list item or a
    paragraph — and the bold spans in it that are emphasis, not structure.
    A table cell also carries its whole row's values, for the row-of-a-kind
    rule in `_marks_a_kind`."""

    line: int
    group: tuple
    spans: List[str]
    row: Tuple[str, ...] = ()


def _table_items(block: List[Tuple[int, str]], table_id: int) -> List[_Item]:
    """Body cells of one table, grouped by column: the comparable items of a
    table cell are the same column's other rows. Header rows and totals rows
    are not items.
    """
    separator = next((i for i, (_, row) in enumerate(block) if _TABLE_SEPARATOR.match(row)), None)
    body = block[separator + 1:] if separator is not None else block
    items = []
    for number, row in body:
        cells = _cells(row)
        if _is_totals_row(cells):
            continue
        values = tuple(_cell_value(cell) for cell in cells)
        for column, cell in enumerate(cells):
            spans = [m.group(1) for m in _BOLD.finditer(cell) if not _is_structure(m, cell)]
            items.append(_Item(number, ("table", table_id, column), spans, values))
    return items


def _line_item(number: int, line: str, title_block: bool = False) -> Optional[_Item]:
    """A text line as a comparable item, or None if it is a heading-like line.

    The group is what the line is comparable WITH: lines opening on the same
    run-in label (every "**Response.**", or every "**N. Title.**"), then list
    items of the same marker shape, then plain paragraphs.
    """
    shape, offset = _marker_shape(line)
    matches = list(_BOLD.finditer(line))
    if not matches:
        return _Item(number, ("item", shape) if shape else ("para",), [])

    first = matches[0]
    if first.start() == offset and not line[first.end():].strip():
        # A line that is one bold span and nothing else is a heading by another
        # name — a title, a subject line, a caption — unless it is punctuated
        # like a clause. Any length with no closing punctuation at all; up to
        # _LABEL_MAX_WORDS closing on anything but a full stop. A list item's
        # trailing "; and" is a clause continuing, not a title.
        text = first.group(1)
        unpunctuated = not text.endswith((".", ";", ":", ",", "!", "?", " and", " or"))
        short = len(text.split()) <= _LABEL_MAX_WORDS and not text.endswith((".", "!", "?"))
        if title_block or unpunctuated or short:
            return None

    group: tuple = ("item", shape) if shape else ("para",)
    label_text = first.group(1)
    closes = label_text.endswith((".", ":")) or line[first.end():first.end() + 1] in (".", ":")
    if (
        first.start() == offset
        and closes
        and len(label_text.split()) <= _LABEL_MAX_WORDS
        and line[first.end():].strip(" .:")
    ):
        key = re.sub(r"\d+", "#", label_text.lower())
        if re.match(r"^#(\.#)*\.?\s", key):
            key = "#" + key[-1]
        group = ("label", shape, key)
        matches = matches[1:]
    spans = [m.group(1) for m in matches if not _is_structure(m, line)]
    return _Item(number, group, spans)


def comparable_items(text: str) -> List[_Item]:
    """Every comparable item in a markdown document, in document order."""
    items: List[_Item] = []
    table: List[Tuple[int, str]] = []
    table_id = 0
    in_fence = False
    # The title block: after a document's opening "#" title, up to the first
    # break or heading, and never more than _TITLE_BLOCK_LINES lines. A document
    # that does not open on a title has none.
    seen, title_block = 0, False
    for number, raw in enumerate(text.splitlines(), 1):
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        line = _normalise(raw)
        if line.strip():
            seen += 1
            opens = seen == 1 and bool(_HEADING.match(line))
            closes = _HEADING.match(line) or _THEMATIC_BREAK.match(line) or seen > _TITLE_BLOCK_LINES
            title_block = opens or (title_block and not closes)
        if not in_fence and line.startswith("|"):
            table.append((number, line))
            continue
        if table:
            items.extend(_table_items(table, table_id))
            table, table_id = [], table_id + 1
        if in_fence or not line.strip() or _HEADING.match(line) or _THEMATIC_BREAK.match(line):
            continue
        item = _line_item(number, line, title_block)
        if item is not None:
            items.append(item)
    if table:
        items.extend(_table_items(table, table_id))
    return items


class BoldTell(NamedTuple):
    rel: str
    line: int
    owner: str
    shared: int
    text: str

    def describe(self) -> str:
        return f"{_slot(self.rel)}:{self.line} {self.owner} ({self.shared} words) '{_clip(self.text)}'"


def _marks_a_kind(members: List[_Item]) -> bool:
    """Whether a table column's bold rows are exactly the rows sharing one
    value in some column — bold applied to every row of a kind.

    Frithcombe's employers' liability schedule bolds the six policy years whose
    "Traced" cell reads "No" and none of the others. That is the reader being
    shown a category, which a real schedule does, not a sentence being singled
    out; the fix kept it. Two rows at least: one bold row trivially shares its
    own values with itself.
    """
    bold = {m.line for m in members if m.spans}
    if len(bold) < 2:
        return False
    rows = {m.line: m.row for m in members}
    width = min(len(row) for row in rows.values())
    for column in range(width):
        values = {rows[line][column] for line in bold}
        if len(values) == 1:
            value = values.pop()
            if value and {line for line, row in rows.items() if row[column] == value} == bold:
                return True
    return False


def _is_uniform(members: List[_Item]) -> bool:
    """Bold on every item of a group — house style, not a spotlight — or, in a
    table, on every row of a kind.

    "Every", not "most": measured on Frithcombe, relaxing it to half the items
    drops five of 123 flags, too few to be worth a second threshold. A lone
    item is never uniform: with nothing
    comparable to it, bold on it is as selective as bold can be. That is the
    room's "**Plan.**" in the commercial summary, the only line so labelled.
    """
    if len(members) < 2:
        return False
    if all(m.spans for m in members):
        return True
    return members[0].group[0] == "table" and _marks_a_kind(members)


def selective_bold(rel: str, text: str, roles: List[Role], thresholds: Thresholds) -> List[BoldTell]:
    """Bold that falls on some of a document's comparable items and not all, on
    words its finding or distractor turns on. At most one tell per line — the
    span sharing the most words — so a wholly bold table row counts once.
    """
    groups: Dict[tuple, List[_Item]] = {}
    for item in comparable_items(text):
        groups.setdefault(item.group, []).append(item)

    min_overlap = thresholds.bold_overlap
    tells: Dict[int, BoldTell] = {}
    for members in groups.values():
        if _is_uniform(members):
            continue
        for member in members:
            for span in member.spans:
                words = content_words(span)
                best = max(((len(words & role.words), role.owner) for role in roles), default=(0, ""))
                if best[0] < min_overlap:
                    continue
                current = tells.get(member.line)
                if current is None or best[0] > current.shared:
                    tells[member.line] = BoldTell(rel, member.line, best[1], best[0], span)
    return [tells[line] for line in sorted(tells)]


# --- Italic notes and pointers ------------------------------------------------

_ITALIC_NOTE = re.compile(r"^([*_])(?![*_\s])(?P<inner>.+?)(?<![*_\s])\1$", re.S)


class Note(NamedTuple):
    line: int  # the note's first line
    cites: Tuple[str, ...]  # slot IDs it cites, other than its own document's


def italic_notes(text: str, own_slot: str, known_slots: Iterable[str]) -> List[Note]:
    """Wholly italic paragraphs that cite an index number of another document.

    A paragraph, not a line: a note wraps ("*Filed on the trade mark papers ...
    The licence ... is at clause 2 of the distribution agreement at 5.3.1.*"
    over three lines was one of the tells). Any label or none — "Data room
    note:", "Related documents in the data room:", "Cross-references:" and an
    unlabelled filing note all count. An italic run-in label with roman text
    after it ("*Reply:* the matter is at 5.1.3") is not a note.
    """
    known = set(known_slots)
    notes: List[Note] = []
    paragraph: List[Tuple[int, str]] = []
    in_fence = False

    def close():
        if paragraph:
            joined = " ".join(part.strip() for _, part in paragraph)
            match = _ITALIC_NOTE.match(joined)
            if match and match.group(1) not in match.group("inner"):
                cites = tuple(
                    dict.fromkeys(
                        ref for ref in SLOT_REF.findall(match.group("inner"))
                        if ref in known and ref != own_slot
                    )
                )
                if cites:
                    notes.append(Note(paragraph[0][0], cites))
            paragraph.clear()

    for number, raw in enumerate(text.splitlines(), 1):
        if _FENCE.match(raw):
            close()
            in_fence = not in_fence
            continue
        line = _normalise(raw)
        if (
            in_fence
            or not line.strip()
            or line.startswith("|")
            or _HEADING.match(line)
            or _THEMATIC_BREAK.match(line)
        ):
            close()
            continue
        paragraph.append((number, line))
    close()
    return notes


_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
_DATE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", re.I)
_DATED_LINE = re.compile(r"^\W*(dated|date)\b", re.I)

# How far into a document its own date is looked for. The header, not the body:
# the body is full of other documents' dates.
_HEADER_LINES = 25

FORWARD, BACKWARD, LATERAL, HABIT = "forward", "backward", "lateral", "habit"


def document_date(text: str) -> Optional[Tuple[int, int, int]]:
    """A document's own date, read from its header: a "Dated"/"Date:" line if it
    has one, otherwise the first full date in its first lines. None if neither.

    A heuristic, and knowingly so. A header's first date is sometimes a period
    start, an expiry it quotes or a filing date — Frithcombe's EUIPO expiry
    notice reads as 2016, its filing date — and a wrong date turns a forward
    pointer backward or the reverse. It is still much the better signal: the
    alternative, direction from the answer key alone, called the ITT's note
    "the framework agreement referred to in the covering letter is at 5.4.1"
    forward, because the ITT is where that finding is planted.
    """
    lines = [line for line in text.splitlines() if line.strip()][:_HEADER_LINES]
    dated = [line for line in lines if _DATED_LINE.match(line)]
    for line in dated + lines:
        match = _DATE.search(line)
        if match:
            day, month, year = match.groups()
            return int(year), _MONTHS.index(month.lower()) + 1, int(day)
    return None


class Pointer(NamedTuple):
    rel: str
    line: int
    target: str
    owner: str
    direction: str

    def describe(self) -> str:
        return f"{_slot(self.rel)}:{self.line} -> {_slot(self.target)} ({self.owner})"


def _direction(
    source: Role,
    target: Role,
    source_date: Optional[Tuple[int, int, int]],
    target_date: Optional[Tuple[int, int, int]],
) -> str:
    """Forward is earlier to later — to the correspondence, claim or resolution a
    document could not have known about. Backward is later to earlier — to the
    contract a letter arises under, which any letter names.

    A distractor's alarm and its resolution are ordered by construction. Other
    documents are ordered by `document_date`; the same date is LATERAL — the
    articles, the resolution adopting them and the board minute of the same
    day are one package, and cross-refer as a package does. Only when a date
    cannot be read does the chain's root decide: towards the root is backward.
    """
    if source.distractor and source.root != target.root:
        return FORWARD if source.root else BACKWARD
    if source_date and target_date:
        if source_date == target_date:
            return LATERAL
        return FORWARD if target_date > source_date else BACKWARD
    return BACKWARD if target.root else FORWARD


def pointers(
    rel: str,
    notes: List[Note],
    roles: Dict[str, List[Role]],
    slot_paths: Dict[str, str],
    dates: Dict[str, Optional[Tuple[int, int, int]]],
) -> List[Pointer]:
    """Notes in `rel` citing another document of the same finding or distractor,
    each with its direction. `dates` maps a path to its `document_date`.
    """
    own = {role.owner: role for role in roles.get(rel, [])}
    found = []
    for note in notes:
        for ref in note.cites:
            target = slot_paths[ref]
            if target == rel:
                continue
            for role in roles.get(target, []):
                if role.owner in own:
                    direction = _direction(own[role.owner], role, dates.get(rel), dates.get(target))
                    found.append(Pointer(rel, note.line, target, role.owner, direction))
    return found


# --- Note-presence asymmetry --------------------------------------------------


@dataclass(frozen=True)
class SectionNotes:
    section: str
    evidence_with: int
    evidence_total: int
    other_with: int
    other_total: int

    @property
    def evidence_share(self) -> float:
        return self.evidence_with / self.evidence_total if self.evidence_total else 0.0

    @property
    def other_share(self) -> float:
        return self.other_with / self.other_total if self.other_total else 0.0

    @property
    def gap(self) -> float:
        return abs(self.evidence_share - self.other_share)

    def describe(self) -> str:
        # Floored, in integers: 7 of 8 is "87%", as the room's own audit put it.
        evidence = self.evidence_with * 100 // self.evidence_total if self.evidence_total else 0
        other = self.other_with * 100 // self.other_total if self.other_total else 0
        return (
            f"{self.section} notes on {evidence}% of {self.evidence_total} evidence "
            f"against {other}% of {self.other_total} other"
        )


def note_asymmetry(has_note: Dict[str, bool], evidence: Iterable[str]) -> List[SectionNotes]:
    """Per top-level section, the share of evidence and of other documents
    carrying an index-citing italic note. `has_note` maps every markdown
    document's path, relative to the blind tree, to whether it carries one.
    """
    evidence = set(evidence)
    counts: Dict[str, List[int]] = {}
    for rel, noted in has_note.items():
        row = counts.setdefault(rel.split("/", 1)[0], [0, 0, 0, 0])
        base = 0 if rel in evidence else 2
        row[base] += int(noted)
        row[base + 1] += 1
    return [SectionNotes(section, *row) for section, row in sorted(counts.items())]


# --- The room, and the gate ---------------------------------------------------


@dataclass
class TellReport:
    bold: List[BoldTell] = field(default_factory=list)
    pointers: List[Pointer] = field(default_factory=list)
    sections: List[SectionNotes] = field(default_factory=list)
    asymmetric: List[SectionNotes] = field(default_factory=list)

    @property
    def forward(self) -> List[Pointer]:
        return [p for p in self.pointers if p.direction == FORWARD]

    @property
    def count(self) -> int:
        """Tells, as the gate counts them. Backward, lateral and habit pointers are not."""
        return len(self.bold) + len(self.forward) + len(self.asymmetric)


def section_habits(
    cites: Dict[str, Iterable[str]], share: float, min_docs: int
) -> Dict[str, Set[str]]:
    """Per top-level section, the documents its notes cite so often that citing
    them is the section's habit rather than a choice: at least `share` of the
    section's documents, and never fewer than `min_docs` — in a two-document
    section one note is half the section, and a habit of one is not a habit.
    `cites` maps every markdown document's path to the slots its notes cite.

    A pointer to such a document says nothing about the document it sits on.
    Frithcombe's section 06 closes 78% of its documents with "Group register of
    intellectual property rights: 6.1.1", and the register is IP-2's
    corroboration, so the trade mark notice's copy of that line read as a forward
    signpost — the one a re-audit had removed, back in the room only because the
    section's notes were later made to follow one convention.
    """
    documents: Dict[str, int] = {}
    counts: Dict[str, Dict[str, int]] = {}
    for rel, slots in cites.items():
        section = rel.split("/", 1)[0]
        documents[section] = documents.get(section, 0) + 1
        per_slot = counts.setdefault(section, {})
        for slot in set(slots):
            per_slot[slot] = per_slot.get(slot, 0) + 1
    return {
        section: {
            slot for slot, n in per_slot.items() if n >= max(min_docs, share * documents[section])
        }
        for section, per_slot in counts.items()
    }


def _slot(rel: str) -> str:
    return Path(rel).stem.split("_", 1)[0]


def _clip(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def scan_room(
    blind_root: Path, roles: Dict[str, List[Role]], thresholds: Thresholds = THRESHOLDS
) -> TellReport:
    """All three checks over every markdown document under `blind_root`."""
    texts = {
        path.relative_to(blind_root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(blind_root.rglob("*.md"))
        if path.is_file()
    }
    slot_paths = {_slot(rel): rel for rel in texts if SLOT_REF.fullmatch(_slot(rel))}
    dates = {rel: document_date(texts[rel]) for rel in roles if rel in texts}

    report = TellReport()
    cites: Dict[str, List[str]] = {}
    for rel, text in texts.items():
        notes = italic_notes(text, _slot(rel), slot_paths)
        cites[rel] = [slot for note in notes for slot in note.cites]
        if rel in roles:
            report.bold.extend(selective_bold(rel, text, roles[rel], thresholds))
            report.pointers.extend(pointers(rel, notes, roles, slot_paths, dates))

    habits = section_habits(cites, thresholds.hub_share, thresholds.note_min_docs)
    report.pointers = [
        p._replace(direction=HABIT)
        if p.direction == FORWARD and _slot(p.target) in habits.get(p.rel.split("/", 1)[0], ())
        else p
        for p in report.pointers
    ]
    report.sections = note_asymmetry({rel: bool(slots) for rel, slots in cites.items()}, roles)
    report.asymmetric = [
        s
        for s in report.sections
        if min(s.evidence_total, s.other_total) >= thresholds.note_min_docs
        and s.gap >= thresholds.note_gap
    ]
    return report


def gate_20_tells(ctx, thresholds: Thresholds = THRESHOLDS):
    roles = evidence_roles(ctx.findings, ctx.distractors)
    if not roles:
        return skip("20", NAME, "no findings or distractors in the answer key — no evidence to tell apart")
    if not any(p.suffix == ".md" for p in ctx.blind_files()):
        return skip("20", NAME, f"{ctx.blind_root} absent or holds no markdown")

    report = scan_room(ctx.blind_root, roles, thresholds)
    backward, lateral, habit = (
        sum(1 for p in report.pointers if p.direction == kind) for kind in (BACKWARD, LATERAL, HABIT)
    )
    kept = (
        f"not counted: {backward} backward, {lateral} same-date and {habit} section-habit "
        "pointer(s)"
    )
    if not report.count:
        return ok("20", NAME, f"no selective bold, forward pointer or note asymmetry; {kept}")

    parts = []
    if report.bold:
        documents = len({t.rel for t in report.bold})
        parts.append(
            f"selective bold on {len(report.bold)} line(s) in {documents} evidence document(s): "
            + truncated([t.describe() for t in report.bold])
        )
    if report.forward:
        parts.append(
            f"{len(report.forward)} forward pointer note(s): "
            + truncated([p.describe() for p in report.forward])
        )
    if report.asymmetric:
        parts.append(
            f"note asymmetry in {len(report.asymmetric)} section(s): "
            + truncated([s.describe() for s in report.asymmetric])
        )
    return REPORT("20", NAME, f"{report.count} tell(s) — " + " | ".join(parts) + f" | {kept}")
