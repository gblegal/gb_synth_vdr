"""The Legora pack: its sources under legora/, and (Task 7) the pack
tools/build_legora_pack.py builds from them."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

import synthvdr
from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.legora_import import import_review
from synthvdr.roomconf import load_room_conf
from synthvdr.slots import _subsection_name

ROOT = Path(__file__).resolve().parent.parent
LEGORA = ROOT / "legora"
REVIEW = LEGORA / "skills" / "review-a-test-data-room"
HAND_BACK = REVIEW / "references" / "2-what-to-hand-back.md"
EXAMPLE = re.compile(r"```markdown\n(.*?)\n```", re.DOTALL)
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)


def review_skill_texts(folder: Path) -> dict:
    return {
        p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(folder.rglob("*.md"))
    }


def _key_material_patterns():
    prefixes = load_room_conf(ROOT / "fixtures" / "xs-room" / "room.conf").get("FINDING_PREFIXES")
    patterns = [
        re.compile(rf"\b(?:{prefixes})-\d+\b"),
        re.compile(r"plant", re.IGNORECASE),
        re.compile(r"distractor", re.IGNORECASE),
        re.compile(r"red herring", re.IGNORECASE),
        re.compile(r"answer key", re.IGNORECASE),
        re.compile(r"_key\b"),
    ]
    archetypes = yaml.safe_load(
        (ROOT / "domain" / "ma" / "finding-archetypes.yaml").read_text(encoding="utf-8")
    )["finding_archetypes"]
    for items in archetypes.values():
        for archetype in items if isinstance(items, list) else []:
            patterns.append(re.compile(re.escape(archetype), re.IGNORECASE))
    return patterns


def assert_carries_no_key_material(texts: dict) -> None:
    # An empty set would pass without checking anything — a renamed or missing
    # skill folder must fail here, not sail through.
    assert texts, "no files to check — is the review skill where this test expects it?"
    patterns = _key_material_patterns()
    for name, text in texts.items():
        for pattern in patterns:
            assert not pattern.search(text), f"{name} carries {pattern.pattern!r}"


def test_the_worked_example_imports_to_exactly_the_issues_it_shows(xs_room):
    example = EXAMPLE.search(HAND_BACK.read_text(encoding="utf-8")).group(1)
    path = xs_room.parent / "example.md"
    path.write_text(example + "\n", encoding="utf-8")
    result = import_review(path, xs_room)
    shown = [(f["title"], f["severity"], f["documents"]) for f in result.output["findings"]]
    assert shown == [
        (
            "<One line naming the first issue>",
            "high",
            [
                "09_employment/9.2_policies/9.2.1_policies-01.md",
                "12_insurance/12.1_schedule/12.1.1_schedule-01.md",
            ],
        ),
        ("<One line naming the second issue>", "low", ["16_operations-quality/16.1_qms/16.1.1_qms-01.md"]),
    ]
    assert result.files_read == 3
    assert result.output["tool"].startswith("legora/model-not-stated")


def test_the_severity_rubric_defines_exactly_the_schemas_words_in_order():
    schema = json.loads((ROOT / "schemas" / "tool-output.schema.json").read_text(encoding="utf-8"))
    enum = schema["definitions"]["finding"]["properties"]["severity"]["enum"]
    section = HAND_BACK.read_text(encoding="utf-8").split("\n## Severity\n", 1)[1].split("\n## ", 1)[0]
    assert re.findall(r"^- \*\*(\w+)\*\*:", section, re.MULTILINE) == enum


def test_the_review_skill_carries_nothing_from_any_answer_key():
    assert_carries_no_key_material(review_skill_texts(REVIEW))


def test_the_review_skill_rules_out_reading_beyond_the_project():
    skill = (REVIEW / "SKILL.md").read_text(encoding="utf-8")
    assert "CUAD Database" in skill
    assert "not known" in skill
    assert "_review" in skill


def test_no_legora_source_carries_a_table():
    for path in sorted((LEGORA / "skills").rglob("*.md")) + [LEGORA / "README.md"]:
        assert not TABLE_ROW.search(path.read_text(encoding="utf-8")), f"{path} has a table"


BUILDER = ROOT / "tools" / "build_legora_pack.py"


def build(*args):
    return subprocess.run(
        [sys.executable, str(BUILDER), *map(str, args)], capture_output=True, text=True
    )


@pytest.fixture
def pack(tmp_path):
    out = tmp_path / "pack"
    result = build("--no-docx", "--out", out)
    assert result.returncode == 0, result.stdout + result.stderr
    return out


def test_the_pack_holds_each_skill_its_references_the_readme_and_a_zip_per_skill(pack):
    assert (pack / "README.md").read_text(encoding="utf-8") == (LEGORA / "README.md").read_text(encoding="utf-8")
    for skill in ("review-a-test-data-room", "explain-a-planted-finding"):
        assert (pack / "skills" / skill / "SKILL.md").is_file()
        assert (pack / "skills" / f"{skill}.zip").is_file()
    references = pack / "skills" / "review-a-test-data-room" / "references"
    assert sorted(p.name for p in references.iterdir()) == [
        "1-the-twenty-sections.md",
        "2-what-to-hand-back.md",
    ]
    with zipfile.ZipFile(pack / "skills" / "review-a-test-data-room.zip") as zipped:
        assert sorted(zipped.namelist()) == [
            "review-a-test-data-room/SKILL.md",
            "review-a-test-data-room/references/1-the-twenty-sections.md",
            "review-a-test-data-room/references/2-what-to-hand-back.md",
        ]
    assert not (pack / "skills" / "probe").exists()


def test_every_built_skill_names_its_build_under_its_heading(pack):
    line = f"Built from synth-vdr {synthvdr.__version__} · domain pack ma"
    for skill_md in sorted((pack / "skills").glob("*/SKILL.md")):
        lines = skill_md.read_text(encoding="utf-8").split("\n")
        heading = next(i for i, text in enumerate(lines) if text.startswith("# "))
        assert lines[heading + 1] == "" and lines[heading + 2] == line, skill_md


def test_the_sections_page_names_every_section_and_subfolder(pack):
    page = (
        pack / "skills" / "review-a-test-data-room" / "references" / "1-the-twenty-sections.md"
    ).read_text(encoding="utf-8")
    domain = load_domain(DEFAULT_DOMAIN_ROOT)
    assert len(domain.sections) == 20
    for section in domain.sections:
        assert f"`{section.dir_name}`" in page
        for index in range(len(section.subsections)):
            assert f"`{_subsection_name(section, index)}`" in page


def test_two_builds_of_one_commit_are_the_same_bytes(pack, tmp_path):
    again = tmp_path / "again"
    assert build("--no-docx", "--out", again).returncode == 0
    for zipped in sorted((pack / "skills").glob("*.zip")):
        assert zipped.read_bytes() == (again / "skills" / zipped.name).read_bytes(), zipped.name


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_word_copies_are_built_and_are_the_same_bytes_every_time(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    assert build("--out", first).returncode == 0
    assert build("--out", second).returncode == 0
    references = Path("skills") / "review-a-test-data-room" / "references"
    words = sorted((first / references).glob("*.docx"))
    assert [w.name for w in words] == ["1-the-twenty-sections.docx", "2-what-to-hand-back.docx"]
    for word in words:
        assert word.read_bytes() == (second / references / word.name).read_bytes(), word.name


def test_check_passes_on_a_fresh_build(pack):
    result = build("--check", "--out", pack)
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_fails_when_a_source_has_moved_on(tmp_path):
    sources = tmp_path / "legora"
    shutil.copytree(LEGORA, sources, ignore=shutil.ignore_patterns("probe"))
    out = tmp_path / "pack"
    assert build("--no-docx", "--sources", sources, "--out", out).returncode == 0
    skill = sources / "skills" / "explain-a-planted-finding" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nOne more rule.\n", encoding="utf-8")
    result = build("--check", "--sources", sources, "--out", out)
    assert result.returncode == 1
    assert "skills/explain-a-planted-finding/SKILL.md" in result.stdout


def test_check_fails_when_nothing_has_been_built(tmp_path):
    result = build("--check", "--out", tmp_path / "missing")
    assert result.returncode == 1
    assert "make legora" in result.stdout


def test_the_builder_will_not_delete_a_folder_it_did_not_build(tmp_path):
    out = tmp_path / "precious"
    out.mkdir()
    (out / "notes.txt").write_text("mine")
    result = build("--no-docx", "--out", out)
    assert result.returncode == 2
    assert result.stderr.startswith("build_legora_pack:"), result.stderr
    assert (out / "notes.txt").read_text() == "mine"


def test_the_built_review_skill_carries_nothing_from_any_answer_key(pack):
    assert_carries_no_key_material(review_skill_texts(pack / "skills" / "review-a-test-data-room"))


def test_nothing_in_the_built_pack_is_a_table(pack):
    for path in sorted(pack.rglob("*.md")):
        assert not TABLE_ROW.search(path.read_text(encoding="utf-8")), path
