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

import pytest
import yaml

import synthvdr

ROOT = Path(__file__).resolve().parent.parent
SKILL_NAMES = ("vdr-scope", "vdr-findings", "vdr-build", "vdr-qa", "vdr-package", "vdr-score")
AGENT_NAMES = ("vdr-author", "vdr-auditor")

# Arrive in Task 18 ("/vdr-build" + the two subagents) and Task 19 ("/vdr-qa",
# "/vdr-package", "/vdr-score"). Remove each name from here in the same commit that adds
# its skill/agent file — leaving it in place after the file exists is a hard failure
# (XPASS under strict=True), by design; see the module docstring.
PENDING_SKILLS = ("vdr-build", "vdr-qa", "vdr-package", "vdr-score")
PENDING_AGENTS = ("vdr-author", "vdr-auditor")

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


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


def test_no_skill_tells_the_reader_to_hand_over_key_material():
    for name in SKILL_NAMES:
        path = ROOT / "skills" / name / "SKILL.md"
        if not path.is_file():
            # Covered by test_every_skill_exists_with_matching_frontmatter; this test
            # checks a content property that only applies to a skill that exists, and a
            # missing file is not itself an instance of that property being violated.
            continue
        assert "hand over _key" not in _read(path).lower()
