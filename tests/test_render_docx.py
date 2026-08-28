import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from synthvdr.render.docx import (
    _ATX_HEADING,
    RenderUnavailable,
    default_scanned_count,
    render_tree_docx,
    rotation_for,
    scanned_slots,
    write_scanned_csv,
)
from synthvdr.schema import Finding, FindingSet

PDF_MJS = Path(__file__).resolve().parent.parent / "synthvdr" / "render" / "pdf.mjs"

# 22 (slot_id, page) pairs chosen to exercise both signs and enough distinct
# slots/pages to catch a sign-bit or index error, not just agreement on one
# lucky value. rotation_for(...) over this set yields 11 positive, 11
# negative, 20 distinct values (two ties) — see
# test_pdf_mjs_rotation_matches_python_exactly below.
ROTATION_PAIRS = [
    (slot, page)
    for slot in (
        "11.1.1", "2.2.2", "9.9.9", "a", "b",
        "01_corp/1.1_x/1.1.1_other", "11_env/11.1_x/11.1.1_report",
        "3.3.3", "4.4.4", "zulu", "alpha",
    )
    for page in (1, 2)
]

docx_module = pytest.importorskip("docx", reason="python-docx not installed")


def findings():
    return FindingSet(
        [
            Finding(
                id="ENV-1", title="a", severity="critical", workstream="environmental",
                multi_document=False, source="11_env/11.1_x/11.1.1_report.md",
                location="x", substance="s",
            )
        ],
        "Project Testbed",
    )


def two_findings():
    return [
        Finding(
            id="ENV-1", title="a", severity="critical", workstream="environmental",
            multi_document=False, source="11_env/11.1_x/11.1.1_report.md",
            location="x", substance="s",
        ),
        Finding(
            id="CORP-1", title="b", severity="high", workstream="corporate",
            multi_document=False, source="01_corp/1.1_x/1.1.1_other.md",
            location="y", substance="t",
        ),
    ]


def test_rotation_is_deterministic_and_in_range():
    first = rotation_for("11.1.1", 1)
    assert first == rotation_for("11.1.1", 1)
    assert 0.4 <= abs(first) <= 1.1


def test_rotation_varies_by_page():
    assert rotation_for("11.1.1", 1) != rotation_for("11.1.1", 2)


def test_rotation_varies_by_slot():
    """A constant-angle implementation would be deterministic but useless —
    it must actually depend on which slot is being rotated, not just page."""
    assert rotation_for("11.1.1", 1) != rotation_for("2.2.2", 1)


def test_rotation_is_never_zero_and_stays_in_band():
    for slot, page in [("11.1.1", 1), ("2.2.2", 3), ("9.9.9", 42), ("a", 1), ("b", 1)]:
        angle = rotation_for(slot, page)
        assert 0.4 <= abs(angle) <= 1.1


def test_rotation_deterministic_across_processes_with_different_hash_seed():
    """sha256 does not depend on PYTHONHASHSEED, but Python's built-in
    hash() of strings does — the failure mode this guards against is a
    same-process test passing while a bare hash()-based implementation
    would silently vary from run to run. Exercise it across two real
    subprocesses with different seeds to actually catch that."""
    script = (
        "from synthvdr.render.docx import rotation_for; "
        "print(rotation_for('11.1.1', 1), rotation_for('2.2.2', 7))"
    )
    results = set()
    for seed in ("0", "1", "12345"):
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": seed, "PATH": __import__("os").environ.get("PATH", "")},
        )
        results.add(proc.stdout.strip())
    assert len(results) == 1


def test_scanned_slots_prefer_evidence_documents():
    slots = scanned_slots(findings(), count=1)
    assert slots == ["11_env/11.1_x/11.1.1_report.md"]


def test_scanned_slots_are_deterministic():
    assert scanned_slots(findings(), count=1) == scanned_slots(findings(), count=1)


