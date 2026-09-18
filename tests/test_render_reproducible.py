"""Both render trees must be byte-reproducible.

`synthvdr.manifest.compute_content_hash` hashes raw file BYTES. So anything a
renderer stamps into its output lands in the room's fingerprint, and a tree
re-rendered from an identical source hashes differently. Both renderers did
exactly that, by opposite mechanisms:

- `pdf.mjs`: Chrome's PDF writer stamps `/CreationDate` and `/ModDate` to the
  second (792 distinct values across the 800 files of one real render),
  `/Producer` (the Skia/Chrome build) and `/Creator` (the user-agent, which
  carries the host OS). Different second, different Chrome, different OS —
  different bytes.
- `docx.py`: the XML inside is already fully deterministic, but `writestr`
  stamped every zip member with the build's wall clock.

Until both were pinned, a render tree could not carry a manifest that meant
anything, so a run over one could not be provenance-verified at all — and
there is no honest way round it, because writing a manifest for a render tree
after the fact hashes the very tree the run just read, which compares the tree
against itself and can never fail.

THE TEST IS REPRODUCIBILITY, SO IT IS PROVED, NOT ASSERTED: every test here
renders the same source tree TWICE, to two different output directories, and
compares. A test that inspected one render for a pinned date would pass just
as happily on a renderer that pinned the date and left something else moving.

SKIP DISCIPLINE, per the rest of this suite: what needs a toolchain skips
loudly when it is absent, and never silently passes. The tiers are deliberate
— `normalisePdfMetadata` is the riskiest code in `pdf.mjs` (it rewrites
cross-reference offsets, and a mistake there produces a PDF no reader will
open) and it needs only `node`, so it is exercised on far more machines than
have a Chrome for puppeteer to drive.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from synthvdr.manifest import compute_content_hash
from synthvdr.render.docx import _FIXED_ZIP_DATE_TIME, render_tree_docx

PDF_MJS = Path(__file__).resolve().parent.parent / "synthvdr" / "render" / "pdf.mjs"

# The four fields Chrome stamps. Matched over raw bytes, with `\\.` ahead of
# the character class so an escaped `\)` inside a value (the user-agent's
# `\(KHTML, like Gecko\)`) does not end the match early.
_STAMPED = re.compile(
    rb"/(CreationDate|ModDate|Producer|Creator)\s*\(((?:\\.|[^\\)])*)\)"
)

PINNED_DATE = b"D:19800101000000+00'00'"


# --- the source tree both renderers are pointed at ------------------------
#
# Small and handwritten rather than the xs-room fixture: PDF rendering is a
# real browser doing real layout, and reproducibility is a property of one
# document as much as of eight hundred. Two documents cover both PDF branches
# — one live-text, one scanned — and the scanned one is long enough to span
# several pages, because the scanned branch screenshots page by page and a
# single-page document would never exercise the loop.


def _source_tree(root: Path) -> Path:
    """A room-shaped source tree at `root`, returning its blind tree.

    `_key/scanned.csv` sits beside the tree rather than inside it, which is
    where `pdf.mjs` looks for it (`<parent of --src>/_key/scanned.csv`).
    """
    src = root / "data-room"
    (src / "01_corporate").mkdir(parents=True)
    (src / "01_corporate" / "1.1.1_articles.md").write_text(
        "# Articles of association\n\nThe company was incorporated in 2011.\n",
        encoding="utf-8",
    )
    long_body = "\n\n".join(
        f"Clause {n}. " + ("The parties agree as set out in this deed. " * 40)
        for n in range(1, 21)
    )
    (src / "01_corporate" / "1.1.2_deed.md").write_text(
        f"# Deed of adherence\n\n{long_body}\n", encoding="utf-8"
    )

    key = root / "_key"
    key.mkdir(parents=True)
    (key / "scanned.csv").write_text(
        "slot\n01_corporate/1.1.2_deed\n", encoding="utf-8"
    )
    return src


# --- tier 1: no toolchain at all ------------------------------------------


def test_both_renderers_pin_the_same_instant():
    """The DOCX and PDF trees must agree about what "no meaningful date"
    looks like. Zip cannot represent anything before 1980-01-01, so that side
    has no choice; this pins the PDF side to the same instant rather than
    leaving the two to drift into separate arbitrary epochs.

    Needs nothing installed — it reads the shipped `pdf.mjs` as text — so it
    is one of the few checks here that runs everywhere, unconditionally.
    """
    assert _FIXED_ZIP_DATE_TIME == (1980, 1, 1, 0, 0, 0)

    mjs = PDF_MJS.read_text(encoding="utf-8")
    match = re.search(
        r'const PINNED_PDF_DATE = "D:(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)',
        mjs,
    )
    assert match, (
        "could not find `const PINNED_PDF_DATE = \"D:YYYYMMDDHHmmSS...\"` in "
        "synthvdr/render/pdf.mjs — has it been renamed or reformatted? "
        "update this test"
    )
    assert tuple(int(part) for part in match.groups()) == _FIXED_ZIP_DATE_TIME


# --- tier 2: node, but no browser -----------------------------------------
#
# `normalisePdfMetadata` shortens the Info dictionary, which is object 1 and
# sits at the head of a Skia-written file — so every later object moves, and
# every offset in the cross-reference table plus the `startxref` pointer have
# to move with it or the file is corrupt. That arithmetic is worth a test that
# does not need a browser, and the guarded entry point in `pdf.mjs` is what
# makes importing it possible without launching one.


def _synthetic_pdf(info_dict: str) -> bytes:
    """A minimal but structurally correct PDF whose Info dictionary is
    `info_dict`, with a classic 20-byte xref table and accurate offsets —
    i.e. exactly the shape Skia emits, small enough to reason about.
    """
    objects = [
        info_dict,
        "<</Type /Catalog\n/Pages 3 0 R>>",
        "<</Type /Pages\n/Kids [4 0 R]\n/Count 1>>",
        "<</Type /Page\n/Parent 3 0 R\n/MediaBox [0 0 595 842]>>",
    ]
    body = b"%PDF-1.4\n%\xd3\xeb\xe9\xe1\n"
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n{obj}\nendobj\n".encode("latin-1")

    table_at = len(body)
    table = f"xref\n0 {len(objects) + 1}\n".encode("latin-1") + b"0000000000 65535 f \n"
    for offset in offsets:
        table += f"{offset:010d} 00000 n \n".encode("latin-1")
    trailer = (
        f"trailer\n<</Size {len(objects) + 1}\n/Root 2 0 R\n/Info 1 0 R>>\n"
        f"startxref\n{table_at}\n%%EOF\n"
    ).encode("latin-1")
    return body + table + trailer


def _xref_offsets(pdf: bytes) -> list:
    """Every in-use xref offset of `pdf`, after checking that `startxref`
    points at a real table. Returns (object number, offset) pairs."""
    startxref_at = pdf.rfind(b"startxref")
    assert startxref_at >= 0, "no startxref"
    table_at = int(pdf[startxref_at + len("startxref") :].split()[0])
    assert pdf[table_at : table_at + 4] == b"xref", (
        f"startxref points at {pdf[table_at:table_at + 20]!r}, not an xref table"
    )

    cursor = table_at + 4
    header = re.match(rb"\s*(\d+)\s+(\d+)\s*\r?\n", pdf[cursor : cursor + 64])
    assert header, "unreadable xref subsection header"
    cursor += len(header.group(0))
    first, count = int(header.group(1)), int(header.group(2))

    found = []
    for number in range(first, first + count):
        record = re.match(rb"(\d{10}) (\d{5}) ([nf])", pdf[cursor : cursor + 20])
        assert record, f"malformed xref entry for object {number}"
        cursor += 20
        if record.group(3) == b"n":
            found.append((number, int(record.group(1))))
    return found


def _assert_xref_is_consistent(pdf: bytes) -> None:
    """Every in-use xref offset must land on its own object header. This is
    the check that would have caught an off-by-delta in the rewrite: the
    bytes could be perfectly reproducible and the file still unopenable."""
    for number, offset in _xref_offsets(pdf):
        head = pdf[offset : offset + 24]
        assert re.match(rb"%d\s+\d+\s+obj" % number, head), (
            f"xref says object {number} is at byte {offset}, which holds {head!r}"
        )


def _node_normalise(node: str, tmp_path: Path, raw: bytes) -> bytes:
    """Run the SHIPPED `normalisePdfMetadata` over `raw`.

    Imports the real `pdf.mjs` rather than re-stating its logic, the same
    discipline as `test_render_docx.py`'s cross-language checks: a test
    carrying its own copy of the algorithm proves the two authors agree, not
    that the shipped file is right.
    """
    source = tmp_path / "in.pdf"
    target = tmp_path / "out.pdf"
    source.write_bytes(raw)
    # json.dumps, not repr: repr produces single quotes (invalid as a JS
    # module specifier the moment a path contains an apostrophe) and does not
    # escape for JS.
    script = (
        f"import {{ normalisePdfMetadata }} from {json.dumps(PDF_MJS.as_uri())};\n"
        'import { readFileSync, writeFileSync } from "node:fs";\n'
        f"writeFileSync({json.dumps(str(target))}, "
        f"normalisePdfMetadata(readFileSync({json.dumps(str(source))})));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run normalisePdfMetadata: {proc.stderr}")
    return target.read_bytes()


def test_normalise_pdf_metadata_pins_the_info_dict_and_fixes_the_xref(tmp_path):
    """The whole rewrite, over a PDF this test builds itself.

    The Info dictionary here is deliberately awkward in the two ways Chrome's
    real one is: a user-agent string containing ESCAPED PARENTHESES, and — the
    boundary a plain `indexOf(">>")` gets wrong — a `>` inside a string value.
    A scanner that did not skip literal strings would cut the dictionary short
    there and corrupt everything after it.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — the Info-dict rewrite is unverified here")

    raw = _synthetic_pdf(
        "<</Title (about:blank)\n"
        "/Creator (Mozilla/5.0 \\(Macintosh; Intel Mac OS X 10_15_7\\) "
        "AppleWebKit/537.36 \\(KHTML, like Gecko\\) HeadlessChrome/153.0.0.0 "
        "Safari/537.36 >>not-the-end)\n"
        "/Producer (Skia/PDF m153)\n"
        "/CreationDate (D:20260918120034+00'00')\n"
        "/ModDate (D:20260918120034+00'00')>>"
    )
    _assert_xref_is_consistent(raw)  # the fixture itself must be sound
    assert b"Skia/PDF" in raw

    out = _node_normalise(node, tmp_path, raw)

    # The rewrite shortened object 1, so every later offset had to move.
    assert len(out) < len(raw)
    assert _xref_offsets(out) != _xref_offsets(raw), (
        "nothing moved — the fixture is not exercising the offset fix-up"
    )
    _assert_xref_is_consistent(out)

    stamped = dict(_STAMPED.findall(out))
    assert stamped == {b"CreationDate": PINNED_DATE, b"ModDate": PINNED_DATE}
    assert b"Skia/PDF" not in out, "/Producer must be gone, not merely rewritten"
    assert b"HeadlessChrome" not in out, "/Creator must be gone, not merely rewritten"

    # Idempotent: normalising an already-normalised file changes nothing.
    assert _node_normalise(node, tmp_path, out) == out


