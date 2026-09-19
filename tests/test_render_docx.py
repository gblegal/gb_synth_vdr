import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from synthvdr.render.docx import (
    _ATX_HEADING,
    _FENCE,
    RenderUnavailable,
    default_scanned_count,
    render_tree_docx,
    rotation_for,
    scanned_slots,
    write_scanned_csv,
)
from synthvdr.roomconf import SCAN_PROFILES, load_room_conf
from synthvdr.schema import Finding, FindingSet, load_findings

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


# --- the scan-profile vocabulary: declared in pdf.mjs, opted into in room.conf


def _extract_scan_profiles_source(mjs_text: str) -> str:
    """Pull the real SCAN_PROFILES table out of pdf.mjs, for the same reason
    _extract_rotation_for_source pulls the real rotationFor: a test carrying
    its own copy of the list proves the two authors agree, not that the
    shipped file does."""
    match = re.search(r"^const SCAN_PROFILES = \{.*?^\};$", mjs_text, re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError(
            "could not find `const SCAN_PROFILES = {...};` in synthvdr/render/pdf.mjs "
            "— has it been renamed or restructured? update the extraction regex"
        )
    return match.group(0)


def _run_node_scan_profiles(node: str):
    script = (
        f"{_extract_scan_profiles_source(PDF_MJS.read_text(encoding='utf-8'))}\n"
        "console.log(JSON.stringify(Object.keys(SCAN_PROFILES)));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's SCAN_PROFILES: {proc.stderr}")
    return json.loads(proc.stdout.strip())


def test_pdf_mjs_scan_profiles_match_python_exactly():
    """`synthvdr.roomconf.SCAN_PROFILES` is the list room.conf's SCAN_PROFILE
    key is validated against, and `pdf.mjs`'s own SCAN_PROFILES table is the
    list the renderer actually implements. They are two hand-maintained lists
    in two languages, exactly like rotationFor and ATX_HEADING above, and the
    drift between them is not cosmetic in either direction:

      * a name Python accepts that pdf.mjs does not means a room opts in, the
        loader is happy, and the render step dies at the last moment with an
        `unknown --scan-profile` from Node;
      * a name pdf.mjs implements that Python does not means room.conf refuses
        a profile the renderer can actually produce, so the tier is
        unreachable through the sanctioned build.

    Needs `node`, so it SKIPs (never silently passes) when node is
    unavailable — the same SKIP discipline as every gate in this project.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — cross-language scan-profile parity unverified")

    assert _run_node_scan_profiles(node) == list(SCAN_PROFILES)



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
    assert rows[0] == "slot", "pdf.mjs skips row 1 as a header; it must be one"
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
        f"const r = await loadScannedSlots({json.dumps(str(room / 'data-room'))});\n"
        "console.log(JSON.stringify({slots: [...r.slots].sort(), "
        "legacyPageRows: r.legacyPageRows}));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's loadScannedSlots: {proc.stderr}")

    loaded = json.loads(proc.stdout.strip())
    assert loaded["slots"], "pdf.mjs read the manifest as empty — the two halves are not connected"
    assert sorted(loaded["slots"]) == sorted(_csv_slots(room / "_key" / "scanned.csv"))
    # No page column any more, so nothing for pdf.mjs to warn about. A
    # non-zero count here would mean write_scanned_csv had regressed to the
    # two-column format whose page number selected nothing.
    assert loaded["legacyPageRows"] == 0


# --- fenced code blocks, the second half of the docx.py port --------------

FENCE_CORPUS = [
    "# Real heading",
    "```bash",
    "# a shell comment",
    "## not a heading either",
    "```",
    "# Heading again",
    "~~~",
    "# tilde-fenced comment",
    "~~~",
    "```python",
    "# unclosed fence runs to EOF",
    "# still inside the fence",
]


def _python_fenced_headings(lines):
    """What docx.py's own state machine makes of each line: the heading level
    for a heading, or None for a paragraph. Mirrors render_tree_docx's loop
    exactly — the shape the node side is compared against below."""
    out, in_fence, fence_char = [], None, None
    in_fence = False
    for raw in lines:
        stripped = raw.rstrip()
        if in_fence:
            out.append(None)
            closing = _FENCE.match(stripped)
            if closing and closing.group(1)[0] == fence_char:
                in_fence, fence_char = False, None
            continue
        fence = _FENCE.match(stripped)
        if fence:
            in_fence, fence_char = True, fence.group(1)[0]
            out.append(None)
            continue
        match = _ATX_HEADING.match(stripped)
        out.append(min(len(match.group(1)), 4) if match else None)
    return out


def test_pdf_mjs_fence_tracking_matches_python_exactly():
    """pdf.mjs shipped with no fence handling at all, so every `# comment`
    inside a fenced block became an <h1> while the DOCX render correctly left
    it a paragraph. docx.py's own comment names the case: "# a shell comment"
    is the most common line in any snippet, and is not a heading just because
    a fence is open.

    Runs the real FENCE regex out of the shipped pdf.mjs under node, same as
    the rotation and heading ports; SKIPs, never silently passes, without node.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — cross-language fence parity unverified")

    fence_src = _extract_pdf_mjs(r"^const FENCE = /.*/;$", "the `const FENCE = /.../;` line")
    atx_src = _extract_pdf_mjs(r"^const ATX_HEADING = /.*/;$", "the ATX_HEADING line")
    script = (
        f"{fence_src}\n{atx_src}\n"
        f"const lines = {json.dumps(FENCE_CORPUS)};\n"
        "let inFence = false, fenceChar = null;\n"
        "const out = [];\n"
        "for (const raw of lines) {\n"
        "  const line = raw.trimEnd();\n"
        "  if (inFence) {\n"
        "    out.push(null);\n"
        "    const c = FENCE.exec(line);\n"
        "    if (c && c[1][0] === fenceChar) { inFence = false; fenceChar = null; }\n"
        "    continue;\n"
        "  }\n"
        "  const f = FENCE.exec(line);\n"
        "  if (f) { inFence = true; fenceChar = f[1][0]; out.push(null); continue; }\n"
        "  const m = ATX_HEADING.exec(line);\n"
        "  out.push(m ? Math.min(m[1].length, 4) : null);\n"
        "}\n"
        "console.log(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's FENCE: {proc.stderr}")

    py_results = _python_fenced_headings(FENCE_CORPUS)
    assert json.loads(proc.stdout.strip()) == py_results

    # Pin the rule itself, so a pair of regexes that both matched nothing
    # could not satisfy the equality above.
    assert py_results[0] == 1, "a real heading outside any fence must stay a heading"
    assert py_results[2] is None and py_results[3] is None, (
        "'# a shell comment' inside a fence must not be a heading"
    )
    assert py_results[5] == 1, "the fence must close and headings resume after it"
    assert py_results[7] is None, "a ~~~ fence must be tracked as well as ```"
    assert py_results[-1] is None, "an unclosed fence must run to EOF"


def test_render_tree_docx_keeps_a_hash_comment_inside_a_fence_as_a_paragraph(tmp_path):
    """The Python side of the same rule, checked against real .docx output
    rather than the regex — this is the behaviour pdf.mjs is being held to."""
    src = tmp_path / "data-room" / "01_corporate"
    src.mkdir(parents=True)
    (src / "1.1.1_articles.md").write_text(
        "# Articles\n\n```bash\n# a shell comment\n```\n", encoding="utf-8"
    )
    out = tmp_path / "data-room-docx"
    render_tree_docx(tmp_path / "data-room", out)

    document = docx_module.Document(str(out / "01_corporate" / "1.1.1_articles.docx"))
    styles = {p.text: p.style.name for p in document.paragraphs}
    assert styles["Articles"].startswith("Heading")
    assert not styles["# a shell comment"].startswith("Heading")
    assert "```bash" in styles, "fence markers must survive the render verbatim"


# --- the scanned render is per PAGE, not per document-tall image ----------
#
# The manifest's page column used to select nothing but page 1, and the
# page-1 branch rotated one document-tall screenshot about its own centre, so
# the sideways displacement grew with the length of the document. Content
# still reached every page (Chrome flowed the tall image across them), so the
# fix is geometric: one page-sized image per page, each with its own skew.
#
# Driving the real renderer needs puppeteer and a Chromium, neither of which
# is a dependency of this package, so these pin the arithmetic and the
# constants out of the shipped file rather than rendering.


def test_pdf_mjs_page_box_is_a4_at_96dpi():
    width = int(_extract_pdf_mjs(r"^const PAGE_WIDTH_PX = (\d+);$", "PAGE_WIDTH_PX").split("=")[1].strip(" ;"))
    height = int(_extract_pdf_mjs(r"^const PAGE_HEIGHT_PX = (\d+);$", "PAGE_HEIGHT_PX").split("=")[1].strip(" ;"))
    # A4 is 210x297mm; at 96 CSS px/inch that is 793.7 x 1122.5, and Chrome
    # lays out at 96dpi. Wrong constants here would tile at the wrong
    # boundaries and cut every page in the same wrong place.
    assert (width, height) == (794, 1123), (width, height)
    assert round(height / width, 2) == round(297 / 210, 2), "page box must keep A4's aspect ratio"


@pytest.mark.parametrize(
    "content_height, expected_pages",
    [
        (0, 1),        # an empty document is still one page, never zero
        (500, 1),      # shorter than a page
        (1123, 1),     # exactly one page
        (1124, 2),     # one pixel over rolls to a second page
        (5246, 5),     # the 2,629-word deed this was verified against
    ],
)
def test_pdf_mjs_page_count_arithmetic(content_height, expected_pages):
    """Runs the real `const pageCount = ...` line out of the shipped pdf.mjs,
    so a change to the tiling rule cannot pass by agreeing with a copy kept
    here. The 5-page row is the case that matters: the old code called
    rotationFor exactly once no matter how long the document was."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — page-count arithmetic unverified")

    height_const = _extract_pdf_mjs(r"^const PAGE_HEIGHT_PX = \d+;$", "PAGE_HEIGHT_PX")
    count_expr = _extract_pdf_mjs(
        r"^  const pageCount = .*$", "the `const pageCount = ...` line"
    )
    script = (
        f"{height_const}\n"
        f"const contentHeight = {content_height};\n"
        f"{count_expr.strip()}\n"
        "console.log(pageCount);\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's pageCount: {proc.stderr}")
    assert int(proc.stdout.strip()) == expected_pages


def test_pdf_mjs_reports_a_legacy_page_column_instead_of_ignoring_it(tmp_path):
    """A manifest from before per-document scanning still loads — the first
    cell is the slot either way — but its page column now selects nothing, and
    saying so is the whole point: an obsolete column that parses cleanly and
    does nothing is how the page-1-only behaviour hid in the first place."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — legacy-manifest handling unverified")

    room = tmp_path / "room"
    (room / "data-room").mkdir(parents=True)
    (room / "_key").mkdir(parents=True)
    (room / "_key" / "scanned.csv").write_text(
        "slot,page\n01_a/1.1_x/1.1.1_a,1\n01_a/1.1_x/1.1.2_b,3\n", encoding="utf-8"
    )
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
        f"const r = await loadScannedSlots({json.dumps(str(room / 'data-room'))});\n"
        "console.log(JSON.stringify({slots: [...r.slots].sort(), "
        "legacyPageRows: r.legacyPageRows}));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run loadScannedSlots: {proc.stderr}")

    loaded = json.loads(proc.stdout.strip())
    assert loaded["slots"] == ["01_a/1.1_x/1.1.1_a", "01_a/1.1_x/1.1.2_b"], (
        "both slots must still be scanned — the page column is obsolete, not disqualifying"
    )
    assert loaded["legacyPageRows"] == 2, "every row carrying the dead column must be counted"


