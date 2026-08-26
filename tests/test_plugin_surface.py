"""Structural tests for the synth-vdr plugin surface: skills, subagents, and the manifest.

Skills and subagents are markdown, so nothing else in this project's test suite would notice
one going missing, being renamed, or being emptied out — a Python import can't fail on a
file that was never Python. This module is the only thing standing between that and a
plugin that silently stops shipping a command: every assertion below reads the real files on
disk under the names this plugin actually declares (`SKILL_NAMES`, `AGENT_NAMES`), never a
fixture checked against itself.

Tasks 18 and 19 append tests here for the four skills and two subagents this task does not
yet create; `SKILL_NAMES` and `AGENT_NAMES` already name the full, final plugin surface, so
this file asserts against the complete list rather than only the two skills this task ships.

PENDING_SKILLS / PENDING_AGENTS are the parametrised cases for files that do not exist yet.
The house rule for this whole project is "never commit a red suite," which is unconditional —
a red suite committed now because a later task will fix it is still a red suite for whoever
checks out this commit in between. Those cases are marked `xfail(strict=True)` rather than
left to fail outright: `strict=True` means the marker itself expires the moment it stops
being true — if Task 18 adds `skills/vdr-build/SKILL.md` but leaves its xfail marker in
place here, the case XPASSes and the suite goes red on that alone, which is exactly the
prod that forces the marker's removal in the same change that makes it stale. A plain
`xfail` (non-strict) would let that happen silently, which is the same failure class the
project's gates call SKIP discipline: an expected gap must say so loudly enough that its own
resolution is forced to be noticed, not quietly tolerated forever.
"""

import ast
import importlib
import json
import os
import sys
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Sequence

import pytest
import yaml

import synthvdr
from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.qa.structural import SLOT_REF, parse_gaps_allowlist
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import (
    AUTHOR_OWNED_FINDING_FIELDS,
    Distractor,
    Finding,
    FindingSet,
    allocate_new_finding_ids,
    consolidate_wave_incoming,
    load_distractors,
    load_findings,
    render_findings_md,
    validate,
)
from synthvdr.score import (
    ToolFinding,
    ToolOutput,
    check_provenance,
    load_adjudications,
    validate_adjudications,
)
from synthvdr.slots import _subsection_name, slot_slug
from synthvdr.twin import TwinError, build_flagged_tree

ROOT = Path(__file__).resolve().parent.parent
SKILL_NAMES = ("vdr-scope", "vdr-findings", "vdr-build", "vdr-qa", "vdr-package", "vdr-score")
AGENT_NAMES = ("vdr-author", "vdr-auditor")

# Arrive in Task 18 ("/vdr-build" + the two subagents) and Task 19 ("/vdr-qa",
# "/vdr-package", "/vdr-score"). Remove each name from here in the same commit that adds
# its skill/agent file — leaving it in place after the file exists is a hard failure
# (XPASS under strict=True), by design; see the module docstring.
PENDING_SKILLS = ()
PENDING_AGENTS = ()

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
FENCED_YAML = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)
FENCED_MARKDOWN = re.compile(r"```markdown\n(.*?)\n```", re.DOTALL)
FENCED_JSON = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
FENCED_PYTHON = re.compile(r"```python\n(.*?)\n```", re.DOTALL)
FENCED_BASH = re.compile(r"```bash\n(.*?)\n```", re.DOTALL)
EMBEDDED_PYTHON_IN_BASH = re.compile(r'python3 -c "\n(.*?)\n"', re.DOTALL)


def _pending_param(name: str, pending: tuple) -> "pytest.param":
    if name in pending:
        return pytest.param(
            name, marks=pytest.mark.xfail(strict=True, reason=f"{name} arrives in a later task")
        )
    return pytest.param(name)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(path: Path) -> dict:
    match = FRONTMATTER.match(_read(path))
    assert match, f"{path} has no YAML frontmatter"
    return yaml.safe_load(match.group(1))


def body_after_frontmatter(path: Path) -> str:
    """The markdown body of a skill/agent file, with its frontmatter block removed.

    Used to assert the file is not JUST frontmatter — a file can pass every check above
    (exists, non-zero size, valid frontmatter, a description over 30 characters) while
    carrying no actual instructions, which is indistinguishable from "not written yet" to
    anyone reading the plugin's behaviour rather than its file listing.
    """
    text = _read(path)
    match = FRONTMATTER.match(text)
    assert match, f"{path} has no YAML frontmatter"
    return text[match.end():]


def yaml_examples(path: Path) -> List[str]:
    """Every fenced ` ```yaml ` code block's raw text in a skill/agent markdown file.

    Reusable across tasks: a skill that documents a YAML artefact by literal example
    (findings.yaml, distractors.yaml, gaps.yaml here in Task 17; wave manifests,
    adjudications.yaml, tool-output reports, etc. in Tasks 18-19) is checked by finding its
    example among these blocks and running it through the real loader — never by the test
    carrying its own copy of the expected content, the same rule the cross-language rotation
    test in tests/test_render_docx.py follows by reading its formula out of the shipped
    pdf.mjs rather than a Python re-implementation of it.
    """
    return FENCED_YAML.findall(_read(path))


def json_examples(path: Path) -> List[str]:
    """Every fenced ` ```json ` code block's raw text in a skill/agent markdown file.

    The JSON counterpart to `yaml_examples()`/`markdown_examples()` above, for an artefact
    that is JSON rather than YAML or prose — `_key/manifest.json` here in Task 19. Same rule:
    a test checks this text as extracted from the shipped skill file, never a copy kept here.
    """
    return FENCED_JSON.findall(_read(path))


def markdown_examples(path: Path) -> List[str]:
    """Every fenced ` ```markdown ` code block's raw text in a skill/agent markdown file.

    The markdown-example counterpart to `yaml_examples()` above, for an artefact that is
    prose rather than YAML — `_key/build-status.md` here in Task 18. Same rule applies: a
    test checks this text as extracted from the shipped skill file, never a copy of the
    expected content kept in the test.
    """
    return FENCED_MARKDOWN.findall(_read(path))


def python_examples(path: Path) -> List[str]:
    """Every fenced ` ```python ` code block's raw text in a skill/agent markdown file.

    Final review, test-suite gap: no ```python or ```bash fence in any skill was executed
    or even parsed by any test before this — exactly where F6 (a hardcoded subset total), F7
    (a circular verification step) and two `room_codename` NameErrors lived, none of them
    catchable by the YAML/JSON/markdown example tests above. This is the same class as those
    extractors: read the fence text out of the shipped file, never a copy kept here.
    """
    return FENCED_PYTHON.findall(_read(path))


def bash_examples(path: Path) -> List[str]:
    """Every fenced ` ```bash ` code block's raw text in a skill/agent markdown file."""
    return FENCED_BASH.findall(_read(path))


def embedded_python_snippets(bash_text: str) -> List[str]:
    """Every `python3 -c "..."` heredoc's inner Python source inside a fenced bash block.

    Several skills embed a real Python script inside a bash fence via `python3 -c "\\n...\\n"`
    (single-quoted Python string literals throughout, so the shell's own double quotes never
    collide with them) rather than a bare ` ```python ` fence, because the step also runs a
    plain shell command alongside it (e.g. `node ... pdf.mjs` next to a DOCX-render `python3
    -c`). `python_examples()` alone would miss these entirely.
    """
    return EMBEDDED_PYTHON_IN_BASH.findall(bash_text)


def find_example_by_marker(blocks: List[str], marker: str, source: Path) -> str:
    """The one fenced markdown block, among `blocks`, containing the literal `marker` text.

    Markdown has no top-level-key structure to key off the way `find_example_by_top_level_key`
    does for YAML, so this matches on a distinctive literal substring instead — and, exactly
    like its YAML counterpart, fails loudly rather than guessing if zero or more than one
    block qualifies.
    """
    matches = [block for block in blocks if marker in block]
    assert matches, f"{source}: no fenced ```markdown block containing {marker!r}"
    assert len(matches) == 1, (
        f"{source}: {len(matches)} fenced ```markdown blocks contain {marker!r} — "
        "expected exactly one canonical example"
    )
    return matches[0]


def find_example_by_top_level_key(blocks: List[str], key: str, source: Path) -> str:
    """The one fenced YAML block, among `blocks`, whose parsed top-level mapping has `key`.

    Fails loudly rather than guessing if zero or more than one block matches: a skill is
    expected to carry exactly one complete, canonical example per artefact it documents, and
    either failure mode here means the skill and this test have drifted apart in a way worth
    seeing immediately, not silently taking "whichever matched first".
    """
    matches = []
    for block in blocks:
        try:
            doc = yaml.safe_load(block)
        except yaml.YAMLError:
            continue
        if isinstance(doc, dict) and key in doc:
            matches.append(block)
    assert matches, f"{source}: no fenced ```yaml block with a top-level {key!r} key"
    assert len(matches) == 1, (
        f"{source}: {len(matches)} fenced ```yaml blocks have a top-level {key!r} key — "
        "expected exactly one canonical example"
    )
    return matches[0]


# Fix round 1, coordinator ruling: a minimal but real room.conf for SEMANTIC
# example-validation — see assert_evidence_paths_resolve_under_blind_tree() below. Every
# value is arbitrary except the three PATH_KEYS, which must be internally consistent for
# synthvdr.roomconf.load_room_conf / check_tree_identity to accept it at all.
_SEMANTIC_ROOM_CONF = """ROOM_CODENAME="Example Verification Room"
INDEX_TOTAL=1
BLIND_TOTAL=1
FLAGGED_TOTAL=1
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="XXX"
EXPECTED_KDP_CARRIERS=0
SECTION_DIRS="."
"""


def _domain_pack():
    """The one oracle an example cannot influence: the real section/subsection taxonomy the
    M&A domain pack declares, read fresh via `synthvdr.domain.load_domain` — the same
    function every real room-building step reads it through.

    Fix round 2, coordinator ruling: fix round 1's semantic check built its throwaway blind
    tree FROM the example's own paths (stripping one hard-coded `data-room/` prefix), so it
    validated the example's self-consistency, not its correctness — any internally
    consistent path passed, and a peer session found a `blind/`-prefixed path and a
    `14_operations` section that does not exist at all (the real one is
    `16_operations-quality`) both slipped through untouched.

    Fix round 3, coordinator ruling: the section check alone is not enough either — it
    caught the SECTION segment being wrong but not the SUBSECTION segment, and a peer
    session found four wrong subsection numbers (`11.3_site-reports` when site-reports is
    `11.2`, `11.5_hse` naming a subsection that does not exist at all, `11.4_permits` when
    permits is `11.1`) that a section-only oracle cannot see. Both checks now read from this
    one loaded pack, never from the example.
    """
    return load_domain(DEFAULT_DOMAIN_ROOT)