def test_scanned_slots_stable_across_reordering():
    """The set of findings, not the order they were listed in, determines
    the chosen slots — a findings.yaml re-save that shuffles entries must
    not change which documents get scanned."""
    rows = two_findings()
    forward = FindingSet(rows, "Project Testbed")
    backward = FindingSet(list(reversed(rows)), "Project Testbed")
    assert scanned_slots(forward, count=2) == scanned_slots(backward, count=2)
    assert scanned_slots(forward, count=1) == scanned_slots(backward, count=1)


def test_scanned_slots_deterministic_across_processes_with_different_hash_seed():
    script = (
        "from synthvdr.schema import Finding, FindingSet\n"
        "from synthvdr.render.docx import scanned_slots\n"
        "fs = FindingSet([\n"
        "    Finding(id='ENV-1', title='a', severity='critical', workstream='environmental',\n"
        "            multi_document=False, source='11_env/11.1_x/11.1.1_report.md',\n"
        "            location='x', substance='s'),\n"
        "    Finding(id='CORP-1', title='b', severity='high', workstream='corporate',\n"
        "            multi_document=False, source='01_corp/1.1_x/1.1.1_other.md',\n"
        "            location='y', substance='t'),\n"
        "], 'Project Testbed')\n"
        "print(scanned_slots(fs, count=2))\n"
    )
    results = set()
    for seed in ("0", "1", "999"):
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": seed, "PATH": __import__("os").environ.get("PATH", "")},
        )
        results.add(proc.stdout.strip())
    assert len(results) == 1


def test_renders_every_markdown_file(tmp_path):
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_articles.md").write_text("# Articles\n\nA paragraph.\n")
    out = tmp_path / "data-room-docx"
    assert render_tree_docx(tmp_path / "data-room", out) == 1
    assert (out / "01_corporate" / "1.1.1_articles.docx").is_file()


def test_render_tree_docx_is_non_destructive(tmp_path):
    """render_tree_docx must never delete anything — not even files it did
    not write. Stale renders are gate 16's problem, not this writer's."""
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_articles.md").write_text("# Articles\n")
    out = tmp_path / "data-room-docx"
    out.mkdir(parents=True)
    unrelated = out / "01_corporate"
    unrelated.mkdir(parents=True)
    sentinel = unrelated / "not_mine.txt"
    sentinel.write_text("do not touch")

    render_tree_docx(tmp_path / "data-room", out)

    assert sentinel.is_file()
    assert sentinel.read_text() == "do not touch"


def test_render_tree_docx_is_idempotent(tmp_path):
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_articles.md").write_text("# Articles\n")
    out = tmp_path / "data-room-docx"
    first = render_tree_docx(tmp_path / "data-room", out)
    second = render_tree_docx(tmp_path / "data-room", out)
    assert first == second == 1
    assert (out / "01_corporate" / "1.1.1_articles.docx").is_file()


def test_render_unavailable_when_docx_import_fails(tmp_path, monkeypatch):
    """python-docx IS installed in this environment, so the RenderUnavailable
    branch can only be exercised by simulating its absence — patch it out
    of sys.modules so `from docx import Document` raises ImportError, the
    same failure mode a machine without python-docx installed would hit."""
    monkeypatch.setitem(sys.modules, "docx", None)
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_articles.md").write_text("# Articles\n")
    with pytest.raises(RenderUnavailable):
        render_tree_docx(tmp_path / "data-room", tmp_path / "data-room-docx")


def _extract_rotation_for_source(mjs_text: str) -> str:
    """Pull rotationFor's actual function body out of pdf.mjs, rather than
    letting the test carry its own copy of the formula. A test with its own
    copy proves the two authors agree, not that the shipped file matches —
    this one breaks the moment pdf.mjs's implementation drifts from
    synthvdr.render.docx.rotation_for, because it runs the real source."""
    match = re.search(r"function rotationFor\([^)]*\)\s*\{.*?\n\}", mjs_text, re.DOTALL)
    if not match:
        raise AssertionError(
            "could not find `function rotationFor(...)` in synthvdr/render/pdf.mjs "
            "— has it been renamed or restructured? update the extraction regex"
        )
    return match.group(0)


def _extract_crypto_import(mjs_text: str) -> str:
    match = re.search(r"^import .*createHash.*$", mjs_text, re.MULTILINE)
    if not match:
        raise AssertionError(
            "could not find the createHash import in synthvdr/render/pdf.mjs"
        )
    return match.group(0)