# --- the THIRD cross-language port: scan-degradation parameters -----------
#
# degradationFor is a pure function of (slot, page), like rotationFor, but
# hashes a `scan:`-prefixed string so its digest is independent of
# rotationFor's — otherwise a page's skew and its blur/grain would come from
# the same digest, correlating the most-tilted pages with the least legible
# ones.


def _extract_pdf_function(name: str) -> str:
    """Pull a top-level function's actual body out of pdf.mjs by brace-matching,
    rather than grepping the whole file or restating the code here. A whole-file
    grep for a needle that also appears in the explanatory comment ABOVE a
    function keeps passing after that function's behaviour is deleted; a test
    that carries its own copy of the code passes forever while the
    implementation drifts away from it. Anchoring to the function's own body
    avoids both."""
    source = PDF_MJS.read_text(encoding="utf-8")
    start = source.find(f"function {name}(")
    if start == -1:
        raise AssertionError(f"could not find `function {name}(...)` in synthvdr/render/pdf.mjs")
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[start : j + 1]
    raise AssertionError(f"{name}'s body is unbalanced in synthvdr/render/pdf.mjs")


def _extract_degradation_js() -> str:
    """Pull degradationFor's actual function body out of pdf.mjs, rather than
    restating it here — a test that carries its own copy of the code passes
    forever while the implementation drifts away from it."""
    return _extract_pdf_function("degradationFor")


