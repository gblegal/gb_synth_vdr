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

## New findings

| Provisional id | Final id | Workstream |
|---|---|---|
| wave2-batch-a-NEW-1 | ENV-2 | environmental |
| wave2-batch-b-NEW-1 | OPS-1 | operations |

## Next wave

Wave 3, slots 91-140 (filler). Not yet started.

## Auditor

Not started — runs once authoring is complete.
```

"New findings" is a **cumulative ledger**, appended to across the whole build, not reset per
wave — it is both the permanent record the design spec requires a mid-authoring discovery to
be "declared in," and the durable idempotency check Step 3 reads before allocating anything.
It is written by Step 3 itself, **immediately** when a discovery is allocated — never deferred
to Step 7 — because Step 3 can succeed while Step 6's gate check afterwards fails, and a
resumed build must be able to tell "already allocated in a prior attempt" from "not yet seen"
without waiting for the wave to fully complete. An empty build (nothing discovered yet) omits
the section entirely rather than leaving it with no rows.

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

Consolidate `_key/incoming/*.yaml` into `_key/findings.yaml`. Each file carries two different
things, handled two different ways:

- **`findings:`** — refinements of finding IDs that already exist in the Gate-B registry.
  This is an **upsert**: the author's finalised `location` and `substance` wording (settled
  only once the real document exists to point at) overwrites those fields on the matching
  finding. A `findings:` row whose ID has no match in the master registry is a defect in the
  batch this wave was given, not a new finding — stop and fix the batch rather than silently
  adding it.
- **`new_findings:`** — findings an author genuinely discovered that were not in the Gate-B
  registry at all (design spec §5.1: "findings discovered during authoring are appended with
  the next free number in the owning workstream and declared in the wave manifest"). Every row
  here carries a **provisional ID** (`<label>-NEW-1`, scoped to the author's own label) instead
  of a real one — an author never assigns itself a real finding ID, because `/vdr-build` runs
  several authors in parallel with no channel between them, and two authors independently
  claiming "the next free ENV number" would collide silently on one ID for two distinct
  issues. Allocating the real ID is this consolidation step's job, done once, after the wave,
  over every author's discoveries together — never the author's.

The shape each `vdr-author` writes to `_key/incoming/<label>.yaml` (`<label>` is that
subagent's wave-and-batch identifier, e.g. `wave1-batch-a.yaml`) — copy and adapt, do not
reconstruct it from memory, since `findings:` is exactly `synthvdr.schema`'s `findings.yaml`
shape and `new_findings:` rows carry the same required fields, just with a provisional ID:

`source` and `corroboration` are relative to the blind tree root, never prefixed with
`BLIND_TREE`'s own name — see `/vdr-findings`' fuller note on this. An author who has just
written a real file knows its path *within* `BLIND_TREE`, not the tree's own name, so this
is usually automatic; the failure mode when it is not is `build_flagged_tree` (Step 5 below)
raising `TwinError` for the batch.

```yaml
findings:
  - id: FIN-2
    title: Deferred consideration escrow release condition unclear
    severity: medium
    workstream: financial
    multi_document: false
    source: 02_financial/2.1_statutory-accounts/2.1.3_escrow-notice.md
    location: "Clause 4.2"
    substance: >
      The escrow release notice references a completion accounts adjustment mechanism that
      is not itself present anywhere else in the room's financial section.
  - id: OPS-1
    title: Single-source supplier dependency undisclosed in main agreement summary
    severity: high
    workstream: operations
    multi_document: true
    source: 16_operations-quality/16.3_supply-chain/16.3.5_supplier-list.md
    location: "Row 3, annual spend column"
    corroboration:
      - 05_commercial/5.2_supplier-contracts/5.2.1_master-supply-agreement.md
    substance: >
      One supplier accounts for the majority of a key input's annual spend, and the master
      supply agreement contains no minimum-volume or exclusivity carve-out addressing that
      concentration.
new_findings:
  - id: wave2-batch-a-NEW-1
    title: Undisclosed related-party balance surfaced in the intercompany schedule
    severity: high
    workstream: environmental
    multi_document: false
    source: 11_environmental-hs/11.1_permits/11.1.2_variation-notice.md
    location: "Condition 7"
    substance: >
      A permit variation notice tightens a discharge limit the room's other environmental
      documents never mention meeting or missing — a genuinely new issue, not a restatement
      of an existing finding.
```

Merging and allocating is `synthvdr.schema.consolidate_wave_incoming` — a single, pure,
already-tested function, not something this script re-derives by hand. Two things it depends
on that are also shared functions, never reimplemented inline:

- `derive_prefix_for_workstream` builds the workstream -> finding-ID-prefix mapping
  `FINDING_PREFIXES` needs to be read as, and **validates the correspondence** rather than
  trusting a bare `zip()`. `FINDING_PREFIXES` carries no explicit workstream labels — the
  correspondence with `pack.workstreams()`'s order is positional — so a short list raises a
  clear error naming the mismatch, and a *reordered* list (same length, wrong pairing, which a
  bare zip would accept silently and use to misattribute a new finding's workstream) is caught
  by cross-checking every workstream that already has an existing finding against that
  finding's own id. Always pass `pack.workstreams()` here, never `pack.finding_archetypes`
  directly (a dict, whose key order happens to match today only because `load_domain` itself
  now checks that `sections.yaml` and `finding-archetypes.yaml` agree — see
  `synthvdr.domain.load_domain` — and refuses to load a domain pack where they do not; a
  hand-edited `finding-archetypes.yaml` that reorders two workstreams now fails at load time,
  before this script ever runs, rather than silently mispairing a discovery here).
- `parse_new_findings_ledger` reads `_key/build-status.md`'s "New findings" table into
  `{provisional_id: final_id}` — the durable record of what has already been allocated.
  **Consolidation is not something this build guarantees happens exactly once.** A wave can
  succeed at this step and then fail its gate at Step 6; resuming re-runs Step 3 over the
  same, untouched `_key/incoming/*.yaml` content. Without checking this ledger first, that
  rerun would allocate a *second*, higher id for the same discovery — a duplicate that still
  passes `validate()`, because nothing about a duplicate substance under two different ids is
  structurally invalid. A provisional id already in the ledger is **skipped, not
  re-allocated** — `consolidate_wave_incoming` does this internally given `already_mapped`.

```python
import yaml
from pathlib import Path
from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.roomconf import load_room_conf
from synthvdr.schema import (
    consolidate_wave_incoming,
    derive_prefix_for_workstream,
    load_findings,
    load_distractors,
    parse_new_findings_ledger,
    validate,
)

conf = load_room_conf(Path("room.conf"))
pack = load_domain(DEFAULT_DOMAIN_ROOT)
existing = load_findings(Path("_key/findings.yaml"))
prefix_for_workstream = derive_prefix_for_workstream(
    pack.workstreams(), conf.get("FINDING_PREFIXES").split("|"), existing.findings
)

status_path = Path("_key/build-status.md")
already_mapped = parse_new_findings_ledger(status_path.read_text()) if status_path.is_file() else {}

findings_doc = yaml.safe_load(Path("_key/findings.yaml").read_text()) or {"findings": []}
incoming_docs = {
    p.stem: (yaml.safe_load(p.read_text()) or {})
    for p in sorted(Path("_key/incoming").glob("*.yaml"))
}

result = consolidate_wave_incoming(findings_doc, incoming_docs, already_mapped, prefix_for_workstream)
Path("_key/findings.yaml").write_text(yaml.safe_dump(result.findings_doc, sort_keys=False))

# Persist the new mapping to the ledger IMMEDIATELY, before Step 6's gate even runs — this is
# what makes a rerun idempotent regardless of whether the gate afterwards passes or fails.
if result.new_mapping:
    status_text = status_path.read_text() if status_path.is_file() else "# Build status\n"
    if "## New findings" not in status_text:
        status_text += "\n## New findings\n\n| Provisional id | Final id | Workstream |\n|---|---|---|\n"
    rows = "\n".join(
        f"| {pid} | {fid} | {result.workstream_by_final_id[fid]} |"
        for pid, fid in result.new_mapping.items()
    )
    status_path.write_text(status_text + rows + "\n")

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
from synthvdr.schema import load_distractors, load_findings
from synthvdr.twin import build_flagged_tree
conf = load_room_conf(Path('room.conf'))
findings = load_findings(Path('_key/findings.yaml'))
distractors = load_distractors(Path('_key/distractors.yaml'))
print(build_flagged_tree(Path('.'), conf, findings, distractors))
"
```

Pass `distractors` here even though nothing in the flagged tree is ever annotated for one: this is the
only build-time check that a distractor's `location` and `resolution` were actually authored.
Nothing else in the harness opens either path — `synthvdr.score` only ever matches a *cited*
document's path against `distractor.location` by string equality, so a distractor pointed at a
document that was never written can never be flagged by scoring a tool's output; it can only be
caught here, at build time, or by gate 8's carrier census afterwards.

### 6. Run the gates

`bash tools/check.sh .`

### 7. Update the build status

Update `_key/build-status.md`: append this wave's number, the slots it authored, and the gate
result to the "Waves completed" table, then rewrite "Next wave" to name exactly one more than
the wave you just appended (see the literal shape above) — never leave the file pointing at a
wave number that has already run, and never skip a number. "New findings" is **not** touched
here — Step 3 already appended to it, unconditionally, before this wave's gate even ran.

Do not start the next wave while any gate is failing. A wave whose gate run failed is not
recorded in "Waves completed" at all; it stays the resume target until it passes.

## After the last wave

Dispatch `vdr-auditor` once per finding, or in batches. Hand each one the finding's **ID and
substance** (`f.by_id[finding_id].substance` — what the issue is, in the abstract) plus the
path to the blind room. Never hand over its `source`, `corroboration` or `location` — those
are exactly what the auditor must find independently — and never the flagged tree or
`_key/findings.yaml` itself. See `agents/vdr-auditor.md` for why: an auditor told where to
look is not measuring whether a real reviewer could find it, but an auditor told nothing about
the issue at all cannot look for anything either — the design spec's own phrase is "attempts
to reach each registered finding" (§3), which needs the finding named, not just numbered.

`vdr-auditor` has no write access (its frontmatter restricts it to `Read, Grep, Glob`) — it
**returns** its verdict rather than writing to the answer key itself. For each finding, take
the returned `reachable`/`not reachable` verdict and one-line `audit_note`, and write
`discoverable_from_blind` and `audit_note` into `_key/findings.yaml` for that finding
yourself.

Gate 15 fails until every finding is audited, because a planted finding nobody can find is a
corpus bug that no grep detects. Once every finding's `discoverable_from_blind`/`audit_note`
is written into `_key/findings.yaml`, re-run `bash tools/check.sh .` and record the result in
`_key/build-status.md`'s "Auditor" section.