def _valid_subsection_names(section) -> set:
    """The exact set of subsection folder names `synthvdr.slots.build_slot_manifest` would
    ever generate for `section` — by calling the SAME function it calls
    (`synthvdr.slots._subsection_name`) rather than re-deriving the
    "section.number.index+1_name" format here a second time.

    Fix round 3, coordinator ruling: "reuse the existing slots machinery for the numbering —
    do not re-derive it. A second implementation of a numbering rule is exactly how the last
    three defects in this task started." (fix round 1's `data-room/` stripping, fix round
    2's fixture-built-from-the-example, and this round's under-validated oracle were each a
    parallel, ad hoc piece of logic standing in for something that already existed.)
    """
    return {_subsection_name(section, i) for i in range(len(section.subsections))}


FILENAME_SLOT_ID = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{1,3})_")


def _assert_path_matches_the_domain_pack(rel: str, owner: str, field: str) -> None:
    """The section-, subsection- and FILENAME-level oracle: `rel`'s first segment must be a
    real section directory, its second segment (if present) one of that section's real
    subsection folder names, and — for a markdown path with all three segments — its
    filename must be EXACTLY what `synthvdr.slots.build_slot_manifest` would emit for that
    section/subsection/ordinal. None of these three facts comes from the example; all three
    come from `_domain_pack()` and `synthvdr.slots.slot_slug`.

    Final review, F10: every prior round of this oracle checked the section and subsection
    segments but never the filename itself, so every shipped evidence-path example used an
    invented, descriptive filename (`11.2.4_phase-2.md`, `5.2.1_master-supply-agreement.md`,
    ...) that `build_slot_manifest` can never actually produce — it only ever emits
    `<slot-id>_<subsection-name>-<NN>.md`, never a freely chosen name. An author who copied
    one of those examples literally would author a document at a path `_key/anchors.csv`
    never lists, so it could never be selected as a real slot to author against. Checked only
    for `.md` paths: `Slot.rel_path` always ends `.md` (see slots.py), so a `.csv` evidence
    path is necessarily hand-authored outside the slot manifest and has no "expected filename"
    this oracle could check it against.
    """
    pack = _domain_pack()
    real_dirs = set(pack.section_dirs())
    segments = rel.split("/")
    section_dir = segments[0]
    assert section_dir in real_dirs, (
        f"{owner}: {field} {rel!r} starts with {section_dir!r}, which is not a real "
        f"section directory (domain/ma/sections.yaml declares: {sorted(real_dirs)}) — every "
        "source/corroboration/location/resolution path must start with one, never with "
        "BLIND_TREE's own name or any other invented segment"
    )
    if len(segments) < 2:
        return
    section = pack.section_by_dir(section_dir)
    subsection = segments[1]
    valid_subsections = _valid_subsection_names(section)
    assert subsection in valid_subsections, (
        f"{owner}: {field} {rel!r} names subsection {subsection!r}, which {section_dir} does "
        f"not have — its real subsections, in order, are: {sorted(valid_subsections)}"
    )
    if len(segments) < 3 or not rel.endswith(".md"):
        return
    filename = segments[2]
    stem = filename[: -len(".md")]
    match = FILENAME_SLOT_ID.match(stem)
    assert match, (
        f"{owner}: {field} {rel!r}'s filename {filename!r} does not start with a "
        "<section>.<subsection>.<ordinal>_ slot id, which every real slot's filename does"
    )
    sub_number, ordinal = int(match.group(2)), int(match.group(3))
    expected_stem = slot_slug(section, sub_number - 1, ordinal)
    assert stem == expected_stem, (
        f"{owner}: {field} {rel!r} names a filename build_slot_manifest can never emit for "
        f"this position — expected {expected_stem + '.md'!r}. build_slot_manifest only ever "
        "names a slot <slot-id>_<subsection-name>-<NN>.md; a descriptive filename like this "
        "one is not a real, authorable slot"
    )


def assert_evidence_paths_resolve_under_blind_tree(
    tmp_path: Path,
    findings: FindingSet,
    distractors: Sequence[Distractor] = (),
) -> None:
    """SEMANTIC validation for a findings/distractors example — reusable across every test
    that extracts one from a shipped skill file, and left in this shape so Task 20's
    end-to-end acceptance test can import and call it directly rather than re-deriving it.

    Two independent checks, deliberately kept separate because they catch different things
    (fix round 2, coordinator ruling):

    1. **The oracle** (`_assert_path_matches_the_domain_pack`). Every `source`/
       `corroboration`/`location`/`resolution` path's SECTION and SUBSECTION segments
       must both be real, in the domain pack's own ordering — a fact the example cannot
       influence. Fix round 1's check built its throwaway blind tree FROM the example's
       own paths and so only ever validated the example against itself, missing a
       `blind/`-prefixed path and an invented section like `14_operations`. Checking the
       section alone (fix round 2) still missed a wrong SUBSECTION number
       (`11.3_site-reports` when site-reports is really `11.2`) — this checks both.
    2. **The twin derivation** (`build_flagged_tree`, kept from fix round 1, no longer doing
       any path correction). Once every path names a real section, a real file is created at
       each of the example's own, completely unmodified paths, and the real
       `synthvdr.twin.build_flagged_tree` — what `/vdr-build` actually calls — is run over
       the result. This catches a path that is well-formed and in a real section but still
       cannot actually be built into a room (a filesystem-level collision between two
       evidence paths, for instance), which neither the schema check nor the oracle above
       would see.

    Final review, F1: `build_flagged_tree` never annotates a distractor (only finding
    evidence gets a block), but it does now refuse to build at all if a distractor's
    `location`/`resolution` names no real file — the same existence obligation findings
    already had. Passing `distractors` into the one `build_flagged_tree` call below, rather
    than re-deriving a second existence check here, means this test exercises the exact
    function `/vdr-build` calls instead of a parallel implementation of the same rule that
    could silently drift from it.
    """
    finding_paths = []
    for finding in findings.findings:
        finding_paths.append((finding.id, "source", finding.source))
        for corroboration_path in finding.corroboration:
            finding_paths.append((finding.id, "corroboration", corroboration_path))
    distractor_paths = []
    for distractor in distractors:
        distractor_paths.append((distractor.id, "location", distractor.location))
        distractor_paths.append((distractor.id, "resolution", distractor.resolution))
    all_paths = finding_paths + distractor_paths
    assert all_paths, "no evidence paths to verify — the example has no findings or distractors"

    for owner, field, rel in all_paths:
        _assert_path_matches_the_domain_pack(rel, owner, field)

    (tmp_path / "room.conf").write_text(_SEMANTIC_ROOM_CONF, encoding="utf-8")
    blind_root = tmp_path / "data-room"
    for _, _, rel in all_paths:
        target = blind_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"Placeholder content for {rel}.\n", encoding="utf-8")

    conf = load_room_conf(tmp_path / "room.conf")
    try:
        build_flagged_tree(tmp_path, conf, findings, distractors)
    except TwinError as exc:
        pytest.fail(
            "a findings/distractors example's evidence paths do not resolve under "
            f"BLIND_TREE — {exc}."
        )


@pytest.mark.parametrize("name", [_pending_param(n, PENDING_SKILLS) for n in SKILL_NAMES])
def test_every_skill_exists_with_matching_frontmatter(name):
    path = ROOT / "skills" / name / "SKILL.md"
    assert path.is_file(), f"missing {path}"
    assert path.stat().st_size > 0, f"{path} exists but is empty"
    meta = frontmatter(path)
    assert meta["name"] == name
    assert isinstance(meta["description"], str) and len(meta["description"]) > 30
    assert len(body_after_frontmatter(path).strip()) > 0, f"{path} has frontmatter but no body"


@pytest.mark.parametrize("name", [_pending_param(n, PENDING_AGENTS) for n in AGENT_NAMES])
def test_every_agent_exists_with_matching_frontmatter(name):
    path = ROOT / "agents" / f"{name}.md"
    assert path.is_file(), f"missing {path}"
    assert path.stat().st_size > 0, f"{path} exists but is empty"
    meta = frontmatter(path)
    assert meta["name"] == name
    assert isinstance(meta["description"], str) and len(meta["description"]) > 30


def _plugin_manifest() -> dict:
    return json.loads(_read(ROOT / ".claude-plugin" / "plugin.json"))


def test_plugin_manifest_is_valid_json_and_names_the_plugin():
    manifest = _plugin_manifest()
    assert manifest["name"] == "synth-vdr"


def test_plugin_manifest_version_agrees_with_package_version():
    """The manifest's version and `synthvdr.__version__` must be the same string.

    Carried from Task 1: this test file has always checked the manifest's NAME, but nothing
    ever checked its VERSION, so `.claude-plugin/plugin.json` and `synthvdr/__init__.py` have
    been kept in step by hand since Task 1 — with nothing that runs to catch the two drifting
    apart. This reads both files and compares them directly; it is not satisfied by either
    file agreeing with a copy of itself.
    """
    manifest = _plugin_manifest()
    assert manifest["version"] == synthvdr.__version__

    # pyproject.toml carries it a third time and is the one pip actually reads, so a
    # release that bumps the other two ships a package still claiming the old version.
    # Review 2026-08-26, R1: the published build being behind master is what made the
    # version numbers worth checking against each other in the first place.
    declared = re.search(
        r'(?m)^version = "([^"]+)"', _read(ROOT / "pyproject.toml")
    ).group(1)
    assert declared == synthvdr.__version__, (
        f"pyproject.toml says {declared}, synthvdr.__version__ says {synthvdr.__version__}"
    )

    # And a fourth time in the marketplace entry, which is the one that decides what
    # Claude Code offers. This repo IS its own marketplace (`"source": "./"`, registered
    # as a git source pointing at this remote), so master is the published artefact and
    # this entry is the published manifest — bumping the other three while leaving this
    # one behind ships a release nobody is offered. The entry had no `version` field at
    # all until the gap was found; a missing one is the same defect, silently.
    entry, = json.loads(_read(ROOT / ".claude-plugin" / "marketplace.json"))["plugins"]
    assert entry.get("version") == synthvdr.__version__, (
        f"the marketplace entry says {entry.get('version')!r}, "
        f"synthvdr.__version__ says {synthvdr.__version__!r}"
    )


