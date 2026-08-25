---
name: vdr-build
description: Author the data room in waves of subagents, consolidating answer-key refinements and running the QA gates after every wave. Resumable — re-running continues from the build status file rather than starting the room over.
---

# Build the room in waves

Requires `/vdr-findings` to have completed and Gate B to be closed: `_key/findings.yaml`,
`_key/distractors.yaml` and `_key/gaps.yaml` already name every finding, distractor and
declared gap this room will ever have. Nothing in this skill invents a new finding — it
authors the documents that make the already-signed-off registry true of the corpus.

## Ordering: findings-first, filler after

Author **every evidence-bearing document in the opening waves**, before a single benign
filler document is written. The corpus is then complete and valid at every checkpoint: a
40-document room and a 2,000-document room differ only in how much benign noise surrounds
the same verified answer key. An interrupted build never strands a finding half-planted —
at the point a build stops, either a finding's evidence is fully in the room or it has not
been started at all, never partially.

Concretely: sort the slot list by tier before batching, `A` (anchor — carries a finding, a
distractor, or is otherwise load-bearing) before `F` (filler), and exhaust every `A` slot
across as many waves as it takes before any `F` slot is assigned to a wave.

## Resume

Read `_key/build-status.md` first, before doing anything else. It lists the waves already
completed, the slots each one authored, and the next wave to run. Re-running this skill
**continues from there** — it never restarts a finished wave, and it never re-authors a slot
that a completed wave already recorded. If the file does not exist yet, this is wave 1 of a
fresh build.

The literal shape to read and to write back (copy and adapt — the "Next wave" line is the
resume pointer everything else in this skill trusts, so keep the arithmetic exact: it always
names exactly one more than the highest wave recorded above it, and no wave is ever added to
this table until its own gate run above (Step 6) has come back all-PASS):

```markdown
# Build status — Project Ashfell

## Waves completed

| Wave | Slots authored | Gate result |
|---|---|---|
| 1 | 45 | PASS |
| 2 | 45 | PASS |

## Next wave

Wave 3, slots 91-140 (filler). Not yet started.

## Auditor

Not started — runs once authoring is complete.
```

A wave is only added to the "Waves completed" table once its gate run is clean; a wave that
failed its gate stays the "Next wave" entry, re-run rather than duplicated, until it passes.

## Per wave

### 1. Select the next batch

At most **5 subagents**, roughly 40–50 slots each, drawn from `_key/anchors.csv` (`slot_id`,
`tier`, `rel_path`) in tier order per the ordering rule above. Cross-reference
`_key/findings.yaml` and `_key/distractors.yaml` for which slots in this batch carry a
finding's `source`/`corroboration` or a distractor's `location`/`resolution` — those are this
wave's registry rows.

### 2. Dispatch the authors

Dispatch `vdr-author` subagents in parallel, one per batch. Give each one: its slot list with
tier and finding/distractor class, the relevant `_key/index-src/` sections, the fact-sheet
extracts it needs from `_key/fact-sheet.md`, and only the registry rows for findings/
distractors whose evidence falls inside its own batch — not the whole `_key/findings.yaml`,
and never the flagged tree or its path. A subagent that cannot see a finding outside its own
batch cannot leak it into the wrong document by accident, and it has no route to the flagged
tree to write to even if it wanted to; see `agents/vdr-author.md` for why that separation is
load-bearing, not just tidy scoping.

### 3. Consolidate the answer-key refinements

Consolidate `_key/incoming/*.yaml` into `_key/findings.yaml`. Every finding ID inside an
incoming file must already exist in the Gate-B registry — this step **upserts** the author's
finalised `location` and `substance` wording (settled only once the real document exists to
point at) onto the matching finding; it never introduces a finding after Gate B has closed. A
finding ID with no match in the master registry is a defect in the batch this wave was given,
not a new finding — stop and fix the batch rather than silently adding it.