def _run_node_rotation(node: str, pairs):
    mjs_text = PDF_MJS.read_text(encoding="utf-8")
    import_line = _extract_crypto_import(mjs_text)
    fn_source = _extract_rotation_for_source(mjs_text)
    script = (
        f"{import_line}\n"
        f"{fn_source}\n"
        f"const pairs = {json.dumps(pairs)};\n"
        "console.log(JSON.stringify(pairs.map(([slot, page]) => rotationFor(slot, page))));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's rotationFor: {proc.stderr}")
    return json.loads(proc.stdout.strip())


def test_pdf_mjs_rotation_matches_python_exactly():
    """pdf.mjs's rotationFor is a hand-written JS port of
    synthvdr.render.docx.rotation_for, kept as two separate implementations
    because one is Python (DOCX path) and one is Node (PDF path). Nothing
    else in the harness compares them, so a scanned PDF and a DOCX render of
    the same slot could silently rotate differently if the port ever
    drifts — this is the only check that would catch it.

    python-docx being installed doesn't help here: this needs `node`, not
    `docx`, so it SKIPs (never silently passes) if node is unavailable,
    same SKIP discipline as every gate in this project.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — cross-language rotation parity unverified")

    js_values = _run_node_rotation(node, ROTATION_PAIRS)
    py_values = [rotation_for(slot, page) for slot, page in ROTATION_PAIRS]

    assert js_values == py_values
    assert any(v > 0 for v in py_values), "fixture pairs must cover the positive case"
    assert any(v < 0 for v in py_values), "fixture pairs must cover the negative case"


# --- the SECOND cross-language port: ATX heading detection ----------------
#
# rotation_for was not the only formula hand-ported into pdf.mjs, but it was
# the only one pinned. The heading regex was ported too, and drifted: pdf.mjs
# shipped `\s*` where docx.py has `[ \t]+` — optional AND Unicode-wide, both
# of the spellings _ATX_HEADING's own comment records as already having gone
# wrong once each on the Python side. Gate 16 compares filenames only and
# never opens a render, so the DOCX and PDF trees disagreed about what a
# heading was with nothing in the harness looking.

# Every line here is a documented boundary of the rule, not a sample: the two
# rejected separator spellings (absent, and non-ASCII whitespace), the level
# bound at 6, a title that legitimately starts with '#', and controls that
# must stay headings so a regex that simply never matches cannot pass.
HEADING_CORPUS = [
    "# Articles of association",     # ordinary heading (control)
    "###### Deepest real level",     # H6, the bound (control)
    "#\tTab separated",              # tab is a legal separator (control)
    "#   Extra spaces",              # greedy separator, title not re-trimmed
    "# #1 priority",                 # title legitimately begins with '#'
    "#MeToo campaign details",       # no separator -> paragraph
    "#1 supplier by volume",         # no separator -> paragraph, keeps its '#'
    "# NBSP separated",         # non-ASCII space -> paragraph
    "#　ideographic space",      # non-ASCII space -> paragraph
    "####### Beyond H6",             # 7 hashes -> paragraph
    "#",                             # bare hash -> paragraph
    "Ref #4821 was closed",          # mid-line hash -> paragraph
    "",                              # blank -> dropped by both
]


def _extract_atx_heading_source(mjs_text: str) -> str:
    """Pull the real ATX_HEADING declaration out of pdf.mjs, for the same
    reason _extract_rotation_for_source pulls the real rotationFor: a test
    carrying its own copy of the pattern proves the two authors agree, not
    that the shipped file does."""
    match = re.search(r"^const ATX_HEADING = /.*/;$", mjs_text, re.MULTILINE)
    if not match:
        raise AssertionError(
            "could not find `const ATX_HEADING = /.../;` in synthvdr/render/pdf.mjs "
            "— has it been renamed or inlined back into mdToHtml? update the "
            "extraction regex"
        )
    return match.group(0)


def _run_node_headings(node: str, lines):
    """What pdf.mjs's own regex makes of each line: (level, title) for a
    heading, or None for a paragraph — the same shape _python_headings
    returns below, so the two are directly comparable."""
    script = (
        f"{_extract_atx_heading_source(PDF_MJS.read_text(encoding='utf-8'))}\n"
        f"const lines = {json.dumps(lines)};\n"
        "console.log(JSON.stringify(lines.map((raw) => {\n"
        "  const m = ATX_HEADING.exec(raw.trimEnd());\n"
        "  return m ? [Math.min(m[1].length, 4), m[2]] : null;\n"
        "})));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's ATX_HEADING: {proc.stderr}")
    return json.loads(proc.stdout.strip())


def _python_headings(lines):
    out = []
    for raw in lines:
        match = _ATX_HEADING.match(raw.rstrip())
        out.append([min(len(match.group(1)), 4), match.group(2)] if match else None)
    return out


def test_pdf_mjs_headings_match_python_exactly():
    """pdf.mjs's ATX_HEADING is a hand-written JS port of
    synthvdr.render.docx._ATX_HEADING. Both renderers claim to present the
    same structure, and this is the only check that holds them to it.

    Needs `node`, not `docx`, so it SKIPs (never silently passes) when node
    is unavailable — same SKIP discipline as every gate in this project.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — cross-language heading parity unverified")

    py_results = _python_headings(HEADING_CORPUS)
    assert _run_node_headings(node, HEADING_CORPUS) == py_results

    # A pattern that matched nothing would satisfy the equality above, so
    # pin both sides of the rule the corpus exists to express.
    assert py_results[0] == [1, "Articles of association"], "controls must still be headings"
    assert py_results[4] == [1, "#1 priority"], "a title starting with '#' must survive intact"
    assert py_results[5] is None and py_results[7] is None, (
        "a missing or non-ASCII separator must not make a heading"
    )


