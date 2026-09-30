"""The Legora pack: its sources under legora/, and (Task 7) the pack
tools/build_legora_pack.py builds from them."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from synthvdr.legora_import import import_review
from synthvdr.roomconf import load_room_conf

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