def _scanned_render_source() -> str:
    """The bodies of the two functions that together implement the scanned-
    render path: renderScannedDocument (chooses PNG vs JPEG per profile and
    calls applyScanProfile) and applyScanProfile (builds the CSS filter chain
    and the grain overlay). Greps anchored to these two bodies cannot be
    satisfied by the explanatory comments that sit ABOVE them in the file —
    only by the code actually doing the work — unlike a whole-file grep."""
    return (
        _extract_pdf_function("renderScannedDocument")
        + "\n"
        + _extract_pdf_function("applyScanProfile")
    )


def _python_degradation(slot_id: str, page: int) -> dict:
    """The expectation, computed independently of the JS."""
    digest = hashlib.sha256(f"scan:{slot_id}:{page}".encode("utf-8")).digest()
    return {
        "quality": round(45 + (digest[0] / 255) * 35),
        "blurPx": 0.2 + (digest[1] / 255) * 0.5,
        "contrast": 0.85 + (digest[2] / 255) * 0.15,
        "brightness": 0.92 + (digest[3] / 255) * 0.13,
        "grain": 0.04 + (digest[4] / 255) * 0.08,
    }


def _run_node_degradation(node: str, pairs):
    script = (
        'import { createHash } from "node:crypto";\n'
        + _extract_degradation_js()
        + "\nconst pairs = "
        + json.dumps([[s, p] for s, p in pairs])
        + ";\n"
        "console.log(JSON.stringify(pairs.map(([s, p]) => degradationFor(s, p))));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's degradationFor: {proc.stderr}")
    return json.loads(proc.stdout)