# --- F1: ATX heading detection must require whitespace (round 2, fix 1) ----


def test_heading_requires_whitespace_after_hashes(tmp_path):
    """CommonMark requires whitespace between a '#' run and heading text --
    '#MeToo' and '#1 supplier' are paragraphs, not headings, in any
    dialect. The old `stripped.startswith('#')` check got both wrong, and
    `lstrip('# ')` then ate real content from any heading whose own title
    legitimately started with '#'. Exercises every documented failure mode
    plus a correctly-untouched control, by inspecting the actual .docx
    paragraphs and styles produced."""
    lines = [
        "#MeToo campaign details go here.",
        "#1 supplier by volume in the region.",
        "Ref #4821 was closed in March.",
        "#",
        "####### Beyond H6, still a paragraph.",
        "#\tTabbed heading.",
        "# #1 priority",
    ]
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_mixed.md").write_text("\n".join(lines) + "\n")
    out = tmp_path / "data-room-docx"

    render_tree_docx(tmp_path / "data-room", out)

    document = docx_module.Document(str(out / "01_corporate" / "1.1.1_mixed.docx"))
    by_text = {p.text: p.style.name for p in document.paragraphs}

    def is_heading(style_name):
        return style_name.lower().startswith("heading") or style_name.lower() == "title"

    # Paragraphs, not headings: no whitespace after the '#' run.
    assert not is_heading(by_text["#MeToo campaign details go here."])
    assert not is_heading(by_text["#1 supplier by volume in the region."])
    assert not is_heading(by_text["#"])
    assert not is_heading(by_text["####### Beyond H6, still a paragraph."])
    # Control: a '#' mid-line was never mistaken for a heading marker.
    assert not is_heading(by_text["Ref #4821 was closed in March."])
    # Genuine headings: whitespace (including a tab) after the '#' run,
    # and a heading whose title legitimately starts with '#' survives.
    assert is_heading(by_text["Tabbed heading."])
    assert is_heading(by_text["#1 priority"])


# --- F3: rotation_for's formula must be pinned by a non-skippable test ----


