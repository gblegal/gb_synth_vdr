"""Gate 21: a long document must not repeat itself.

Padding is the failure mode long documents invite — an author short of a floor
pastes a clause again rather than writing the next one — and gate 10's floor
cannot see it (see depth.py's own docstring: it "does NOT catch deliberate
padding").

THE MEASURE is the duplicate-paragraph share: the fraction of a document's
word tokens that sit in a paragraph repeating an EARLIER paragraph of the same
document exactly. Paragraphs are blank-line-separated blocks; tokens are
lower-cased alphanumeric runs; leading numbering tokens are stripped, so
"14.2 X" and "15.3 X" match; paragraphs under MIN_PARAGRAPH_TOKENS and table
blocks are ignored, because signature lines and registers repeat by nature.

WHY NOT SHINGLES. The spec first named the share of repeated 8-word shingles.
Measured on 29 September 2026 it could not tell padding from drafting: real
merger agreements (MAUD) scored a median 0.17 and up to 0.42, ll_vdr_09
already held documents at 0.34-0.42, and a paragraph pasted five times moved
an SPA by 0.037. The duplicate-paragraph share, on the same material: MAUD max
0.020, the Model Commercial Lease and NVCA forms max 0.004, ll_vdr_08 max
0.000 (n=127) and ll_vdr_09 max 0.068 (n=512) — the rooms' populations being
documents of 2,000+ words by wordcount — against 0.331 for an SPA with its
second half repeated and 0.167 for one paragraph pasted five times. THRESHOLD
sits between. The floor is 30 tokens, not 15: at 15 a compilation of six
conformed J30 stock transfer forms in ll_vdr_09 (1.4.7, 2,343 words) scored
0.350 on its 19-26-token boilerplate — legitimate form repetition — while
clause-length padding is unaffected. Record of the measurement:
TECHNICAL-NOTES.md, "Long-form documents".

It measures repetition WITHIN a document only. Similarity ACROSS documents is
expected — house forms produce it, and real rooms contain it. A paraphrased
restatement is not an exact repeat and passes; no cheap measure catches it.
"""

from __future__ import annotations

import re
from typing import List

from .depth import strip_annotation, wordcount
from .runner import fail, ok, skip, truncated

MIN_WORDS = 2000
MIN_PARAGRAPH_TOKENS = 30
THRESHOLD = 0.10

_TOKEN = re.compile(r"[a-z0-9]+")
_PARAGRAPH_BREAK = re.compile(r"\n[ \t\r]*\n")


def paragraphs(text: str) -> List[List[str]]:
    """The document's non-table paragraphs as normalised token lists."""
    out: List[List[str]] = []
    for block in _PARAGRAPH_BREAK.split(text):
        if block.lstrip().startswith("|"):
            continue
        tokens = _TOKEN.findall(block.lower())
        while tokens and tokens[0].isdigit():
            tokens = tokens[1:]
        if tokens:
            out.append(tokens)
    return out


def duplicate_paragraph_share(text: str) -> float:
    blocks = paragraphs(text)
    total = sum(len(tokens) for tokens in blocks)
    if not total:
        return 0.0
    seen = set()
    duplicated = 0
    for tokens in blocks:
        if len(tokens) < MIN_PARAGRAPH_TOKENS:
            continue
        key = " ".join(tokens)
        if key in seen:
            duplicated += len(tokens)
        seen.add(key)
    return duplicated / total


def gate_21_repetition(ctx):
    files = [p for p in ctx.blind_files() if p.suffix == ".md"]
    if not files:
        return skip("21", "repetition", f"{ctx.blind_root} absent or empty")
    flag = ctx.conf.get("FLAG_STRING_1")
    checked = 0
    problems = []
    for path in files:
        text = strip_annotation(path.read_text(encoding="utf-8"), flag)
        if wordcount(text) < MIN_WORDS:
            continue
        checked += 1
        share = duplicate_paragraph_share(text)
        if share > THRESHOLD:
            rel = path.relative_to(ctx.blind_root).as_posix()
            problems.append(f"{rel}: {share:.0%} of its words repeat an earlier paragraph")
    if problems:
        return fail("21", "repetition", truncated(problems) + f" (limit {THRESHOLD:.0%})")
    if not checked:
        return ok("21", "repetition", f"no document reaches {MIN_WORDS:,} words; nothing to measure")
    return ok(
        "21",
        "repetition",
        f"{checked} document(s) of {MIN_WORDS:,}+ words, none above {THRESHOLD:.0%} repeated paragraphs",
    )