The shape each `vdr-author` writes to `_key/incoming/<label>.yaml` (`<label>` is that
subagent's wave-and-batch identifier, e.g. `wave1-batch-a.yaml`) — copy and adapt, do not
reconstruct it from memory, since it is exactly `synthvdr.schema`'s `findings.yaml` shape and
is loaded and validated the same way:

```yaml
findings:
  - id: FIN-2
    title: Deferred consideration escrow release condition unclear
    severity: medium
    workstream: financial
    multi_document: false
    source: data-room/02_financial/2.6_completion-accounts/2.6.3_escrow-notice.md
    location: "Clause 4.2"
    substance: >
      The escrow release notice references a completion accounts adjustment mechanism that
      is not itself present anywhere else in the room's financial section.
  - id: OPS-1
    title: Single-source supplier dependency undisclosed in main agreement summary
    severity: high
    workstream: operations
    multi_document: true
    source: data-room/14_operations/14.2_supply-chain/14.2.5_supplier-list.md
    location: "Row 3, annual spend column"
    corroboration:
      - data-room/06_commercial/6.3_supplier-contracts/6.3.1_master-supply-agreement.md
    substance: >
      One supplier accounts for the majority of a key input's annual spend, and the master
      supply agreement contains no minimum-volume or exclusivity carve-out addressing that
      concentration.
```

Consolidate with an upsert-by-id merge, then re-run `synthvdr.schema.validate` over the
merged result before writing it back:

```python
import yaml
from pathlib import Path
from synthvdr.schema import load_findings, load_distractors, validate

master = yaml.safe_load(Path("_key/findings.yaml").read_text()) or {"findings": []}
by_id = {row["id"]: row for row in master["findings"]}
for incoming_path in sorted(Path("_key/incoming").glob("*.yaml")):
    incoming = yaml.safe_load(incoming_path.read_text()) or {}
    for row in incoming.get("findings") or []:
        if row["id"] not in by_id:
            raise SystemExit(f"{incoming_path}: {row['id']} is not in the Gate B registry")
        by_id[row["id"]].update(row)
master["findings"] = list(by_id.values())
Path("_key/findings.yaml").write_text(yaml.safe_dump(master, sort_keys=False))

f = load_findings(Path("_key/findings.yaml"))
d = load_distractors(Path("_key/distractors.yaml"))
errors = validate(f, d)
assert not errors, errors
```

### 4. Reconcile new canonical facts

Reconcile any new canonical fact a subagent's manifest reported into the fact sheet's
`## Canonical figures` table. New facts go in the fact sheet **first**; grep the room before
introducing a value gate 13 has not seen yet.

### 5. Rebuild the flagged tree

This is the *only* writer of the flagged tree in the whole plugin; nothing a subagent
produced touches it directly:

```bash
python3 -c "
from pathlib import Path
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import load_findings
from synthvdr.twin import build_flagged_tree
conf = load_room_conf(Path('room.conf'))
print(build_flagged_tree(Path('.'), conf, load_findings(Path('_key/findings.yaml'))))
"
```

### 6. Run the gates

`bash tools/check.sh .`

### 7. Update the build status

Update `_key/build-status.md`: append this wave's number, the slots it authored, and the gate
result to the "Waves completed" table, then rewrite "Next wave" to name exactly one more than
the wave you just appended (see the literal shape above) — never leave the file pointing at a
wave number that has already run, and never skip a number.

Do not start the next wave while any gate is failing. A wave whose gate run failed is not
recorded in "Waves completed" at all; it stays the resume target until it passes.

## After the last wave

Dispatch `vdr-auditor` once per finding, or in batches. Hand each one **only** the finding's
ID and the path to the blind room — never `_key/`, never the flagged tree, never the
finding's substance or evidence paths. See `agents/vdr-auditor.md` for why: an auditor told
where to look is not measuring whether a real reviewer could find it.

Gate 15 fails until every finding is audited, because a planted finding nobody can find is a
corpus bug that no grep detects. Once every finding's `discoverable_from_blind`/`audit_note`
is written back to `_key/findings.yaml`, re-run `bash tools/check.sh .` and record the result
in `_key/build-status.md`'s "Auditor" section.