def test_rotation_matches_hardcoded_golden_values():
    """Hard-coded, non-skippable pin for rotation_for's exact formula.

    The cross-language check against pdf.mjs
    (test_pdf_mjs_rotation_matches_python_exactly) is a valuable
    ADDITIONAL guard, but it SKIPs wherever `node` is absent -- including
    plenty of CI containers -- so a skippable test must never be the sole
    pin for this invariant. These values were computed once, from the
    sha256-based formula, and are asserted byte-for-byte here; if the
    formula ever changes, this test fails everywhere, unconditionally,
    node or no node.
    """
    golden = {
        ("11.1.1", 1): -0.7211764705882353,
        ("11.1.1", 2): 1.0313725490196077,
        ("2.2.2", 1): -0.4494117647058824,
        ("a", 1): 0.5180392156862745,
        ("zulu", 3): 0.7294117647058824,
    }
    for (slot, page), expected in golden.items():
        assert rotation_for(slot, page) == expected


# --- F2: scanned_slots' sha256 keying must be pinned by a golden order ----


def _finding_with_source(fid, source):
    return Finding(
        id=fid, title="t", severity="high", workstream="corporate",
        multi_document=False, source=source, location="x", substance="s",
    )


def test_scanned_slots_golden_order_is_hash_driven_not_alphabetical():
    """Pins the ordering as hash-driven, not alphabetical. This fixture's
    alphabetical order and its sha256-digest order genuinely differ (the
    second and third entries swap), so dropping the
    `key=lambda p: hashlib.sha256(...)` from scanned_slots's second sort --
    collapsing selection to plain alphabetical order -- makes this test
    fail. A fixture where the two orders coincide would prove nothing."""
    paths = [
        "01_corp/1.1_x/1.1.1_alpha.md",
        "02_fin/2.1_y/2.1.1_beta.md",
        "03_ops/3.1_z/3.1.1_gamma.md",
        "04_env/4.1_w/4.1.1_delta.md",
    ]
    expected_hash_order = [
        "01_corp/1.1_x/1.1.1_alpha.md",
        "03_ops/3.1_z/3.1.1_gamma.md",
        "04_env/4.1_w/4.1.1_delta.md",
        "02_fin/2.1_y/2.1.1_beta.md",
    ]
    assert sorted(paths) != expected_hash_order  # the fixture's whole point

    fs = FindingSet(
        [_finding_with_source(f"CORP-{i}", p) for i, p in enumerate(paths, start=1)],
        "Project Testbed",
    )
    assert scanned_slots(fs, count=4) == expected_hash_order


# --- F1 (round 3): the ATX separator is space-and-tab, not \s -------------


# \x/\u escapes throughout, never literal characters, so the offending
# bytes are visible in a diff rather than invisible in the source. NBSP is
# the realistic case -- it survives copy-paste from word processors and
# web pages constantly -- and IDEOGRAPHIC SPACE matters because this
# harness already contemplates CJK documents elsewhere (the depth gate).
_NBSP = "\xa0"
_VERTICAL_TAB = "\x0b"
_FORM_FEED = "\x0c"
_EN_SPACE = "\u2002"
_IDEOGRAPHIC_SPACE = "\u3000"


def test_heading_separator_excludes_vertical_tab_and_form_feed():
    """Vertical tab and form feed are ASCII control characters that OOXML's
    XML backing store refuses outright -- writing either one into ANY
    .docx paragraph (heading or plain) raises from lxml before this
    module's heading logic even runs (verified by hand: python-docx's
    `run.text` setter hits lxml's "All strings must be XML compatible: ...
    no NULL bytes or control characters"). That is a real, but genuinely
    separate, defect -- control-character sanitisation for DOCX output,
    not ATX heading detection -- and out of scope for this fix. What IS in
    scope is the regex itself never treating either character as an ATX
    separator, checked directly here since round-tripping raw control
    bytes through a real .docx is not possible at all, for any line type.
    """
    assert _ATX_HEADING.match(f"#{_VERTICAL_TAB}Vertical tab heading attempt") is None
    assert _ATX_HEADING.match(f"#{_FORM_FEED}Form feed heading attempt") is None