def test_pdf_mjs_degradation_matches_python_exactly():
    """degradationFor must be a pure function of (slot, page) and agree with an
    independent implementation. SKIPs (never silently passes) without node."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; cannot check pdf.mjs's degradationFor")

    pairs = [
        ("01_corporate/1.1_constitutional/1.1.1_constitutional-01", 1),
        ("01_corporate/1.1_constitutional/1.1.1_constitutional-01", 2),
        ("13_pensions/13.4_correspondence/13.4.3_correspondence-03", 1),
        ("19_esg/19.4_climate/19.4.1_climate-01", 7),
    ]
    got = _run_node_degradation(node, pairs)
    for (slot, page), actual in zip(pairs, got):
        expected = _python_degradation(slot, page)
        assert actual["quality"] == expected["quality"], (slot, page)
        for key in ("blurPx", "contrast", "brightness", "grain"):
            assert abs(actual[key] - expected[key]) < 1e-12, (slot, page, key)


def test_degradation_is_independent_of_rotation():
    """The two derive from DIFFERENT digests. Sharing one would correlate a
    page's skew with its blur, so the worst-rotated pages would also be the
    worst-degraded ones and the tree would exercise a narrower range than its
    parameters suggest.

    A pure string grep against the source — it never runs node, so it needs
    no skip guard for node's absence."""
    source = PDF_MJS.read_text(encoding="utf-8")
    assert 'update(`scan:${slotId}:${page}`)' in source, (
        "degradationFor must hash a 'scan:'-prefixed string so its digest is "
        "independent of rotationFor's"
    )


def test_degradation_ranges_are_bounded():
    """Every parameter stays inside the spec's §5 range across 200 sampled
    (slot, page) hashes, so no page can be degraded past the realistic tier
    by an unlucky hash."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; cannot check pdf.mjs")
    pairs = [(f"slot-{i:03d}", (i % 9) + 1) for i in range(200)]
    for d in _run_node_degradation(node, pairs):
        assert 45 <= d["quality"] <= 80
        assert 0.2 <= d["blurPx"] <= 0.7
        assert 0.85 <= d["contrast"] <= 1.0
        assert 0.92 <= d["brightness"] <= 1.05
        assert 0.04 <= d["grain"] <= 0.12


def _run_node_parse_args(node: str, argv):
    """Run pdf.mjs's parseArgs on a synthetic argv, without running a render."""
    source = PDF_MJS.read_text(encoding="utf-8")
    start = source.find("const SCAN_PROFILES")
    if start == -1:
        raise AssertionError("could not find `const SCAN_PROFILES` in synthvdr/render/pdf.mjs")
    end = source.find("function parseArgs(")
    body_end = source.index("\n}", end) + 2
    script = (
        source[start:body_end]
        + "\ntry { console.log(JSON.stringify(parseArgs("
        + json.dumps(argv)
        + "))); } catch (e) { console.log(JSON.stringify({error: e.message})); }\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's parseArgs: {proc.stderr}")
    return json.loads(proc.stdout)


def test_scan_profile_defaults_to_none():
    """Every existing caller passes no --scan-profile and must keep today's
    behaviour. The default is the compatibility guarantee."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; cannot check pdf.mjs")
    args = _run_node_parse_args(node, ["--src", "data-room", "--out", "data-room-pdf"])
    assert args["scanProfile"] == "none"


def test_scan_profile_accepts_office():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; cannot check pdf.mjs")
    args = _run_node_parse_args(
        node,
        ["--src", "data-room", "--out", "data-room-pdf-scanned",
         "--scan-profile", "office"],
    )
    assert args["scanProfile"] == "office"


def test_unknown_scan_profile_is_refused_by_name():
    """A typo must stop the render, not silently produce a pristine tree named
    as though it were degraded — that is a wrong measurement rather than a
    missing one, and nothing downstream would notice."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; cannot check pdf.mjs")
    args = _run_node_parse_args(
        node,
        ["--src", "data-room", "--out", "out", "--scan-profile", "ofice"],
    )
    assert "error" in args
    assert "ofice" in args["error"]
    assert "none" in args["error"] and "office" in args["error"]


def test_scanned_render_uses_png_and_no_filter_under_none():
    """The default path must not move: PNG screenshots, no CSS filter, no
    grain overlay. This reads the source rather than rendering, so it runs
    without puppeteer.

    Anchored to renderScannedDocument's and applyScanProfile's own bodies
    (not the whole file) so this cannot be satisfied by the explanatory
    comment above them once the code itself is gone."""
    source = _scanned_render_source()
    assert "applyScanProfile" in source, "renderScannedDocument must consult the profile"
    assert 'profile.degrade ? "jpeg" : "png"' in source, (
        "the screenshot encoding must be chosen by the profile, PNG under none"
    )