def test_scope_skill_blocks_on_unresolved_name_collisions():
    body = _read(ROOT / "skills" / "vdr-scope" / "SKILL.md").lower()
    assert "gate a" in body
    assert "collision" in body


def test_findings_skill_states_the_hard_gate_before_authoring():
    body = _read(ROOT / "skills" / "vdr-findings" / "SKILL.md").lower()
    assert "gate b" in body
    assert "no authoring" in body or "before any authoring" in body


def test_findings_skill_expected_kdp_carriers_formula_is_markdown_only():
    """Final review, F8: the skill told an author to set EXPECTED_KDP_CARRIERS to
    `len(f.all_evidence_paths())` — EVERY evidence path, any suffix — but gate 8
    (structural.py) only ever counts MARKDOWN carriers, since a CSV register is never
    annotated. Any room with non-markdown evidence set the scalar too high and hard-FAILed
    gate 8 with a diagnosis pointing at room.conf rather than at this formula — the third
    recurrence of the same collision (fixed in the gate's code twice before, never in this
    prose). Pins that the shipped formula filters to markdown paths.
    """
    body = _read(ROOT / "skills" / "vdr-findings" / "SKILL.md")
    assert 'endswith(".md")' in body
    assert "all_evidence_paths()" in body
    # No other skill may state a DIFFERENT formula for the same value, or the two would
    # silently drift apart again exactly as this one did against the gate's own code.
    for name in SKILL_NAMES:
        if name == "vdr-findings":
            continue
        other = ROOT / "skills" / name / "SKILL.md"
        if other.is_file():
            assert "all_evidence_paths()" not in _read(other), (
                f"{other}: states its own EXPECTED_KDP_CARRIERS-related formula — "
                "there must be exactly one place this formula is stated"
            )


def test_scope_skill_checks_for_an_existing_room_before_overwriting():
    """Fix round 1, F3: a literal re-run of /vdr-scope must not silently overwrite a fact
    sheet and name-check record that may already be signed off at Gate A — the name-check
    verdicts each cost a real WebSearch, so silently discarding them is the same destructive-
    silent-write shape this project has fixed at the code level three times already (see the
    ledger's Task 7 rulings). Pinning the language rather than just the earlier prose fix,
    since a future edit could easily soften this back into "just skip the interview".
    """
    body = _read(ROOT / "skills" / "vdr-scope" / "SKILL.md").lower()
    assert "already exist" in body
    assert "overwrit" in body


def test_no_skill_tells_the_reader_to_hand_over_key_material():
    for name in SKILL_NAMES:
        path = ROOT / "skills" / name / "SKILL.md"
        if not path.is_file():
            # Covered by test_every_skill_exists_with_matching_frontmatter; this test
            # checks a content property that only applies to a skill that exists, and a
            # missing file is not itself an instance of that property being violated.
            continue
        assert "hand over _key" not in _read(path).lower()


# ---------------------------------------------------------------------------
# Final review, test-suite gap: no ```python or ```bash fence in any skill was ever
# executed, or even parsed, by any test — exactly where F6 (a hardcoded subset total),
# F7 (a circular verification step) and two `room_codename` NameErrors lived, all
# invisible to the YAML/JSON/markdown example tests above. These two tests are the
# floor every skill's fenced code must clear: real Python syntax, real shell syntax.
# Targeted execution tests further down in this file go further for the specific
# scripts the review named.
# ---------------------------------------------------------------------------


def test_every_python_fence_in_every_skill_is_syntactically_valid():
    for name in SKILL_NAMES:
        path = ROOT / "skills" / name / "SKILL.md"
        for block in python_examples(path):
            try:
                ast.parse(block)
            except SyntaxError as exc:
                pytest.fail(f"{path}: a ```python fence is not valid Python — {exc}\n{block}")


def test_every_bash_fence_in_every_skill_is_syntactically_valid():
    if not shutil.which("bash"):
        pytest.skip("bash not available in this environment")
    for name in SKILL_NAMES:
        path = ROOT / "skills" / name / "SKILL.md"
        for block in bash_examples(path):
            result = subprocess.run(
                ["bash", "-n"], input=block, capture_output=True, text=True
            )
            assert result.returncode == 0, (
                f"{path}: a ```bash fence fails `bash -n` — {result.stderr}\n{block}"
            )
            # Any python3 -c "..." heredoc embedded in this bash block must itself be
            # valid Python — `bash -n` only checks shell syntax, it never looks inside
            # a quoted string, so a NameError-class or syntax-level Python defect
            # embedded this way is otherwise invisible to both syntax checks at once.
            for snippet in embedded_python_snippets(block):
                try:
                    ast.parse(snippet)
                except SyntaxError as exc:
                    pytest.fail(
                        f"{path}: a python3 -c snippet inside a ```bash fence is not "
                        f"valid Python — {exc}\n{snippet}"
                    )


# ---------------------------------------------------------------------------
# Real EXECUTION of specific fenced scripts, not just syntax-checking. Syntax checking
# alone (ast.parse) cannot catch a NameError — `room_codename` used nowhere else in its
# own fence is perfectly valid Python syntax, and both `NameError`s the final review
# found in the shipped skills were exactly this shape. These tests run the shipped
# fence's own source (via exec(), never a copy retyped here) against a real filesystem.
# ---------------------------------------------------------------------------


def test_scope_skill_name_check_example_executes_without_a_nameerror(tmp_path, monkeypatch):
    """Final review, F10: `room_codename` was used and never bound anywhere in this exact
    fence — an instant NameError for anyone who copies it as shown. Runs the shipped fence
    verbatim in a real directory rather than eyeballing it for the fix.
    """
    path = ROOT / "skills" / "vdr-scope" / "SKILL.md"
    block = find_example_by_marker(python_examples(path), "render_name_check_md", path)
    (tmp_path / "_key").mkdir()
    monkeypatch.chdir(tmp_path)
    exec(compile(block, str(path), "exec"), {})
    assert (tmp_path / "_key" / "name-check.md").is_file()


def test_findings_skill_validate_example_executes_without_a_nameerror(tmp_path, monkeypatch):
    """Final review, F10: the second `room_codename` NameError, in the Gate B validate
    step — fixed by loading room.conf's ROOM_CODENAME instead of assuming a bound name.
    Runs the shipped fence verbatim against a real room.conf plus the skill's own
    findings.yaml/distractors.yaml examples (already proven to validate cleanly above).
    """
    path = ROOT / "skills" / "vdr-findings" / "SKILL.md"
    findings_yaml = find_example_by_top_level_key(yaml_examples(path), "findings", path)
    distractors_yaml = find_example_by_top_level_key(yaml_examples(path), "distractors", path)
    (tmp_path / "_key").mkdir()
    (tmp_path / "_key" / "findings.yaml").write_text(findings_yaml, encoding="utf-8")
    (tmp_path / "_key" / "distractors.yaml").write_text(distractors_yaml, encoding="utf-8")
    (tmp_path / "room.conf").write_text(
        'ROOM_CODENAME="Project Example"\n'
        "INDEX_TOTAL=1\nBLIND_TOTAL=1\nFLAGGED_TOTAL=1\n"
        'BLIND_TREE="data-room"\nFLAGGED_TREE="_key/flagged"\nKEY_ROOT="_key"\n'
        'FLAG_STRING_1="Key diligence points"\nFLAG_STRING_2="DD flag"\n'
        'FINDING_PREFIXES="ENV|FIN"\nEXPECTED_KDP_CARRIERS=0\n'
        'SECTION_DIRS="01_corporate"\n',
        encoding="utf-8",
    )
    block = find_example_by_marker(python_examples(path), "render_findings_md", path)
    monkeypatch.chdir(tmp_path)
    exec(compile(block, str(path), "exec"), {})
    assert (tmp_path / "_key" / "findings.md").is_file()
    assert "Project Example" in (tmp_path / "_key" / "findings.md").read_text()


def test_package_skill_manifest_script_executes_and_produces_a_real_hash(xs_room, monkeypatch):
    """Final review, F10: `compute_content_hash` — the single definition of the room's
    provenance hash — "exists only inside a python fence ... and has no test" (confirmed by
    the review by running it by hand against a real built room). Runs the shipped fence
    verbatim against a real built fixture room and checks the result independently, rather
    than trusting that a hand run once means it will keep working.
    """
    path = ROOT / "skills" / "vdr-package" / "SKILL.md"
    block = find_example_by_marker(python_examples(path), "compute_content_hash", path)
    monkeypatch.chdir(xs_room)
    exec(compile(block, str(path), "exec"), {})

    manifest = json.loads((xs_room / "_key" / "manifest.json").read_text())
    assert set(manifest) >= {"room", "content_hash", "documents", "findings", "built"}

    # Independently recompute the same hash a different way (sorted (path, sha256) pairs,
    # joined and hashed) and confirm it agrees — this is the "does it actually work"
    # check the review ran by hand; here it runs every time.
    import hashlib

    blind_root = xs_room / "data-room"
    entries = sorted(
        f"{p.relative_to(blind_root).as_posix()}\0{hashlib.sha256(p.read_bytes()).hexdigest()}"
        for p in blind_root.rglob("*")
        if p.is_file()
    )
    expected_hash = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    assert manifest["content_hash"] == expected_hash
    assert manifest["documents"] == len(entries)