# --- tier 3: DOCX ---------------------------------------------------------
#
# `importorskip` per test rather than at module scope: the node-only check
# above needs no python-docx, and a module-level skip would take it down too.


def _require_docx():
    return pytest.importorskip("docx", reason="python-docx not installed")


def test_docx_render_is_byte_reproducible(tmp_path, monkeypatch):
    """Two renders of one source tree, two output directories, one hash.

    THE CLOCK IS FORCED TO DIFFER BETWEEN THE TWO RENDERS, and without that
    this test is a coin flip rather than a test. The stamp being removed is
    the zip member mtime, which `ZipFile.writestr` takes from
    `time.localtime()` — so two renders milliseconds apart usually land in the
    same second and agree by luck, on the fixed code AND on the broken code.
    Verified: against a `render_tree_docx` with the normalisation removed,
    this test passed until the clock was pinned and fails reliably now.

    Eleven years apart rather than a second, so that a partial fix — one that
    pinned, say, the date but not the time — cannot slip through either.
    """
    _require_docx()
    src = _source_tree(tmp_path / "room")
    first, second = tmp_path / "docx-a", tmp_path / "docx-b"

    def freeze(*fields):
        # struct_time, not a bare tuple: zipfile slices [:6] off whatever
        # time.localtime returns, but anything else reading the clock during
        # the render has a right to the real type.
        monkeypatch.setattr(
            time, "localtime", lambda *_: time.struct_time(fields + (0, 1, 0))
        )

    freeze(2020, 1, 1, 12, 0, 0)
    assert render_tree_docx(src, first) == 2
    freeze(2031, 6, 7, 3, 45, 30)
    assert render_tree_docx(src, second) == 2

    assert compute_content_hash(first) == compute_content_hash(second)
    assert compute_content_hash(first)[1] == 2, "both files must be in the hash"