def test_scanned_render_composes_filter_and_grain_when_degrading():
    """The three degradations the spec names must all reach the page: JPEG
    quality, the CSS filter chain, and the grain overlay.

    Anchored to renderScannedDocument's and applyScanProfile's own bodies (not
    the whole file): deleting the grain overlay must fail this test even
    though the comment describing it, a few lines above the function, still
    contains the same words."""
    source = _scanned_render_source()
    for needle in (
        "shotOptions.quality",
        "degradationFor(slotId, i + 1).quality",
        "filter:",
        "feTurbulence",
        "mix-blend-mode",
    ):
        assert needle in source, f"{needle!r} missing from pdf.mjs's scanned render"


def test_grain_seed_is_derived_not_random():
    """feTurbulence's seed must come from the hash, or the grain differs on
    every render and the tree stops being reproducible."""
    source = PDF_MJS.read_text(encoding="utf-8")
    assert "Math.random" not in source, "pdf.mjs must contain no RNG"
    assert "seed='${grainSeed}'" in source or 'seed=\\"${grainSeed}\\"' in source, (
        "feTurbulence's seed must be interpolated from a derived value"
    )


# --------------------------------------------------------------------------
# The test the whole scan profile exists to satisfy: does the degraded tree
# actually come out HARDER TO READ?
#
# Everything above this line asks whether the pipeline RAN — the right flag
# parsed, the right filter string composed, the same numbers on both sides of
# the language boundary. All of it passed against the profile that shipped,
# whose "scans" were pristine screenshots tilted by under a degree and which
# an OCR engine read at near-perfect confidence. Only a measurement of the
# rendered bytes can tell the difference, so this test renders two real trees
# and reads them with a real OCR engine.
#
# It SKIPs loudly rather than passing silently when Chrome, node or the OCR
# engine is missing: a silent pass here is worse than no test at all.
#
# rapidocr_onnxruntime is deliberately NOT in pyproject.toml, not even in an
# extra. It is a local measuring instrument for this one test, not part of
# what synthvdr ships, and adding it would put an onnxruntime build into
# every install to serve a test that most machines skip.

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

_TOKEN = re.compile(r"[a-z0-9]{3,}")

# An image XObject header in a Chrome-written PDF. Every page of a scanned
# render is one such image and nothing else, so pulling these out is reading
# the pages — no PDF rasteriser needed, which matters because there is no
# such library in this project's dependencies and this test must not add one.
_PDF_IMAGE = re.compile(
    rb"<<[^<>]*?/Subtype\s*/Image[^<>]*?/Length\s+(\d+)\s*>>\s*stream\r?\n", re.S
)


def _tokenise(text: str) -> set:
    """Lowercase alphanumeric runs of three or more characters.

    Three, not one: OCR noise is full of stray one- and two-character marks,
    and counting those as words would let a page of speckle score as readable.
    """
    return set(_TOKEN.findall(text.lower()))


def _pdf_page_images(pdf: Path):
    """Every full-colour image in `pdf`, in page order, as HxWx3 arrays.

    Chrome writes these as raw FlateDecode samples. Anything whose byte count
    is not exactly one RGB triple per pixel is skipped — which is how the
    degraded pages' DeviceGray /SMask companions are left out without this
    having to parse the object graph to find out they are masks.
    """
    import numpy as np

    blob = pdf.read_bytes()
    images = []
    for match in _PDF_IMAGE.finditer(blob):
        head = blob[match.start() : match.end()]
        width = int(re.search(rb"/Width\s+(\d+)", head).group(1))
        height = int(re.search(rb"/Height\s+(\d+)", head).group(1))
        raw = zlib.decompress(blob[match.end() : match.end() + int(match.group(1))])
        if len(raw) != width * height * 3:
            continue
        images.append(np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3))
    return images


def _ocr_tokens(engine, images) -> set:
    """Read every page image in `images` with the OCR engine and tokenise what
    it saw. Takes already-extracted images, not a path, so a caller that needs
    the page count first (see finding 4 below) does not decompress twice."""
    tokens = set()
    for image in images:
        result, _elapsed = engine(image)
        for _box, text, _confidence in result or []:
            tokens |= _tokenise(text)
    return tokens


def _render_pdf_tree(node: str, src: Path, out: Path, profile) -> None:
    """Drive the real pdf.mjs, with the system Chrome puppeteer must not go
    looking for on its own.

    `profile=None` omits `--scan-profile` entirely, to exercise the actual
    default argv every existing caller passes, rather than restating what the
    default is supposed to be."""
    env = {
        **os.environ,
        "PUPPETEER_EXECUTABLE_PATH": str(CHROME),
    }
    argv = [node, str(PDF_MJS), "--src", str(src), "--out", str(out)]
    if profile is not None:
        argv += ["--scan-profile", profile]
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    if proc.returncode != 0 and "puppeteer is not installed" in proc.stderr:
        pytest.skip("puppeteer is not installed; cannot render PDFs")
    assert proc.returncode == 0, f"pdf.mjs --scan-profile {profile} failed: {proc.stderr}"