def test_build_skill_consolidation_script_executes_and_regenerates_findings_md(tmp_path, monkeypatch):
    """Runs /vdr-build's Step 3 consolidation script verbatim, against real inputs, and
    confirms two things at once: `derive_prefix_for_workstream(pack.workstreams(), ...)`
    (F2) works end to end against the real domain pack, and `_key/findings.md` is actually
    regenerated (final review, F10 — previously written once at Gate B and never touched
    again, so it silently went stale the first time a wave discovered a new finding).
    """
    path = ROOT / "skills" / "vdr-build" / "SKILL.md"
    block = find_example_by_marker(python_examples(path), "consolidate_wave_incoming(findings_doc", path)

    (tmp_path / "_key" / "incoming").mkdir(parents=True)
    (tmp_path / "room.conf").write_text(
        'ROOM_CODENAME="Project Example"\n'
        "INDEX_TOTAL=1\nBLIND_TOTAL=1\nFLAGGED_TOTAL=1\n"
        'BLIND_TREE="data-room"\nFLAGGED_TREE="_key/flagged"\nKEY_ROOT="_key"\n'
        'FLAG_STRING_1="Key diligence points"\nFLAG_STRING_2="DD flag"\n'
        'FINDING_PREFIXES="CORP|FIN|TAX|FING|COMM|IP|IT|PROP|EMPL|REG|ENV|INS|PEN|DATA|'
        'LIT|OPS|MGMT|TXN|ESG|JV"\nEXPECTED_KDP_CARRIERS=0\nSECTION_DIRS="01_corporate"\n',
        encoding="utf-8",
    )
    (tmp_path / "_key" / "findings.yaml").write_text(
        "schema_version: 1\n"
        'room: "Project Example"\n'
        "findings:\n"
        "  - id: ENV-1\n"
        "    title: Seed finding\n"
        "    severity: high\n"
        "    workstream: environmental\n"
        "    multi_document: false\n"
        "    source: 01_corporate/1.1_x/1.1.1_x.md\n"
        "    substance: Seed.\n",
        encoding="utf-8",
    )
    (tmp_path / "_key" / "distractors.yaml").write_text(
        "schema_version: 1\ndistractors: []\n", encoding="utf-8"
    )
    (tmp_path / "_key" / "incoming" / "wave1-batch-a.yaml").write_text(
        "new_findings:\n"
        "  - id: wave1-batch-a-NEW-1\n"
        "    title: Newly discovered issue\n"
        "    severity: medium\n"
        "    workstream: operations\n"
        "    multi_document: false\n"
        "    source: 16_operations-quality/16.1_qms/16.1.1_x.md\n"
        "    substance: Discovered mid-authoring.\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    exec(compile(block, str(path), "exec"), {})

    updated = load_findings(tmp_path / "_key" / "findings.yaml")
    assert {f.id for f in updated.findings} == {"ENV-1", "OPS-1"}

    findings_md = (tmp_path / "_key" / "findings.md").read_text()
    assert "OPS-1" in findings_md, "findings.md was not regenerated with the new discovery"
    assert "ENV-1" in findings_md


def test_package_skill_subset_script_executes_against_a_real_room(xs_room, monkeypatch):
    """Final review, F6/test-suite gap: runs the shipped Step 2 subset-building script
    (embedded as a python3 -c heredoc inside a ```bash fence, so python_examples() alone
    would miss it) against a real built fixture room, confirming subset_total is genuinely
    derived from BLIND_TOTAL rather than a number retyped here, and that the derived subset
    is complete.
    """
    path = ROOT / "skills" / "vdr-package" / "SKILL.md"
    bash_block = find_example_by_marker(bash_examples(path), "build_subset", path)
    snippets = embedded_python_snippets(bash_block)
    assert len(snippets) == 1, f"{path}: expected exactly one python3 -c snippet"

    monkeypatch.chdir(xs_room)
    namespace = {}
    exec(compile(snippets[0], str(path), "exec"), namespace)
    assert namespace["report"].complete
    assert namespace["subset_total"] == min(40, max(50, 40 // 2))  # xs-room's BLIND_TOTAL is 40


def test_findings_and_distractors_examples_in_skill_validate_cleanly(tmp_path):
    """The findings.yaml/distractors.yaml examples `/vdr-findings` tells an author to copy
    must themselves load and validate through the real `synthvdr.schema` functions.

    Fix round 1 found the skill described these fields in prose without ever showing the
    shape, and an omitted `title`/`severity`/`workstream` breaks `load_findings` immediately
    for whoever follows the prose literally. Reading the examples straight out of the shipped
    skill file (not a copy kept here) means a future edit that drifts the example — drops a
    required field, breaks `validate()`'s cross-checks between findings and distractors — is
    caught in the commit that drifts it, instead of three tasks later when an author copies a
    now-broken example into a real room and Gate B's own validate step rejects it.
    """
    path = ROOT / "skills" / "vdr-findings" / "SKILL.md"
    blocks = yaml_examples(path)
    findings_yaml = find_example_by_top_level_key(blocks, "findings", path)
    distractors_yaml = find_example_by_top_level_key(blocks, "distractors", path)

    findings_path = tmp_path / "findings.yaml"
    findings_path.write_text(findings_yaml, encoding="utf-8")
    distractors_path = tmp_path / "distractors.yaml"
    distractors_path.write_text(distractors_yaml, encoding="utf-8")

    findings = load_findings(findings_path)
    distractors = load_distractors(distractors_path)
    assert findings.findings, "the findings.yaml example in the skill has no findings"
    assert distractors, "the distractors.yaml example in the skill has no distractors"

    errors = validate(findings, distractors)
    assert errors == [], f"the skill's own example fails validate(): {errors}"

    # render_findings_md is the other real consumer step 5 of the skill tells an author to
    # call; it must not raise on the skill's own example either.
    render_findings_md(findings, "Project Example")

    # SEMANTIC validation, fix round 1: the two checks above are schema-only and would not
    # have caught the data-room/-prefixed evidence paths this skill actually shipped with —
    # see assert_evidence_paths_resolve_under_blind_tree's docstring for the reproduction.
    assert_evidence_paths_resolve_under_blind_tree(tmp_path, findings, distractors)


def test_gaps_example_in_skill_matches_gate_9s_real_parser():
    """The gaps.yaml example `/vdr-findings` tells an author to copy must parse through the
    exact function gate 9 uses (`synthvdr.qa.structural.parse_gaps_allowlist`) — not a
    reimplementation of its shape kept in this test — and the `ref` it declares must actually
    be a slot-shaped token gate 9's `SLOT_REF` would recognise, or the example teaches the
    wrong lesson even though it happens to parse.
    """
    path = ROOT / "skills" / "vdr-findings" / "SKILL.md"
    gaps_yaml = find_example_by_top_level_key(yaml_examples(path), "gaps", path)

    allowed = parse_gaps_allowlist(gaps_yaml)
    assert allowed, "the gaps.yaml example in the skill allowlists nothing"
    for ref in allowed:
        assert SLOT_REF.fullmatch(ref), f"{ref!r} is not a slot-shaped ref gate 9 would recognise"

    # "reason" is this skill's own discipline, not gate 9's — parse_gaps_allowlist
    # deliberately never reads it, so it is checked here, straight off the parsed document.
    doc = yaml.safe_load(gaps_yaml)
    for row in doc["gaps"]:
        assert row.get("reason"), f"gaps.yaml example row {row!r} has no reason"


def test_build_skill_states_the_findings_first_ordering_rule():
    body = (ROOT / "skills" / "vdr-build" / "SKILL.md").read_text().lower()
    assert "findings-first" in body
    assert "resume" in body


def test_build_skill_names_which_gates_legitimately_fail_mid_build():
    """Final review, F5: Step 6 used to run all seventeen gates every wave and Step 7
    forbade proceeding on ANY failure, but gate 2 checks the room's FINISHED size
    (fixed by /vdr-scope before authoring starts) against what has only been authored
    so far — it must fail on every wave but the last for a multi-wave build (M/L/XL
    all take more than one wave), so the documented stop condition was unsatisfiable
    as written. This pins that the skill now names gate 2 (and gates 7/8's SKIP before
    anchors are complete) as expected exceptions, rather than silently reintroducing
    an impossible "clean on every wave" rule.
    """
    body = (ROOT / "skills" / "vdr-build" / "SKILL.md").read_text()
    assert "Multi-wave is the normal case" in body
    assert "expected to FAIL on every wave except the last" in body
    assert "## Anchors" in body


def _normalise_whitespace(text: str) -> str:
    """Collapse all whitespace runs to single spaces, so a sentence that happens to wrap
    across a markdown line break can still be matched as one literal string.
    """
    return " ".join(text.split())


def test_author_agent_is_forbidden_from_the_flagged_tree():
    """Task 18 fix round 2, F4: the original version of this test only checked that the
    words "never" and "flagged" appeared SOMEWHERE in the file — a reviewer proved that
    passes even after replacing the absolute prohibition with a sentence permitting the
    flagged tree, as long as "never" and "flagged" survive elsewhere. This asserts the
    load-bearing sentences THEMSELVES, verbatim (modulo whitespace), so reversing or
    softening either one is what breaks the test — not the mere presence of a keyword.
    """
    body = (ROOT / "agents" / "vdr-author.md").read_text()
    normalised = _normalise_whitespace(body)
    assert "never" in body.lower() and "flagged" in body.lower()
    assert "You never write to the flagged tree, under any name or any path." in normalised
    assert "that proof stops holding" in normalised


def test_auditor_agent_reads_only_the_blind_room():
    """Task 18 fix round 2, F4: same defect as the author test above — checked "blind" and
    "findings.yaml" appeared anywhere in the file, which survives the absolute prohibitions
    being weakened as long as those words remain elsewhere. Now asserts the two load-bearing
    sentences verbatim (modulo whitespace): the auditor never opens the flagged tree, and
    never opens `_key/findings.yaml` (or anything else under `_key/`) at any point.
    """
    body = (ROOT / "agents" / "vdr-auditor.md").read_text()
    normalised = _normalise_whitespace(body)
    assert "blind" in body.lower()
    assert "findings.yaml" in body.lower()
    assert (
        "You never open the flagged tree, under any name or any path, at any point in this "
        "task." in normalised
    )
    assert (
        "You never open `_key/findings.yaml`, or any other file under `_key/`, at any point "
        "in this task." in normalised
    )


def test_author_and_auditor_agents_declare_a_restricted_tool_set():
    """Task 18 fix round 2, F4 (defence in depth): prose is the only enforcement of the
    author/auditor separation today, and prose can be edited away by someone who does not
    know why it was there. A `tools:` frontmatter restriction cannot restrict WHICH PATHS an
    agent touches, but it can restrict WHICH TOOLS it has at all — an auditor with no
    Write/Edit tool cannot write to `_key/findings.yaml` or the flagged tree even if an
    instruction told it to, which is a stronger guarantee than an instruction alone.
    """
    author_tools = frontmatter(ROOT / "agents" / "vdr-author.md").get("tools", "")
    auditor_tools = frontmatter(ROOT / "agents" / "vdr-auditor.md").get("tools", "")
    assert author_tools, "vdr-author.md declares no tools: restriction"
    assert auditor_tools, "vdr-auditor.md declares no tools: restriction"

    auditor_tool_names = {t.strip() for t in auditor_tools.split(",")}
    assert "Write" not in auditor_tool_names and "Edit" not in auditor_tool_names, (
        "vdr-auditor must have no write access at all — it returns its verdict for "
        "/vdr-build to record, rather than writing to the answer key itself"
    )


def test_auditor_is_given_the_finding_substance_but_never_its_location():
    """Task 18 fix round 2, F1: the coordinator found the original design unanswerable — the
    auditor was told to judge whether "the offending clause is actually present" while being
    handed nothing that says what the clause IS. Design spec §5.1's own worked example
    (`audit_note: "Reachable from 11.3.4 + 2.6.2 without the key."`) only makes sense if the
    auditor already knows what it went looking for. What must stay withheld is WHERE the
    finding lives, not WHAT it is.
    """
    build_body = _read(ROOT / "skills" / "vdr-build" / "SKILL.md").lower()
    auditor_body = _read(ROOT / "agents" / "vdr-auditor.md").lower()

    assert "substance" in build_body, "the build skill must hand the auditor the substance"
    assert "not its substance" not in auditor_body, (
        "the auditor must no longer be told its finding's substance is withheld — it needs "
        "to know what the issue IS to look for it at all"
    )
    for withheld in ("source", "corroboration", "location"):
        assert withheld in auditor_body, f"the auditor's doc no longer mentions {withheld!r}"


def _build_skill_incoming_findings_rows():
    path = ROOT / "skills" / "vdr-build" / "SKILL.md"
    incoming_yaml = find_example_by_top_level_key(yaml_examples(path), "findings", path)
    rows = yaml.safe_load(incoming_yaml)["findings"]
    assert rows, "the incoming.yaml example in the skill has no findings"
    return rows


def test_incoming_findings_example_carries_only_the_authors_own_fields():
    """Review 2026-08-26, B1. This example USED to show a full `findings.yaml` row, and that
    is what invited the corruption: an author copying it echoed `workstream`, `title` and
    `corroboration` back, and `consolidate_wave_incoming`'s blanket `dict.update` wrote
    whatever it echoed straight into the signed-off registry. Consolidation is narrow now, so
    the example a subagent is told to copy must be narrow too — an example that raises when
    consolidated is worse than no example at all.
    """
    for row in _build_skill_incoming_findings_rows():
        assert set(row) == {"id", *AUTHOR_OWNED_FINDING_FIELDS}, (
            f"incoming `findings:` row {row['id']!r} carries {sorted(set(row))} — the author "
            f"owns 'id' plus {list(AUTHOR_OWNED_FINDING_FIELDS)} and nothing else"
        )


def test_incoming_findings_example_consolidates_into_a_registry():
    """The shape check above is necessary but not sufficient: it would still pass if the rows
    were narrow and the function rejected them anyway. Drive the real consolidation the skill
    names, against a registry holding the example's own ids.
    """
    rows = _build_skill_incoming_findings_rows()
    registry = {
        "schema_version": 1,
        "room": "Project Testbed",
        "findings": [
            {
                "id": row["id"],
                "title": f"Registry title for {row['id']}",
                "severity": "medium",
                "workstream": "financial",
                "multi_document": False,
                "source": "02_financial/2.1_statutory-accounts/2.1.3_accounts-03.md",
                "substance": "Registry substance, superseded by the author's refinement.",
            }
            for row in rows
        ],
    }

    result = consolidate_wave_incoming(registry, {"wave1-batch-a": {"findings": rows}}, {}, {})

    consolidated = {row["id"]: row for row in result.findings_doc["findings"]}
    for row in rows:
        assert consolidated[row["id"]]["substance"] == row["substance"]
        assert consolidated[row["id"]]["title"] == f"Registry title for {row['id']}", (
            "consolidating the skill's own example overwrote a Gate B field"
        )


NEW_FINDING_ID = re.compile(r"\A(?P<label>.+)-NEW-\d+\Z")


def test_new_findings_example_in_build_skill_has_full_finding_shape(tmp_path):
    """A `new_findings:` row (Task 18 fix round 1 — design spec §5.1: a finding discovered
    during authoring, not part of the Gate-B registry) carries a *provisional* id instead of
    a real one, but every other field is exactly the `findings.yaml` shape — it must load and
    validate the same way a real finding would once its id is swapped for a real one, or an
    author copying this example would produce a row `/vdr-build`'s consolidation step chokes
    on before it ever reaches `allocate_new_finding_ids`.
    """
    path = ROOT / "skills" / "vdr-build" / "SKILL.md"
    new_findings_yaml = find_example_by_top_level_key(yaml_examples(path), "new_findings", path)
    doc = yaml.safe_load(new_findings_yaml)
    rows = doc["new_findings"]
    assert rows, "the new_findings.yaml example in the skill has no rows"

    for row in rows:
        match = NEW_FINDING_ID.match(row["id"])
        assert match, f"{row['id']!r} is not a <label>-NEW-<n> provisional id"

    # Swap each provisional id for a placeholder real one — the shape check is about every
    # OTHER field, not the id itself, which is deliberately not real-finding-shaped yet.
    findings_doc = {
        "findings": [{**row, "id": f"PLACEHOLDER-{i}"} for i, row in enumerate(rows, start=1)]
    }
    findings_path = tmp_path / "new-findings-example.yaml"
    findings_path.write_text(yaml.safe_dump(findings_doc), encoding="utf-8")

    findings = load_findings(findings_path)
    errors = validate(findings, [])
    assert errors == [], f"the skill's own new_findings.yaml example fails validate(): {errors}"

    # SEMANTIC validation, fix round 1 — see assert_evidence_paths_resolve_under_blind_tree's
    # docstring: the new_findings example carried the same data-room/-prefixed source path
    # as the findings: example above it, which validate() cannot see.
    assert_evidence_paths_resolve_under_blind_tree(tmp_path, findings)


def test_new_findings_example_in_build_skill_allocates_deterministically_across_two_agents():
    """The `new_findings:` example must survive the exact allocation `/vdr-build`'s
    consolidation step performs (`synthvdr.schema.allocate_new_finding_ids`), and that
    allocation must give the same answer regardless of which order two parallel authors'
    incoming files were read in (Task 18 fix round 1 — an author never picks its own real
    finding id, because two authors racing for the same workstream would collide). Paired
    here with a second discovery in the SAME workstream from the same label (the case that
    actually depends on sort order — two discoveries in different workstreams never collide
    regardless of order, so a test using only those would pass even with the sort removed)
    and a third, independently labelled discovery for a different workstream, to exercise the
    actual multi-author scenario `/vdr-build` fans a wave out into. The shipped row supplies
    one discovery; the other two are synthetic, but only because the shipped example, by
    design, shows a single author's file with a single discovery, not a whole wave's.
    """
    path = ROOT / "skills" / "vdr-build" / "SKILL.md"
    new_findings_yaml = find_example_by_top_level_key(yaml_examples(path), "new_findings", path)
    row = yaml.safe_load(new_findings_yaml)["new_findings"][0]
    label = NEW_FINDING_ID.match(row["id"]).group("label")

    prefix_for_workstream = {row["workstream"]: "ENV", "operations": "OPS"}
    discoveries = [
        (label, row["id"], row["workstream"]),
        (label, f"{label}-NEW-2", row["workstream"]),
        ("wave2-batch-b", "wave2-batch-b-NEW-1", "operations"),
    ]
    existing_ids = {"ENV-1"}

    forward = allocate_new_finding_ids(existing_ids, prefix_for_workstream, discoveries)
    backward = allocate_new_finding_ids(
        existing_ids, prefix_for_workstream, list(reversed(discoveries))
    )
    assert forward == backward, "allocation must not depend on the order intakes were read in"
    assert forward[row["id"]] == "ENV-2"
    assert forward[f"{label}-NEW-2"] == "ENV-3"
    assert forward["wave2-batch-b-NEW-1"] == "OPS-1"


BUILD_STATUS_WAVE_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(PASS|FAIL)\s*\|\s*$", re.MULTILINE
)
BUILD_STATUS_NEXT_WAVE = re.compile(r"##\s*Next wave\s*\n+.*?Wave\s+(\d+)", re.DOTALL)


def test_build_status_example_in_build_skill_proves_the_resume_contract():
    """`_key/build-status.md` exists so an interrupted build can resume "without duplicating
    or losing slots" (Task 18 ruling 6) — and that property is mechanical, not a matter of
    prose. The literal example the skill tells an implementer to copy must itself satisfy it:
    every recorded wave shows a passing gate result (a failed wave is never recorded — see
    the skill's own text), wave numbers are consecutive starting at 1, and the "Next wave"
    section names exactly one more than the last completed wave. Read out of the shipped
    skill file, never a copy kept here, so a future edit that breaks the arithmetic — the
    exact way a stale resume pointer would duplicate or strand a wave — is caught in the
    commit that breaks it.
    """
    path = ROOT / "skills" / "vdr-build" / "SKILL.md"
    block = find_example_by_marker(markdown_examples(path), "# Build status", path)

    waves = [
        (int(wave), int(slots), gate)
        for wave, slots, gate in BUILD_STATUS_WAVE_ROW.findall(block)
    ]
    assert waves, f"{path}: build-status.md example has no wave rows"
    assert [w for w, _, _ in waves] == list(range(1, len(waves) + 1)), (
        "wave numbers in the build-status.md example must be consecutive, starting at 1"
    )
    assert all(gate == "PASS" for _, _, gate in waves), (
        "every recorded wave in the build-status.md example must show a passing gate result"
    )

    next_wave = BUILD_STATUS_NEXT_WAVE.search(block)
    assert next_wave, f"{path}: build-status.md example has no '## Next wave' naming a wave"
    assert int(next_wave.group(1)) == waves[-1][0] + 1, (
        "'Next wave' in the build-status.md example must name exactly one more than the "
        "last completed wave"
    )


FINAL_FINDING_ID = re.compile(r"\A[A-Z]+-\d+\Z")
BUILD_STATUS_NEW_FINDING_ROW = re.compile(
    r"^\|\s*([\w-]+)\s*\|\s*([A-Z]+-\d+)\s*\|\s*(\w+)\s*\|\s*$", re.MULTILINE
)


def test_build_status_example_new_findings_table_names_real_looking_ids():
    """`_key/build-status.md`'s "New findings this wave" section (Task 18 fix round 1) is
    the permanent record design spec §5.1 requires a mid-authoring discovery to be "declared
    in" — the provisional -> final id mapping `allocate_new_finding_ids` produced. Checked
    mechanically: every provisional id is `<label>-NEW-<n>` shaped (the only shape an author
    is ever allowed to write) and every final id is `PREFIX-<n>` shaped (a real, allocated
    finding id, never a provisional one left unresolved in the permanent record).
    """
    path = ROOT / "skills" / "vdr-build" / "SKILL.md"
    block = find_example_by_marker(markdown_examples(path), "# Build status", path)
    rows = BUILD_STATUS_NEW_FINDING_ROW.findall(block)
    assert rows, f"{path}: build-status.md example has no 'New findings this wave' rows"
    for provisional_id, final_id, workstream in rows:
        assert NEW_FINDING_ID.match(provisional_id), (
            f"{provisional_id!r} is not a <label>-NEW-<n> provisional id"
        )
        assert FINAL_FINDING_ID.match(final_id), f"{final_id!r} is not a real PREFIX-<n> id"
        assert workstream, "every 'New findings this wave' row must name its workstream"


def test_qa_skill_documents_strict_mode():
    body = (ROOT / "skills" / "vdr-qa" / "SKILL.md").read_text().lower()
    assert "--strict" in body
    assert "skip" in body


def test_package_skill_requires_strict_before_freezing():
    body = (ROOT / "skills" / "vdr-package" / "SKILL.md").read_text().lower()
    assert "--strict" in body
    assert "manifest" in body


def test_package_skill_subset_total_is_derived_not_hardcoded():
    """Final review, F6: the skill hardcoded a subset total of 900 against a default room
    of 200 documents, so build_subset's cap-at-what-exists behaviour made subset/ a byte
    copy of the whole room and gate 11 passed trivially — a "bounded run" that was not
    bounded at all, silently. Pins that the shipped script derives the total from the
    room's own BLIND_TOTAL instead of a fixed number.
    """
    body = (ROOT / "skills" / "vdr-package" / "SKILL.md").read_text()
    assert "900" not in body
    assert "BLIND_TOTAL" in body
    assert "subset_total" in body


def test_package_skill_step_1_is_not_circular():
    """Final review, F7: Step 1 used to say run `--strict` and re-run it until clean
    before doing anything else, but gates 11/16 SKIP until Steps 2-3 build their inputs
    and `--strict` converts every skip to a failure — a literal reader could never get a
    clean Step 1 to "unlock" Step 2. Pins that the first QA run in the skill is explicitly
    non-strict, and that exactly one later step is named as the real, strict release gate.
    """
    body = (ROOT / "skills" / "vdr-package" / "SKILL.md")
    text = body.read_text()
    assert "not yet strict" in text.lower()
    assert "the actual release gate" in text.lower()
    # exactly one `--strict` gate run in the whole skill, not two competing ones
    assert text.count("python3 -m synthvdr.qa --room . --strict") == 1


def test_score_skill_records_adjudications_rather_than_re_deriving_them():
    body = (ROOT / "skills" / "vdr-score" / "SKILL.md").read_text().lower()
    assert "adjudicat" in body
    assert "room_hash" in body


def test_manifest_example_in_package_skill_verifies_via_the_real_scorer(tmp_path):
    """Handoff Task 19, carried ruling 3: `/vdr-package` must write `content_hash` into
    `_key/manifest.json` in the EXACT form `synthvdr.score.check_provenance` reads, or the
    provenance check is permanently inert — a tool's output from one room could be scored
    against a different room's answer key and produce a confident, meaningless number with
    nothing able to catch it.

    This does not just eyeball the skill's prose: it extracts the literal `_key/manifest.json`
    example from the SHIPPED skill file, feeds it to a throwaway room, builds a ToolOutput
    whose `room_hash` equals that example's `content_hash`, and runs it through the real
    `check_provenance` — the exact function `python3 -m synthvdr score` calls. A skill that
    documents a plausible-looking manifest shape the scorer does not actually read would pass
    every prose-only check and fail only here.
    """
    path = ROOT / "skills" / "vdr-package" / "SKILL.md"
    blocks = json_examples(path)
    assert len(blocks) == 1, f"{path}: expected exactly one fenced ```json manifest example"
    manifest_doc = json.loads(blocks[0])
    for key in ("room", "content_hash", "documents", "findings", "built"):
        assert key in manifest_doc, f"{path}: manifest.json example is missing {key!r}"

    key_dir = tmp_path / "_key"
    key_dir.mkdir()
    (key_dir / "manifest.json").write_text(json.dumps(manifest_doc), encoding="utf-8")

    matching_output = ToolOutput(tool="acme/1.0", room_hash=manifest_doc["content_hash"], findings=[])
    status = check_provenance(tmp_path, matching_output)
    assert status.verified is True, status.detail

    mismatched_output = ToolOutput(tool="acme/1.0", room_hash="not-the-real-hash", findings=[])
    with pytest.raises(Exception):
        check_provenance(tmp_path, mismatched_output)


# Fix round 3, coordinator ruling: a FIXED fixture, declared once here, completely
# independent of anything a `_key/adjudications.yaml` example says. This is deliberately
# NOT derived from the example under test — ten tool reports (indices 0-9) and three
# real-looking finding ids, chosen because they comfortably cover the shipped example's
# actual content (tool_index up to 7; finding ids ENV-1/EMP-2/FIN-3), never because they
# were read out of it. Sizing or naming this fixture FROM the example (as this test
# previously did — `max(a.tool_index for a in adjudications)` for the report count, the
# example's own finding_id values for the known-id set) makes any tool_index or finding_id
# the example names trivially "in range" or "known": a mutated example
# (`tool_index: 400, finding_id: TOTALLY-BOGUS-999`) sailed straight through 31/31 green.
# validate_adjudications is meant to raise loudly on exactly those two cases; a fixture that
# reshapes itself around the artefact it is checking cannot ever let it.
_ADJUDICATION_FIXTURE_OUTPUT = ToolOutput(
    tool="acme/1.0",
    room_hash="",
    findings=[ToolFinding(title="t", severity="medium", documents=[]) for _ in range(10)],
)
_ADJUDICATION_FIXTURE_FINDINGS = FindingSet(
    findings=[
        Finding(
            id=finding_id,
            title="t",
            severity="medium",
            workstream="w",
            multi_document=False,
            source="doc.md",
            location="loc",
            substance="s",
        )
        for finding_id in ("ENV-1", "EMP-2", "FIN-3")
    ],
    room="Test Room",
)


def test_adjudications_example_in_score_skill_loads_via_the_real_parser(tmp_path):
    """The `_key/adjudications.yaml` example `/vdr-score` tells an adjudicator to copy must
    itself load and reconcile through the real `synthvdr.score` functions — the same
    discipline Task 17's fix round established for findings.yaml/distractors.yaml, and Task 18
    for incoming.yaml/new_findings.yaml. Read straight out of the shipped skill file, never a
    copy kept here, so a future edit that drifts the shape (a `finding_id` type
    `load_adjudications` would reject, a `tool_index` out of range) is caught in the commit
    that drifts it rather than the first time a real adjudicator copies a now-broken example.

    Reconciled against `_ADJUDICATION_FIXTURE_OUTPUT`/`_ADJUDICATION_FIXTURE_FINDINGS` — a
    fixture declared independently above, not derived from the example itself (fix round 3;
    see the module-level comment there for why that distinction is load-bearing).
    """
    path = ROOT / "skills" / "vdr-score" / "SKILL.md"
    adjudications_yaml = find_example_by_top_level_key(yaml_examples(path), "adjudications", path)

    adjudications_path = tmp_path / "adjudications.yaml"
    adjudications_path.write_text(adjudications_yaml, encoding="utf-8")
    adjudications = load_adjudications(adjudications_path)
    assert adjudications, "the adjudications.yaml example in the skill has no rows"

    # Must not raise: every tool_index is in range and every finding_id is known, checked
    # against the fixed fixture above — never against anything sized or named from
    # `adjudications` itself.
    validate_adjudications(adjudications, _ADJUDICATION_FIXTURE_OUTPUT, _ADJUDICATION_FIXTURE_FINDINGS)


def test_pyproject_declares_its_own_packages():
    """`pip install -e .` — the first command in TECHNICAL-NOTES §1 — must actually work.

    This repository is a flat layout: `synthvdr/` sits beside `agents/`, `skills/`,
    `domain/`, `schemas/`, `fixtures/`, `tests/` and `tools/`. Given more than one
    top-level candidate and no explicit declaration, setuptools refuses to build at all
    ("Multiple top-level packages discovered in a flat-layout"), and every documented
    install command fails.

    Nothing else in this suite catches that, because pytest imports `synthvdr` from the
    repo root — the tested path and the *documented* path are disjoint, which is exactly
    how the defect this test guards reached master. Assert the declaration is present and
    still resolves to the Python package alone.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[build-system]" in text, "pyproject.toml declares no build backend"
    assert "[tool.setuptools.packages.find]" in text, (
        "pyproject.toml does not declare its packages, so setuptools falls back to "
        "flat-layout auto-discovery and refuses to build"
    )

    # Every importable package in the repo, discovered the way setuptools would, but
    # without importing setuptools — a bare venv on 3.12+ no longer ships it, and this
    # test must not need a build backend installed to say whether one is configured.
    #
    # Dot-directories are skipped wholesale rather than named one at a time. `.venv` used
    # to be the only one worth excluding, until a git worktree checked out under
    # `.claude/worktrees/` put a second copy of this entire repo inside the first: the walk
    # then found `.claude.worktrees.<name>.synthvdr` and the assertion below failed on the
    # main checkout while passing inside the worktree. Nothing under a dot-directory is an
    # importable package of THIS project, whatever the next tool to create one calls it.
    packages = {
        ".".join(d.relative_to(ROOT).parts)
        for d in ROOT.rglob("__init__.py")
        for d in [d.parent]
        if not any(part.startswith(".") for part in d.relative_to(ROOT).parts)
        and "build" not in d.parts
    }
    assert packages == {"synthvdr", "synthvdr.qa", "synthvdr.render", "tests"}, packages

    # Everything the wheel must carry is matched by the declared `synthvdr*` glob; `tests`
    # is the one package outside it and is deliberately not shipped.
    assert {p for p in packages if p.startswith("synthvdr")} == packages - {"tests"}

    # The stanza is only necessary because discovery is genuinely ambiguous here. If this
    # ever stops being true the repo has been restructured, and the reasoning above —
    # and the comment in pyproject.toml — needs revisiting rather than silently holding.
    top_level = {p.name for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")}
    assert len(top_level) > 1, top_level


def test_every_make_target_the_docs_tell_you_to_run_exists():
    """The Makefile is a thin wrapper, so the only way it can be wrong is by
    drifting from the docs that point at it — a renamed or dropped target
    leaves an instruction that fails on a newcomer's first command, which is
    precisely the papercut `make test` was added to remove.

    Only real commands count: a line inside a ```bash fence, or an inline
    `make x` in backticks. Matching bare prose instead reads "make an empty
    folder for the room" in the README as a target named `an`.
    """
    import re

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    declared = set(re.findall(r"^([a-zA-Z][\w-]*):", makefile, re.MULTILINE))

    referenced = {}
    for name in ("README.md", "TECHNICAL-NOTES.md", "ARCHITECTURE.md"):
        path = ROOT / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        commands = []
        for block in re.findall(r"```bash\n(.*?)```", text, re.DOTALL):
            commands += re.findall(r"^\s*make ([a-z][\w-]*)", block, re.MULTILINE)
        commands += re.findall(r"`make ([a-z][\w-]*)[^`]*`", text)
        for target in commands:
            referenced.setdefault(target, name)

    assert referenced, "no `make <target>` commands found — has the docs wiring changed?"
    missing = {t: doc for t, doc in referenced.items() if t not in declared}
    assert not missing, f"documented but not declared in the Makefile: {missing}"


def test_the_licence_file_matches_what_the_manifests_declare():
    """A repo whose manifest and LICENCE file disagree states two different
    sets of terms at once, which is worse than either alone — and it is only
    discoverable by reading both. Caught exactly that before publication:
    plugin.json said MIT while an open PR added GPL v3.
    """
    import json

    licence_file = ROOT / "LICENSE"
    assert licence_file.is_file(), "no LICENSE file, but the manifests declare a licence"
    body = licence_file.read_text(encoding="utf-8")

    declared = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["license"]
    first_line = body.strip().splitlines()[0].strip()
    assert declared.lower() in first_line.lower(), (
        f"plugin.json declares {declared!r} but LICENSE opens with {first_line!r}"
    )
    assert "Greg Baker" in body, "LICENSE names no copyright holder"


def test_scope_skill_checks_former_company_names_not_just_current_ones():
    """A company that HELD a name and later renamed keeps its history but not
    its name, so neither a plain web search nor an exact current-name register
    lookup will surface it. Found in the wild: the shipped xs-room fixture's
    "Halstead Fasteners Limited" collides with HENRY HALSTEAD (FASTENERS)
    LIMITED (00725298), which held that name 1962-1999, is still trading under
    a new one, and is in the same industry — recorded "clear" by a check that
    only ever looked at names in use today.
    """
    body = _read(ROOT / "skills" / "vdr-scope" / "SKILL.md").lower()
    assert "former name" in body or "previous name" in body, (
        "the name check does not tell the author to look at former company names"
    )
    assert "companies house" in body, (
        "the entity check names no company register to search former names in"
    )


def test_the_generated_name_check_record_states_the_register_limits():
    """The record is what the user actually reads at Gate A, so it must carry
    the same caveats the skill tells the author to say out loud — otherwise
    the signed-off artefact claims less doubt than the check earned.
    """
    from datetime import date

    from synthvdr.namecheck import Verdict, render_name_check_md

    body = render_name_check_md(
        [Verdict(text="Ashfell Trading Limited", kind="entity", verdict="clear",
                 checked=date(2026, 1, 1).isoformat(), note="")],
        "Project Ashfell",
    ).lower()
    assert "former name" in body or "previous name" in body
    assert "trade mark" in body or "trademark" in body


# ---------------------------------------------------------------------------
# Review 2026-08-26, B2. The ordering rule drifted from the code it described:
# the skill said "sort by tier" and glossed tier `A` as carrying a finding,
# while `build_slot_manifest` assigned tier positionally at /vdr-scope time,
# before any finding existed. Prose alone could drift because nothing checked
# it. The rule is a function now, and these check the skill still calls it.
# ---------------------------------------------------------------------------


def test_build_skill_orders_waves_with_authoring_order_not_by_tier():
    body = _read(ROOT / "skills" / "vdr-build" / "SKILL.md")
    assert "authoring_order" in body, (
        "the build skill must name the function that owns the ordering rule"
    )
    assert "sort the slot list by tier" not in body, (
        "the tier-sort rule is the B2 defect — it must not come back as prose"
    )


def test_every_skill_import_resolves_to_something_real():
    """A skill's fenced example is the only interface most of this package has, and
    `ast.parse` above proves only that it is syntactically Python. A renamed or deleted
    function leaves the fence parsing perfectly and failing the moment anyone runs it.
    """
    for name in SKILL_NAMES:
        path = ROOT / "skills" / name / "SKILL.md"
        blocks = python_examples(path) + [
            snippet
            for block in bash_examples(path)
            for snippet in embedded_python_snippets(block)
        ]
        for block in blocks:
            for node in ast.walk(ast.parse(block)):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                if not node.module.startswith("synthvdr"):
                    continue
                module = importlib.import_module(node.module)
                for alias in node.names:
                    assert hasattr(module, alias.name), (
                        f"{path}: an example imports {alias.name!r} from {node.module}, "
                        "which does not exist"
                    )


# ---------------------------------------------------------------------------
# Review 2026-08-26, S1. /vdr-scope invented the deal in step 2 and generated the
# slot manifest in step 4, so the fiction was written with no knowledge of which
# sections the room would contain or how substantial a document each demanded.
# The domain pack allocates a slot to all 20 workstreams even at XS, several with
# a 2,500-word floor, so a deal invented without them had to be retro-fitted with
# bank debt, a pension arrangement and a minority stake AFTER the user signed the
# fact sheet off at Gate A — the exact change Gate A exists to prevent.
# ---------------------------------------------------------------------------


def _scope_body():
    return _read(ROOT / "skills" / "vdr-scope" / "SKILL.md")


def test_vdr_scope_generates_the_structure_before_inventing_the_deal():
    body = _scope_body()
    structure = body.index("## 2. Generate the structure")
    fact_sheet = body.index("## 3. Invent the deal and write the fact sheet")
    name_check = body.index("## 4. Check every invented name")
    assert structure < fact_sheet < name_check, (
        "the slot manifest must be generated before the fact sheet is written, or the "
        "deal is invented with no knowledge of what the room will demand of it"
    )


def test_vdr_scope_names_every_section_that_demands_a_longform_document():
    """The skill tells the author which sections need a substantial document before they
    invent anything, and names them literally. That list is derived from the domain pack,
    so it goes stale the moment the pack changes — which is how the B2 defect happened one
    file over. Recompute it and check the prose still matches.
    """
    from synthvdr.qa.depth import floor_for
    from synthvdr.slots import SIZE_PRESETS, build_slot_manifest

    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    longform = pack.archetypes["longform"].floor
    heaviest = {}
    for slot in build_slot_manifest(pack, SIZE_PRESETS["XS"]):
        floor = floor_for(slot.slot_id, Path(slot.rel_path).name, slot.tier, pack)
        heaviest[slot.section_dir] = max(heaviest.get(slot.section_dir, 0), floor)

    body = _scope_body()
    demanding = sorted(d for d, f in heaviest.items() if f == longform)
    assert demanding, "this test is meaningless if no XS section carries the longform floor"
    for section_dir in demanding:
        assert section_dir in body, (
            f"/vdr-scope does not warn that {section_dir} demands a {longform}-word "
            "document at XS — the author will invent a deal that cannot fill it"
        )
    assert str(longform) in body or f"{longform:,}" in body, (
        "the skill quotes the longform floor in prose; it has drifted from the pack"
    )


def test_vdr_scope_warns_that_every_workstream_gets_a_slot_even_at_xs():
    # The other half of the same trap: a section with a low floor still needs the
    # fiction to give it something to be about.
    from synthvdr.slots import SIZE_PRESETS, build_slot_manifest

    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    slots = build_slot_manifest(pack, SIZE_PRESETS["XS"])
    assert {s.section_dir for s in slots} == set(pack.section_dirs()), (
        "the premise of the warning below is that XS still allocates to every workstream"
    )
    assert "every workstream at every size" in _scope_body()


def test_every_step_cross_reference_in_every_skill_points_at_a_real_step():
    """A skill that renumbers its own steps and misses a back-reference sends the reader to
    a step that says something else, which is worse than no reference at all. Cheap to
    check, and it makes reordering a skill (see /vdr-scope, review item S1) a safe edit
    rather than a careful one.
    """
    step_heading = re.compile(r"(?m)^#{2,3} (\d+)\. ")
    single = re.compile(r"\bSteps? (\d+)\b")
    span = re.compile(r"\bSteps (\d+)[–-](\d+)\b")

    for name in SKILL_NAMES:
        path = ROOT / "skills" / name / "SKILL.md"
        body = _read(path)
        declared = {int(n) for n in step_heading.findall(body)}
        if not declared:
            continue
        referenced = {int(n) for n in single.findall(body)}
        for first, last in span.findall(body):
            referenced |= set(range(int(first), int(last) + 1))
        missing = sorted(referenced - declared)
        assert not missing, (
            f"{path}: refers to step(s) {missing} that no heading declares "
            f"(declared: {sorted(declared)})"
        )


# ---------------------------------------------------------------------------
# Review 2026-08-26, S2/S3/S4. Three instructions in /vdr-build that are only
# correct because of a fact stated somewhere else — the author agent's tool
# grant, gate 15's tri-state, and room.conf's key names. Each would go quietly
# wrong if that other fact changed, so each is pinned against it here rather
# than against a copy of it.
# ---------------------------------------------------------------------------


def test_vdr_author_neither_can_measure_words_nor_is_asked_to():
    """S2. The agent has no Bash, so it cannot run `wordcount()` and every depth figure it
    could report is a visual estimate. If it is ever granted Bash, the instruction below
    stops being true and this test should fail so someone revisits it.
    """
    body = _read(ROOT / "agents" / "vdr-author.md")
    tools = re.search(r"(?m)^tools:\s*(.+)$", body).group(1)
    assert "Bash" not in tools, (
        "vdr-author has been granted Bash — it can measure now, so revisit both its "
        "'Do not report word counts' instruction and /vdr-build's Step 3"
    )
    assert "Do not report word counts" in body
    assert "depth_problems" in body, (
        "the agent should name the function that measures for it, so the instruction is "
        "a division of labour rather than a bare prohibition"
    )


def test_build_skill_excepts_every_gate_that_cannot_pass_before_the_audit():
    """S3. Gate 15 fails on a finding whose `discoverable_from_blind` is None, and nothing
    sets that until vdr-auditor runs — which /vdr-build dispatches only after the last wave.
    So every earlier wave necessarily fails it, and the skill must say so; it used to name
    only gates 2, 7 and 8 and then assert that a FAIL on anything else was a real defect.
    """
    from synthvdr.schema import Finding

    unaudited = Finding(
        id="ENV-1", title="t", severity="high", workstream="environmental",
        multi_document=False, source="a.md", location="L", substance="S",
    )
    assert unaudited.discoverable_from_blind is None, (
        "the premise of gate 15's mid-build exception is that a fresh finding is unaudited"
    )

    body = _read(ROOT / "skills" / "vdr-build" / "SKILL.md")
    excepted = body[body.index("### 7. Run the gates") : body.index("### 8.")]
    assert "Gate 15" in excepted, "gate 15 is not in the named mid-build exception list"
    assert "three of the seventeen gates" in excepted, (
        "the exception list says how many gates it names; that count has drifted"
    )


def test_build_skill_names_real_room_conf_keys_for_the_author_invariants():
    """S4. The dispatch step tells you to hand every author four room-level invariants by
    value, naming the room.conf keys they come from. A renamed key would leave the
    instruction pointing at nothing.
    """
    from synthvdr.roomconf import REQUIRED_KEYS

    body = _read(ROOT / "skills" / "vdr-build" / "SKILL.md")
    # Scoped to the dispatch step: these keys appear elsewhere in the skill for other
    # reasons, so a whole-file search would pass even with the invariants paragraph gone.
    dispatch = body[body.index("### 2. Dispatch the authors") : body.index("### 3.")]
    for key in ("FLAG_STRING_1", "FLAG_STRING_2", "FINDING_PREFIXES"):
        assert key in REQUIRED_KEYS, f"{key} is no longer a required room.conf key"
        assert key in dispatch, f"the dispatch step no longer hands the author {key}"
    assert "_key/gaps.yaml" in dispatch and "## Invented names" in dispatch, (
        "the other two invariants — the gap allowlist and the closed name list — are gone"
    )


def test_findings_skill_quotes_the_severity_split_its_own_function_returns():
    """Review 2026-08-26, S5. The skill quotes two worked severity splits in prose. They come
    from `severity_targets`, so they can go stale the moment its tie-breaks change — the same
    drift that made the old "1 : 3 : 4 : 3" line unusable at XS in the first place.
    """
    from synthvdr.schema import severity_targets
    from synthvdr.slots import SIZE_PRESETS

    body = _read(ROOT / "skills" / "vdr-findings" / "SKILL.md")

    xs = severity_targets(SIZE_PRESETS["XS"].findings)
    assert len(set(xs.values())) == 1 and set(xs.values()) == {1}, (
        "the skill says XS comes back one per band"
    )
    assert "one per band" in body

    s_split = severity_targets(SIZE_PRESETS["S"].findings)
    quoted = " / ".join(str(s_split[k]) for k in ("critical", "high", "medium", "low"))
    assert quoted in body, (
        f"the skill quotes an S split that severity_targets no longer returns ({quoted})"
    )


# ---------------------------------------------------------------------------
# Review 2026-08-26, R1. `tools/check.sh` is copied into each room and execs a
# bare `python3 -m synthvdr.qa`. A room is a directory of its own, nowhere near
# whatever environment synthvdr was installed into, so on any machine where the
# system python is not that environment the harness died on a raw
# ModuleNotFoundError traceback — the first thing a new user meets, and it names
# neither the cause nor the fix.
# ---------------------------------------------------------------------------


def _run_check_sh(env_python, tmp_path):
    return subprocess.run(
        ["bash", str(ROOT / "tools" / "check.sh"), str(tmp_path)],
        capture_output=True,
        text=True,
        env={**os.environ, "SYNTHVDR_PYTHON": env_python},
    )


def test_check_sh_explains_itself_when_synthvdr_is_not_importable(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("bash not available in this environment")
    stub = tmp_path / "python-without-synthvdr"
    stub.write_text('#!/usr/bin/env bash\nexit 1\n')
    stub.chmod(0o755)

    result = _run_check_sh(str(stub), tmp_path)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "synthvdr" in combined and "pip install" in combined, (
        f"check.sh gave no actionable message: {combined!r}"
    )
    assert "SYNTHVDR_PYTHON" in combined, (
        "the message must name the override, or a user with a venv has no way through"
    )
    assert "Traceback" not in combined, "the raw traceback is what this replaces"


def test_check_sh_uses_the_interpreter_it_is_told_to(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("bash not available in this environment")
    result = _run_check_sh(sys.executable, tmp_path)
    # tmp_path is not a room, so the run fails on a missing room.conf — but it must
    # fail INSIDE synthvdr.qa, having imported it, not on the import itself.
    combined = result.stdout + result.stderr
    assert "pip install" not in combined, (
        f"check.sh did not use SYNTHVDR_PYTHON: {combined!r}"
    )


# ---------------------------------------------------------------------------
# The version-drift guard. This repo is its own marketplace — marketplace.json
# says `"source": "./"` and Claude Code has it registered as a git source on this
# remote — so master IS the published artefact and "published is behind master"
# cannot happen. The drift that does happen, and did: master moves while the
# version does not, so every install keeps serving what it already cached,
# because the cache is keyed on the version. That is the 26 August review's R1 in
# its true form.
# ---------------------------------------------------------------------------


def _throwaway_repo(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / "skills").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.0.0"}\n')
    (tmp_path / "skills" / "SKILL.md").write_text("original\n")
    (tmp_path / "README.md").write_text("original\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    return git


def _run_version_check(tmp_path, base):
    return subprocess.run(
        ["bash", str(ROOT / "tools" / "version-check.sh"), base],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )


def test_version_check_fails_when_the_surface_moves_and_the_version_does_not(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("bash not available in this environment")
    git = _throwaway_repo(tmp_path)
    (tmp_path / "skills" / "SKILL.md").write_text("changed\n")
    git("commit", "-qam", "change a skill, forget the bump")

    result = _run_version_check(tmp_path, "HEAD~1")

    assert result.returncode == 1, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "still 1.0.0" in combined
    assert "skills/SKILL.md" in combined, "the failure must name what changed"


def test_version_check_passes_when_the_version_moves_with_the_surface(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("bash not available in this environment")
    git = _throwaway_repo(tmp_path)
    (tmp_path / "skills" / "SKILL.md").write_text("changed\n")
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.1.0"}\n')
    git("commit", "-qam", "change a skill and bump")

    result = _run_version_check(tmp_path, "HEAD~1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1.0.0 -> 1.1.0" in result.stdout


def test_version_check_ignores_a_change_outside_the_plugin_surface(tmp_path):
    # A README or a review document is not something an install serves, so it must
    # not force a release nobody needs.
    if not shutil.which("bash"):
        pytest.skip("bash not available in this environment")
    git = _throwaway_repo(tmp_path)
    (tmp_path / "README.md").write_text("changed\n")
    git("commit", "-qam", "docs only")

    result = _run_version_check(tmp_path, "HEAD~1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing to bump" in result.stdout


# ---------------------------------------------------------------------------
# Task 7: /vdr-scope and /vdr-findings now use the section-subset machinery
# (DomainPack.subset, evidence_outside_sections). subset().workstreams() raises by
# design, so FINDING_PREFIXES and SECTION_DIRS must be drawn from opposite packs —
# getting that backwards is the one mistake this task must not make.
# ---------------------------------------------------------------------------


def test_scope_skill_uses_the_subset_pack_for_sections_and_the_full_pack_for_prefixes():
    # The one place both packs are in scope. Getting them the wrong way round
    # gives a room whose SECTION_DIRS lists sections it never builds, and a
    # FINDING_PREFIXES that mispairs every discovered finding.
    body = _read(ROOT / "skills" / "vdr-scope" / "SKILL.md")
    assert "room_pack.section_dirs()" in body, "SECTION_DIRS must come from the subset"
    # A bare substring check for "pack.workstreams()" is satisfied by
    # "room_pack.workstreams()" alone (it ends in "...pack.workstreams()"), so it
    # would pass even if the skill never once told the reader to call the correct,
    # full-pack form. Require the un-prefixed call to appear literally...
    assert re.search(r"(?<!room_)`pack\.workstreams\(\)`", body), (
        "FINDING_PREFIXES must come from the full pack via a bare `pack.workstreams()` call"
    )
    # ...and require every mention of the subset's (forbidden) form to sit in a
    # sentence that is warning the reader off it, never instructing them to use it.
    for line in body.splitlines():
        if "room_pack.workstreams()" in line:
            assert "never" in line or "raises" in line, (
                "room_pack.workstreams() must appear only as a prohibited example, "
                f"not an instruction to call it: {line!r}"
            )
    assert "build_slot_manifest(room_pack" in body, (
        "the manifest must be built from the subset, or the room builds every section"
    )
    assert "write_index_sources(slots, room_pack" in body, (
        "the index must describe the sections the room actually builds"
    )


def test_findings_skill_checks_evidence_against_the_declared_sections():
    body = _read(ROOT / "skills" / "vdr-findings" / "SKILL.md")
    assert "evidence_outside_sections" in body
    assert "SECTION_DIRS" in body


def test_scope_skill_names_the_core_sections_the_pack_marks():
    # Prose derived from sections.yaml, so it can go stale. Recompute it.
    pack = load_domain(DEFAULT_DOMAIN_ROOT)
    body = _read(ROOT / "skills" / "vdr-scope" / "SKILL.md")
    for section in pack.sections:
        if section.core:
            assert section.dir_name in body, (
                f"/vdr-scope does not tell the author {section.dir_name} is always present"
            )