def test_heading_separator_is_exactly_space_and_tab(tmp_path):
    """`\\s` is Unicode-wide and matches NBSP, EN SPACE and IDEOGRAPHIC
    SPACE -- under it, a hash followed by e.g. a non-breaking space
    silently became a heading, the same corruption class the round-2
    heading fix was written to close, reopened one character class wider.
    CommonMark's ATX separator set is exactly space and tab; verify these
    three legal-but-wide Unicode whitespace characters stay paragraphs
    (round-tripped through a real .docx, unlike the two ASCII control
    characters covered separately above, which cannot be), and that plain
    space and tab still work as real separators."""
    lines = [
        f"#{_NBSP}NBSP heading attempt",
        f"#{_EN_SPACE}EN SPACE heading attempt",
        f"#{_IDEOGRAPHIC_SPACE}Ideographic space heading attempt",
        "# Real space heading",
        "#\tReal tab heading",
    ]
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_ws.md").write_text("\n".join(lines) + "\n")
    out = tmp_path / "data-room-docx"

    render_tree_docx(tmp_path / "data-room", out)

    document = docx_module.Document(str(out / "01_corporate" / "1.1.1_ws.docx"))
    by_text = {p.text: p.style.name for p in document.paragraphs}

    def is_heading(style_name):
        return style_name.lower().startswith("heading") or style_name.lower() == "title"

    # Each of these three lines is untouched: still one paragraph, whole
    # line intact, hash included, not restyled as a heading.
    for line in lines[:3]:
        assert not is_heading(by_text[line]), line

    # Real separators still produce real headings.
    assert is_heading(by_text["Real space heading"])
    assert is_heading(by_text["Real tab heading"])


# --- F2: fenced code blocks must not leak into heading detection ---------


def test_fenced_code_block_lines_are_never_headings(tmp_path):
    """Inside a fence, '# a shell comment' is the single most common line
    in any shell or Python snippet -- it must never become a heading, and
    the fence markers themselves must survive the render (content survives
    the render; it is not this task's job to prettify output by dropping
    lines). Covers a backtick fence, a real heading immediately after the
    fence closes (to prove fence state actually clears), and an unclosed
    fence that must swallow the rest of the document."""
    lines = [
        "# Real heading before the fence",
        "```bash",
        "# a shell comment",
        "echo hello",
        "```",
        "# Real heading after the fence",
        "~~~",
        "# unclosed fence swallows this",
    ]
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_fenced.md").write_text("\n".join(lines) + "\n")
    out = tmp_path / "data-room-docx"

    render_tree_docx(tmp_path / "data-room", out)

    document = docx_module.Document(str(out / "01_corporate" / "1.1.1_fenced.docx"))
    paragraphs = [(p.text, p.style.name) for p in document.paragraphs]
    by_text = dict(paragraphs)

    def is_heading(style_name):
        return style_name.lower().startswith("heading") or style_name.lower() == "title"

    assert is_heading(by_text["Real heading before the fence"])
    assert is_heading(by_text["Real heading after the fence"])

    # Fence markers themselves survive as visible paragraphs -- not dropped.
    assert by_text["```bash"] == "Normal"
    assert by_text["```"] == "Normal"
    assert by_text["~~~"] == "Normal"

    # The shell comment inside the fence is a paragraph, not a heading --
    # the entire point of this fix.
    assert not is_heading(by_text["# a shell comment"])
    assert by_text["echo hello"] == "Normal"

    # An unclosed fence swallows the rest of the document: this line starts
    # with '#' and would ordinarily be a heading, but the fence never
    # closed before EOF.
    assert not is_heading(by_text["# unclosed fence swallows this"])

    # No content was silently dropped, and line order/count is preserved
    # 1:1. Headings strip their leading '#' run and separator by design
    # (the level moves into the style, not the text) -- compare those
    # against the matched title, not the raw line; every other line must
    # be verbatim.
    assert len(paragraphs) == len(lines)
    for (text, style), original in zip(paragraphs, lines):
        if is_heading(style):
            match = _ATX_HEADING.match(original)
            assert match is not None, original
            assert text == match.group(2)
        else:
            assert text == original