def _build_mini_scan_tree(tmp_path: Path, build_xs_room):
    """Build one small room and copy its chosen scanned document, plus one
    born-digital control, into a source tree of their own.

    Shared by the byte-parity test and the OCR-survival test below, which
    both need to render this pair rather than the whole forty-document room:
    rendering all forty takes three quarters of a minute per profile and
    measures nothing the two do not.
    """
    room = build_xs_room(tmp_path / "room")
    blind = room / load_room_conf(room / "room.conf").get("BLIND_TREE")
    findings = load_findings(room / "_key" / "findings.yaml")

    # One scanned document, chosen by the real selector rather than by hand,
    # and written with the real manifest writer — a fixture that picked its
    # own slot could measure a document the shipped code would never scan.
    src = tmp_path / "mini" / "data-room"
    scanned_rel = write_scanned_csv(
        findings, 1, tmp_path / "mini" / "_key" / "scanned.csv"
    )[0]
    # A born-digital control alongside it: the first document that is not the
    # scanned one, so the byte comparisons below have something to compare.
    digital_rel = next(
        p.relative_to(blind).as_posix()
        for p in sorted(blind.rglob("*.md"))
        if p.relative_to(blind).as_posix() != scanned_rel
    )
    for rel in (scanned_rel, digital_rel):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(blind / rel, src / rel)
    return blind, src, scanned_rel, digital_rel


def test_scan_profile_none_is_byte_identical_to_default(tmp_path, build_xs_room):
    """`--scan-profile none` is the compatibility guarantee every existing
    room depends on: it must be byte-identical to passing no `--scan-profile`
    flag at all, which is what every caller before this branch — and every
    caller that has not been updated — actually does.

    This is a real measurement, not a read of the source: moving the filter
    chain out of the `if (!profile.degrade)` guard would still leave every
    string this file greps for present, and would still pass the source-only
    tests above. Only rendering both ways and comparing bytes catches that.

    Needs node and Chrome only, so it runs on every machine — it must not
    sit behind the rapidocr skip the way the byte comparisons below used to.
    """
    node = shutil.which("node")
    if node is None or not CHROME.exists():
        pytest.skip("needs node and system Chrome to render PDFs")

    _blind, src, scanned_rel, digital_rel = _build_mini_scan_tree(tmp_path, build_xs_room)

    default_tree = tmp_path / "pdf-default-argv"
    explicit_none_tree = tmp_path / "pdf-explicit-none"
    _render_pdf_tree(node, src, default_tree, None)
    _render_pdf_tree(node, src, explicit_none_tree, "none")

    for rel in (scanned_rel, digital_rel):
        target = rel.replace(".md", ".pdf")
        assert (default_tree / target).read_bytes() == (
            explicit_none_tree / target
        ).read_bytes(), (
            f"{rel}: --scan-profile none must render byte-identically to passing "
            "no --scan-profile flag at all"
        )