def test_docx_zip_member_mtimes_are_pinned(tmp_path):
    """Every member of every archive, pinned — the direct check that does not
    depend on two renders happening to straddle a second boundary."""
    import zipfile

    _require_docx()
    src = _source_tree(tmp_path / "room")
    out = tmp_path / "docx"
    render_tree_docx(src, out)

    rendered = sorted(out.rglob("*.docx"))
    assert rendered, "nothing was rendered"
    for path in rendered:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            assert members, f"{path.name} has no members"
            for info in members:
                assert info.date_time == _FIXED_ZIP_DATE_TIME, (
                    f"{path.name}:{info.filename} carries {info.date_time}"
                )


def test_docx_render_still_reads_back_correctly(tmp_path):
    """Metadata-only: the normalisation must not disturb the document.

    Rewriting a zip is the sort of change that can produce a file that hashes
    beautifully and no longer opens, so the structure python-docx put in is
    read back out of the normalised archive here.
    """
    Document = _require_docx().Document

    src = _source_tree(tmp_path / "room")
    out = tmp_path / "docx"
    render_tree_docx(src, out)

    document = Document(str(out / "01_corporate" / "1.1.1_articles.docx"))
    paragraphs = [(p.text, p.style.name) for p in document.paragraphs]
    assert ("Articles of association", "Heading 1") in paragraphs
    assert ("The company was incorporated in 2011.", "Normal") in paragraphs


