"""Export a room's answer key for a Legora project of its own.

`explain-a-planted-finding` answers "what was planted here", "where is
CORP-1" and "was that a red herring" in Legora by quoting a room's key. The
project it runs in must hold the key and nothing of the blind room, or a
review run there could read the answers. This writes that project's whole
upload into one fresh folder, so what goes up is decided by the tool rather
than by remembering which folders to leave out:

- `findings.md`, rendered fresh from `findings.yaml` so it cannot be stale;
- `distractors.md`, rendered from `distractors.yaml`;
- `flagged/`, holding only the flagged documents that carry a finding's
  evidence or sit at a distractor's location or resolution. Those are the
  only documents a question can be about: the flagged tree's carriers are
  exactly the evidence paths, and every other flagged document is a
  byte-for-byte copy of its blind twin with nothing to explain. On a
  2,000-document room that is a couple of hundred files, not two thousand.

The folder must not exist, and must sit outside every tree of the room.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .roomconf import load_room_conf
from .schema import load_distractors, load_findings, render_distractors_md, render_findings_md


class ExportKeyError(Exception):
    """The key cannot be exported to that folder, or from this room."""


@dataclass(frozen=True)
class ExportReport:
    out: Path
    findings: int
    distractors: int
    flagged: int


def export_key(room: Path, out: Path) -> ExportReport:
    conf = load_room_conf(room / "room.conf")
    key_root = room / conf.get_relative_path("KEY_ROOT")
    flagged_root = room / conf.get_relative_path("FLAGGED_TREE")
    blind_root = room / conf.get_relative_path("BLIND_TREE")

    if out.exists():
        raise ExportKeyError(
            f"{out} already exists — export-key writes a fresh folder and never over one"
        )
    target = out.resolve()
    for key, tree in (("BLIND_TREE", blind_root), ("FLAGGED_TREE", flagged_root), ("KEY_ROOT", key_root)):
        resolved = tree.resolve()
        if target == resolved or resolved in target.parents:
            raise ExportKeyError(
                f"{out} is inside the room's {key} ({tree}) — the export must sit outside "
                "every tree of the room"
            )

    findings = load_findings(key_root / "findings.yaml")
    distractors_path = key_root / "distractors.yaml"
    distractors = load_distractors(distractors_path) if distractors_path.is_file() else []
    if not flagged_root.is_dir():
        raise ExportKeyError(
            f"no flagged tree at {flagged_root} — /vdr-build writes it, and export-key copies from it"
        )
    wanted = sorted(
        findings.all_evidence_paths()
        | {d.location for d in distractors}
        | {d.resolution for d in distractors}
    )
    missing = [rel for rel in wanted if not (flagged_root / rel).is_file()]
    if missing:
        raise ExportKeyError(
            f"the flagged tree lacks {len(missing)} document(s) the key names: "
            + ", ".join(missing)
            + " — rebuild it before exporting"
        )

    codename = conf.get("ROOM_CODENAME")
    out.mkdir(parents=True)
    (out / "findings.md").write_text(render_findings_md(findings, codename), encoding="utf-8")
    (out / "distractors.md").write_text(render_distractors_md(distractors, codename), encoding="utf-8")
    for rel in wanted:
        copy = out / "flagged" / rel
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(flagged_root / rel, copy)
    return ExportReport(
        out=out, findings=len(findings.findings), distractors=len(distractors), flagged=len(wanted)
    )