def test_office_profile_measurably_degrades_extracted_text(tmp_path, build_xs_room):
    """Render one small room twice, pristine and degraded, OCR both, and
    require that the degraded tree loses text the pristine one keeps.

    Needs Chrome and an OCR reader, so it SKIPs loudly rather than passing
    silently — this is the test the whole profile exists to satisfy, and a
    silent pass here is worse than no test.

    The rendered tree is two documents, not the whole fixture room: the room
    is built in full (so the scanned slot is a real evidence document with the
    prose the generator really writes), and the two documents the measurement
    needs are copied into a source tree of their own. Rendering all forty
    takes three quarters of a minute per profile and measures nothing the two
    do not.
    """
    node = shutil.which("node")
    if node is None or not CHROME.exists():
        pytest.skip("needs node and system Chrome to render PDFs")

    blind, src, scanned_rel, digital_rel = _build_mini_scan_tree(tmp_path, build_xs_room)

    pristine_tree = tmp_path / "pdf-none"
    degraded_tree = tmp_path / "pdf-office"
    repeat_tree = tmp_path / "pdf-office-again"
    _render_pdf_tree(node, src, pristine_tree, "none")
    _render_pdf_tree(node, src, degraded_tree, "office")
    _render_pdf_tree(node, src, repeat_tree, "office")

    def pdf_for(tree, rel):
        return tree / rel.replace(".md", ".pdf")

    # Spec §9 test 2: the profile touches SCANNED slots and nothing else. A
    # born-digital document is written by the same text path either way, so
    # its bytes must not move; the scanned one must.
    assert pdf_for(pristine_tree, digital_rel).read_bytes() == pdf_for(
        degraded_tree, digital_rel
    ).read_bytes(), (
        f"{digital_rel} is born-digital and must render identically under both "
        "profiles — the scan profile is leaking into unscanned documents"
    )
    assert pdf_for(pristine_tree, scanned_rel).read_bytes() != pdf_for(
        degraded_tree, scanned_rel
    ).read_bytes(), f"{scanned_rel} rendered identically under both profiles"

    # Spec §9 test 3: two renders of the same tree agree byte for byte. This
    # is what the derived grain seed and the hash-driven parameters are FOR —
    # an RNG anywhere in the degradation would show up here and nowhere else.
    assert pdf_for(degraded_tree, scanned_rel).read_bytes() == pdf_for(
        repeat_tree, scanned_rel
    ).read_bytes(), (
        "two office renders of the same tree differ — something in the "
        "degradation is not a pure function of (slot, page)"
    )

    # rapidocr is deliberately not a declared dependency (see the module
    # docstring above), so this import is the point past which the test
    # SKIPs on every machine but this one. It sits here, after every
    # assertion above that needs only node and Chrome, and not at the top of
    # the test — moved up, it would swallow those assertions into the skip
    # too, and nothing but this file's source-only grep tests would be left
    # guarding the compatibility and determinism guarantees they check.
    rapidocr = pytest.importorskip("rapidocr_onnxruntime")

    pristine_images = _pdf_page_images(pdf_for(pristine_tree, scanned_rel))
    degraded_images = _pdf_page_images(pdf_for(degraded_tree, scanned_rel))
    # The instrument, not the profile: _pdf_page_images silently drops any
    # image whose decompressed length isn't exactly W*H*3, which is how it
    # tells a real page from a DeviceGray /SMask companion. If a future
    # Chrome ever emitted a degraded page as DeviceGray instead, its tokens
    # would vanish here and the assertions below would report degradation
    # that was never actually measured. Equal, non-zero counts rule that out.
    assert len(pristine_images) == len(degraded_images) and len(pristine_images) > 0, (
        f"expected equal, non-zero page-image counts from both trees, got "
        f"{len(pristine_images)} pristine vs {len(degraded_images)} degraded page "
        f"image(s) for {scanned_rel} — the measuring instrument failed to read a "
        "page, not the scan profile"
    )

    engine = rapidocr.RapidOCR()
    pristine_tokens = _ocr_tokens(engine, pristine_images)
    degraded_tokens = _ocr_tokens(engine, degraded_images)
    source_tokens = _tokenise((blind / scanned_rel).read_text())

    pristine_survival = len(pristine_tokens & source_tokens) / len(source_tokens)
    degraded_survival = len(degraded_tokens & source_tokens) / len(source_tokens)
    print(
        f"\n{scanned_rel}: pristine survival {pristine_survival:.4f}, "
        f"degraded survival {degraded_survival:.4f}"
    )

    # The pristine tree is the control: OCR should read it nearly perfectly.
    assert pristine_survival > 0.90, (
        f"pristine scan only survived at {pristine_survival:.2f} — the control "
        "is broken, so the comparison below means nothing"
    )
    # The degraded tree must be measurably worse, and still readable. Both
    # bounds matter: no degradation measures nothing, and total destruction
    # measures nothing either (spec §10).
    #
    # 0.20, NOT the 0.05 the plan suggested, and the number is measured rather
    # than chosen. Stubbing degradationFor to quality 100 / no blur / no
    # contrast or brightness shift / no grain — a profile that still re-encodes
    # and re-composites the page but degrades NOTHING — still scores 0.86 here,
    # because compositing the filter layer makes Chrome resample the page. A
    # 0.05 margin passes that stub, which is precisely the profile this test
    # exists to fail. The real parameters score 0.69, so the threshold sits
    # between the two: the stub fails by 0.06 and the real profile passes by
    # 0.11. If a future Chrome moves the resampling, re-measure with the stub
    # before touching this number — it is the whole calibration.
    assert degraded_survival < pristine_survival - 0.20, (
        f"degraded scan survived at {degraded_survival:.2f} against pristine "
        f"{pristine_survival:.2f} — the office profile is not degrading anything "
        "beyond what re-encoding the page does on its own"
    )
    assert degraded_survival > 0.40, (
        f"degraded scan survived at only {degraded_survival:.2f} — this is the "
        "fax-quality tier, which is out of scope; loosen degradationFor's ranges"
    )


# --- page seams: the clip must land BETWEEN lines, not through one ---------
#
# known-issues §1. renderScannedDocument laid a document out at A4 and
# screenshotted it at fixed `y = i * 1123` offsets with no pagination
# anywhere, so a line straddling a boundary was cut through its glyphs — top
# half on one page, bottom half on the next, and OCR read neither. Measured at
# 0.12 of token survival on a PRISTINE scan, with no degradation applied.


def _extract_page_cuts_source(mjs_text: str) -> str:
    """Pull the real pageCutsFrom out of pdf.mjs, for the same reason
    _extract_rotation_for_source pulls the real rotationFor: a test carrying
    its own copy of the algorithm proves the two authors agree, not that the
    shipped file does."""
    match = re.search(r"function pageCutsFrom\([^)]*\)\s*\{.*?\n\}", mjs_text, re.DOTALL)
    if not match:
        raise AssertionError(
            "could not find `function pageCutsFrom(...)` in "
            "synthvdr/render/pdf.mjs — has it been renamed or inlined back "
            "into renderScannedDocument? update the extraction regex"
        )
    return match.group(0)