def test_fence_of_one_character_does_not_close_a_fence_of_the_other(tmp_path):
    """A ~~~ line inside a ``` fence does not close it (different fence
    character), and vice versa -- only a matching fence character closes."""
    lines = [
        "```",
        "~~~ this looks like a fence but is not the same character",
        "# still fenced, still not a heading",
        "```",
        "# real heading, fence is closed",
    ]
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_mixed_fence.md").write_text("\n".join(lines) + "\n")
    out = tmp_path / "data-room-docx"

    render_tree_docx(tmp_path / "data-room", out)

    document = docx_module.Document(str(out / "01_corporate" / "1.1.1_mixed_fence.docx"))
    by_text = {p.text: p.style.name for p in document.paragraphs}

    def is_heading(style_name):
        return style_name.lower().startswith("heading") or style_name.lower() == "title"

    assert not is_heading(by_text["# still fenced, still not a heading"])
    assert is_heading(by_text["real heading, fence is closed"])


# --- F3: heading level must map # through ###### correctly, with a clamp -


def test_heading_levels_map_correctly_and_clamp_at_four(tmp_path):
    """docx.py hard-codes `level = min(len(hashes), 4)` -- pin every level
    from h1 through h6 (the clamp applies from h4 upward) so a
    hard-coded `level = 1` (which left every prior test green) is caught."""
    lines = [
        "# H1 title",
        "## H2 title",
        "### H3 title",
        "#### H4 title",
        "##### H5 title",
        "###### H6 title",
    ]
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_levels.md").write_text("\n".join(lines) + "\n")
    out = tmp_path / "data-room-docx"

    render_tree_docx(tmp_path / "data-room", out)

    document = docx_module.Document(str(out / "01_corporate" / "1.1.1_levels.docx"))
    style_by_text = {p.text: p.style.name for p in document.paragraphs}

    expected_levels = {
        "H1 title": 1,
        "H2 title": 2,
        "H3 title": 3,
        "H4 title": 4,
        "H5 title": 4,  # clamped
        "H6 title": 4,  # clamped
    }
    for text, level in expected_levels.items():
        assert style_by_text[text] == f"Heading {level}", (text, style_by_text[text])


# --- the scanned-page manifest: written here, read by pdf.mjs ------------
#
# scanned_slots and pdf.mjs's loadScannedSlots were both written and both
# tested, and nothing ever produced the file between them: no room shipped a
# scanned page, while README and TECHNICAL-NOTES §5 described the feature as
# working. write_scanned_csv is that missing step, so these tests pin the
# join — not each half in isolation, which is how the gap survived.


def _findings_with(*paths):
    return FindingSet(
        [
            Finding(
                id=f"ENV-{i}", title="t", severity="high", workstream="environment",
                multi_document=False, source=path, location="", substance="s",
            )
            for i, path in enumerate(paths, start=1)
        ],
        "Project Testbed",
    )


def _csv_slots(path):
    rows = path.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "slot,page", "pdf.mjs skips row 1 as a header; it must be one"
    return [row.split(",")[0] for row in rows[1:] if row.strip()]


def test_write_scanned_csv_draws_only_from_markdown_evidence(tmp_path):
    """A CSV register named as evidence is real evidence with no page to
    scan — pdf.mjs only walks *.md. Selecting it and then dropping it would
    quietly return fewer scans than asked for."""
    findings = _findings_with(
        "01_corporate/1.1_x/1.1.1_a.md",
        "02_financial/2.1_y/2.1.1_register.csv",
        "05_commercial/5.1_z/5.1.1_c.md",
    )
    out = tmp_path / "_key" / "scanned.csv"
    slots = write_scanned_csv(findings, 3, out)
    assert all(s.endswith(".md") for s in slots)
    assert len(slots) == 2
    assert not any("register" in s for s in _csv_slots(out))


def test_write_scanned_csv_is_byte_identical_across_runs(tmp_path):
    """Determinism is a project-wide rule and this file feeds a render, so a
    reordering here would silently re-scan different documents between two
    builds of the same room."""
    findings = _findings_with(*(f"0{i}_s/{i}.1_x/{i}.1.1_d.md" for i in range(1, 8)))
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    write_scanned_csv(findings, 3, first)
    write_scanned_csv(findings, 3, second)
    assert first.read_bytes() == second.read_bytes()