# --- tier 4: the full PDF render, which needs puppeteer AND a browser -----


def _chrome() -> str:
    """The Chrome puppeteer should drive, or "" if there is none to find.

    An explicit PUPPETEER_EXECUTABLE_PATH wins: without one puppeteer tries to
    download a ~200MB Chrome for Testing, which is not a thing a test suite
    may do. The fallbacks are the ordinary install locations, so the test runs
    on a developer machine without ceremony.
    """
    explicit = os.environ.get("PUPPETEER_EXECUTABLE_PATH", "")
    if explicit and Path(explicit).exists():
        return explicit
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if Path(mac).exists():
        return mac
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _render_pdf(src: Path, out: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — PDF render reproducibility unverified")
    chrome = _chrome()
    if not chrome:
        pytest.skip("no local Chrome/Chromium — PDF render reproducibility unverified")

    proc = subprocess.run(
        [node, str(PDF_MJS), "--src", str(src), "--out", str(out)],
        capture_output=True,
        text=True,
        env={**os.environ, "PUPPETEER_EXECUTABLE_PATH": chrome},
    )
    if proc.returncode != 0 and "puppeteer is not installed" in proc.stderr:
        pytest.skip("puppeteer not installed — PDF render reproducibility unverified")
    assert proc.returncode == 0, f"pdf.mjs failed: {proc.stderr}"
    assert "1 rendered as scans" in proc.stdout, (
        f"the scanned branch did not run, so it is untested: {proc.stdout}"
    )


def test_pdf_render_is_byte_reproducible(tmp_path):
    """Two renders of one source tree, two output directories, one hash —
    across BOTH branches, live-text and scanned (the scanned one rebuilds the
    document as one screenshot per page, so it is a second, quite different
    path to the same PDF writer).

    This is the check the whole change exists to satisfy: while it fails, a
    render tree cannot carry a manifest that means anything.
    """
    src = _source_tree(tmp_path / "room")
    first, second = tmp_path / "pdf-a", tmp_path / "pdf-b"

    _render_pdf(src, first)
    _render_pdf(src, second)

    hash_a, count_a = compute_content_hash(first)
    hash_b, count_b = compute_content_hash(second)
    assert count_a == count_b == 2
    assert hash_a == hash_b


def test_pdf_render_stamps_nothing_that_varies(tmp_path):
    """No `/CreationDate`, `/ModDate`, `/Producer` or `/Creator` may differ
    between two renders — and the two that remain must be the pinned value,
    not merely a value the two runs agreed on by being a second apart.

    Named separately from the hash comparison above because the two fail for
    different reasons and should be read differently: this one says WHAT is
    still moving, where the hash can only say that something is.
    """
    src = _source_tree(tmp_path / "room")
    first, second = tmp_path / "pdf-a", tmp_path / "pdf-b"

    _render_pdf(src, first)
    _render_pdf(src, second)

    def stamped(root: Path) -> dict:
        return {
            path.relative_to(root).as_posix(): sorted(
                _STAMPED.findall(path.read_bytes())
            )
            for path in sorted(root.rglob("*.pdf"))
        }

    a, b = stamped(first), stamped(second)
    assert set(a) == {"01_corporate/1.1.1_articles.pdf", "01_corporate/1.1.2_deed.pdf"}
    assert a == b

    for name, fields in a.items():
        assert fields == [
            (b"CreationDate", PINNED_DATE),
            (b"ModDate", PINNED_DATE),
        ], f"{name} carries {fields!r}"

    for path in sorted(first.rglob("*.pdf")):
        raw = path.read_bytes()
        assert b"/Producer" not in raw, f"{path.name} still names its producer"
        assert b"/Creator" not in raw, f"{path.name} still names its creator"
        _assert_xref_is_consistent(raw)
