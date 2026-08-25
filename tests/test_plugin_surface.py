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

import json
import re
from pathlib import Path
from typing import List

import pytest
import yaml

import synthvdr
from synthvdr.qa.structural import SLOT_REF, parse_gaps_allowlist
from synthvdr.schema import (
    allocate_new_finding_ids,
    load_distractors,
    load_findings,
    render_findings_md,
    validate,
)

ROOT = Path(__file__).resolve().parent.parent
SKILL_NAMES = ("vdr-scope", "vdr-findings", "vdr-build", "vdr-qa", "vdr-package", "vdr-score")
AGENT_NAMES = ("vdr-author", "vdr-auditor")

# Arrive in Task 18 ("/vdr-build" + the two subagents) and Task 19 ("/vdr-qa",
# "/vdr-package", "/vdr-score"). Remove each name from here in the same commit that adds
# its skill/agent file — leaving it in place after the file exists is a hard failure
# (XPASS under strict=True), by design; see the module docstring.
PENDING_SKILLS = ("vdr-qa", "vdr-package", "vdr-score")
PENDING_AGENTS = ()

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
FENCED_YAML = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)
FENCED_MARKDOWN = re.compile(r"```markdown\n(.*?)\n```", re.DOTALL)


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


def markdown_examples(path: Path) -> List[str]:
    """Every fenced ` ```markdown ` code block's raw text in a skill/agent markdown file.

    The markdown-example counterpart to `yaml_examples()` above, for an artefact that is
    prose rather than YAML — `_key/build-status.md` here in Task 18. Same rule applies: a
    test checks this text as extracted from the shipped skill file, never a copy of the
    expected content kept in the test.
    """
    return FENCED_MARKDOWN.findall(_read(path))


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


def test_scope_skill_blocks_on_unresolved_name_collisions():
    body = _read(ROOT / "skills" / "vdr-scope" / "SKILL.md").lower()
    assert "gate a" in body
    assert "collision" in body


def test_findings_skill_states_the_hard_gate_before_authoring():
    body = _read(ROOT / "skills" / "vdr-findings" / "SKILL.md").lower()
    assert "gate b" in body
    assert "no authoring" in body or "before any authoring" in body


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


def test_incoming_example_in_build_skill_validates_as_findings(tmp_path):
    """The `_key/incoming/<label>.yaml` example `/vdr-build` tells a vdr-author subagent to
    copy — the answer-key refinement it writes alongside its documents — must itself load and
    validate through the real `synthvdr.schema` functions, the same discipline Task 17's fix
    round established for findings.yaml/distractors.yaml in `/vdr-findings`. It is exactly the
    `findings.yaml` shape (a subset of rows, upserted into the master registry), so it is
    checked the same way: read straight out of the shipped skill file, never a copy kept here.
    """
    path = ROOT / "skills" / "vdr-build" / "SKILL.md"
    incoming_yaml = find_example_by_top_level_key(yaml_examples(path), "findings", path)

    incoming_path = tmp_path / "incoming-example.yaml"
    incoming_path.write_text(incoming_yaml, encoding="utf-8")

    findings = load_findings(incoming_path)
    assert findings.findings, "the incoming.yaml example in the skill has no findings"

    errors = validate(findings, [])
    assert errors == [], f"the skill's own incoming.yaml example fails validate(): {errors}"


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