def test_default_scanned_count_never_returns_zero_while_there_is_evidence(tmp_path):
    """A room with no scanned page does not test OCR at all, which is the one
    thing the PDF render adds over the markdown a tool could read directly."""
    assert default_scanned_count(_findings_with("01_a/1.1_x/1.1.1_a.md")) == 1
    assert default_scanned_count(_findings_with("01_a/1.1_x/1.1.1_a.csv")) == 0
    eight = _findings_with(*(f"0{i}_s/{i}.1_x/{i}.1.1_d.md" for i in range(1, 9)))
    assert default_scanned_count(eight) == 2


def _extract_pdf_mjs(pattern: str, what: str, flags: int = re.MULTILINE) -> str:
    """`flags` defaults to MULTILINE only. DOTALL is opt-in per call because a
    single-line pattern anchored with `$` silently swallows the rest of the
    file under it — which is exactly what this helper's first version did."""
    match = re.search(pattern, PDF_MJS.read_text(encoding="utf-8"), flags)
    if not match:
        raise AssertionError(
            f"could not find {what} in synthvdr/render/pdf.mjs — has it been "
            "renamed or restructured? update the extraction regex"
        )
    return match.group(0)


def test_scanned_csv_slots_match_pdf_mjs_slot_ids(tmp_path):
    """The `slot` column must equal the id pdf.mjs derives from its OWN walk.
    A mismatch is invisible — an unmatched slot is simply never scanned, and
    the render succeeds — so the expectation is taken from that file's real
    expression rather than restated here."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — cross-language slot-id parity unverified")

    rels = ["01_corporate/1.1_x/1.1.1_a.md", "11_environmental-hs/11.2_y/11.2.1_b.md"]
    out = tmp_path / "_key" / "scanned.csv"
    write_scanned_csv(_findings_with(*rels), len(rels), out)

    slot_expr = _extract_pdf_mjs(r"^\s*const slotId = .*$", "the `const slotId = ...` line")
    script = (
        'import path from "node:path";\n'
        f"const rels = {json.dumps(rels)};\n"
        "console.log(JSON.stringify(rels.map((rel) => {\n"
        f"{slot_expr}\n"
        "  return slotId;\n"
        "})));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's slotId expression: {proc.stderr}")
    assert sorted(json.loads(proc.stdout.strip())) == sorted(_csv_slots(out))


def test_pdf_mjs_loads_the_manifest_write_scanned_csv_writes(tmp_path):
    """The end-to-end pin: run pdf.mjs's REAL loadScannedSlots over a file
    this module actually wrote. Everything else here tests one side; this is
    the join whose absence meant no room ever shipped a scanned page."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — scanned-manifest round trip unverified")

    room = tmp_path / "room"
    (room / "data-room").mkdir(parents=True)
    rels = ["01_corporate/1.1_x/1.1.1_a.md", "05_commercial/5.1_y/5.1.1_b.md"]
    write_scanned_csv(_findings_with(*rels), len(rels), room / "_key" / "scanned.csv")

    loader = _extract_pdf_mjs(
        r"^async function loadScannedSlots\(src\) \{.*?\n\}",
        "loadScannedSlots",
        flags=re.MULTILINE | re.DOTALL,
    )
    script = (
        'import { readFile } from "node:fs/promises";\n'
        'import { existsSync } from "node:fs";\n'
        'import path from "node:path";\n'
        f"{loader}\n"
        f"const slots = await loadScannedSlots({json.dumps(str(room / 'data-room'))});\n"
        "console.log(JSON.stringify([...slots].map(([k, v]) => [k, [...v]]).sort()));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's loadScannedSlots: {proc.stderr}")

    loaded = json.loads(proc.stdout.strip())
    assert loaded, "pdf.mjs read the manifest as empty — the two halves are not connected"
    assert sorted(slot for slot, _ in loaded) == sorted(_csv_slots(room / "_key" / "scanned.csv"))
    # Page 1 for every row, because that is the only page pdf.mjs honours —
    # see write_scanned_csv on why a higher number must not be written yet.
    assert all(pages == [1] for _, pages in loaded)