def _run_node_page_cuts(node: str, line_boxes, content_height, page_height):
    script = (
        f"{_extract_page_cuts_source(PDF_MJS.read_text(encoding='utf-8'))}\n"
        f"console.log(JSON.stringify(pageCutsFrom("
        f"{json.dumps(line_boxes)}, {content_height}, {page_height})));\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed to run pdf.mjs's pageCutsFrom: {proc.stderr}")
    return json.loads(proc.stdout.strip())


def test_page_cuts_never_fall_through_a_line():
    """The defect this exists to fix. With a line spanning 95..105 and a page
    height of 100, the old code clipped at exactly 100 — through the glyphs,
    leaving the top half on one page and the bottom half on the next, and OCR
    read neither. The cut must land at 90, the foot of the last line that
    fits, so the straddling line moves whole onto page 2."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — page-cut arithmetic unverified")

    lines = [[0, 10], [20, 30], [50, 60], [80, 90], [95, 105], [120, 130]]
    cuts = _run_node_page_cuts(node, lines, 200, 100)
    boundaries = {end for _start, end in cuts}
    for top, bottom in lines:
        assert not any(top < b < bottom for b in boundaries), (
            f"a cut falls through the line {top}..{bottom}: {sorted(boundaries)}"
        )
    assert cuts[0] == [0, 90]


def test_page_cuts_cover_the_whole_document_without_gaps_or_overlap():
    """Every pixel of content must appear on exactly one page. A gap silently
    drops text (the bug being fixed); an overlap duplicates it, which inflates
    token survival and is dishonest in the other direction."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — page-cut arithmetic unverified")

    lines = [[i * 25, i * 25 + 15] for i in range(12)]
    cuts = _run_node_page_cuts(node, lines, 300, 100)
    assert cuts[0][0] == 0
    assert cuts[-1][1] >= 300
    for (_, previous_end), (next_start, _) in zip(cuts, cuts[1:]):
        assert previous_end == next_start, f"gap or overlap at {previous_end}/{next_start}"


def test_a_line_taller_than_a_page_still_makes_progress():
    """The infinite-loop guard. No line bottom fits inside the first page, so
    the chooser must fall back to the hard limit rather than failing to
    advance. Accepting one bad cut on a pathological input beats hanging the
    render."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — page-cut arithmetic unverified")

    cuts = _run_node_page_cuts(node, [[0, 250]], 250, 100)
    assert len(cuts) >= 2
    assert cuts[0] == [0, 100]


def test_a_document_with_no_text_lines_still_paginates():
    """An image-only or empty document measures no line boxes at all. It must
    fall back to the fixed grid, not return zero pages and silently render
    nothing."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — page-cut arithmetic unverified")

    assert _run_node_page_cuts(node, [], 250, 100) == [[0, 100], [100, 200], [200, 250]]


def test_a_document_shorter_than_one_page_is_a_single_cut():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — page-cut arithmetic unverified")

    assert _run_node_page_cuts(node, [[0, 10]], 40, 100) == [[0, 40]]


def test_line_boxes_are_measured_per_visual_line_not_per_block():
    """A wrapped paragraph must yield one box per VISUAL line. Measuring per
    block instead would return one tall box for the whole paragraph, and the
    cut chooser would then treat a 40-line paragraph as an indivisible unit
    taller than a page — falling back to the hard clip every time and fixing
    nothing at all."""
    node = shutil.which("node")
    if node is None or not CHROME.exists():
        pytest.skip("needs node and system Chrome to measure line boxes")

    mjs = PDF_MJS.read_text(encoding="utf-8")
    match = re.search(r"async function lineBoxesFor\([^)]*\)\s*\{.*?\n\}", mjs, re.DOTALL)
    assert match, (
        "could not find `async function lineBoxesFor(...)` in "
        "synthvdr/render/pdf.mjs — update the extraction regex"
    )

    body = "<p style='font:16px/20px serif;margin:0;width:200px'>" + ("word " * 40) + "</p>"
    script = (
        "import puppeteer from 'puppeteer';\n"
        f"{match.group(0)}\n"
        f"const browser = await puppeteer.launch({{executablePath: {json.dumps(str(CHROME))}}});\n"
        "const page = await browser.newPage();\n"
        "await page.setViewport({width: 400, height: 600});\n"
        f"await page.setContent({json.dumps(body)});\n"
        "console.log(JSON.stringify(await lineBoxesFor(page)));\n"
        "await browser.close();\n"
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    boxes = json.loads(proc.stdout.strip())

    assert len(boxes) > 3, f"expected one box per wrapped line, got {len(boxes)}"
    for top, bottom in boxes:
        assert 0 < bottom - top < 40, f"box {top}..{bottom} is not one line tall"
    assert boxes == sorted(boxes), "boxes must be sorted by top"
