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

Concretely, and this is a function rather than a rule to apply by hand:

```python
from pathlib import Path
from synthvdr.schema import load_bearing_paths, load_distractors, load_findings
from synthvdr.slots import authoring_order, read_slot_manifest

findings = load_findings(Path("_key/findings.yaml"))
distractors = load_distractors(Path("_key/distractors.yaml"))

# The canonical home of a fact-sheet figure is load-bearing too — see below. Nothing
# declares these, so name them yourself, as rel_paths.
figure_homes = {
    "18_transaction/18.2_spa/18.2.1_spa-01.md",   # enterprise value, earn-out
}
load_bearing = load_bearing_paths(findings, distractors) | figure_homes

order = authoring_order(read_slot_manifest(Path("_key/anchors.csv")), load_bearing)
print(sum(1 for slot in order if slot.rel_path in load_bearing), "load-bearing slots first")
```

`authoring_order` puts every slot the answer key depends on first — a finding's `source` and
`corroboration`, both ends of every distractor — then orders the rest by tier, and keeps
manifest order inside each group. Exhaust the whole load-bearing block, across as many waves
as it takes, before any other slot is assigned to a wave.

**Add the canonical home of every fact-sheet figure to that set.** Gate 13 greps each value in
`## Canonical figures` into the room, so a figure whose only natural home is a slot not yet
authored fails the gate through no defect — and gate 13 is not excepted mid-build, correctly,
because it is what stops a room drifting from its own fact sheet. In the XS build that
surfaced this, `£48.0m` (enterprise value) and `£9.0m` (earn-out) belong in the draft SPA at
`18.2.1`, a tier-`F` slot the ordering rule would otherwise leave to the last wave; it had to
be pulled forward by hand to make gate 13 answerable at all. The answer key cannot tell you
which slots these are — `## Canonical figures` records a key and a value, not a home — so read
the table and name them, once, when you compose the set above.

**Do not sort by tier instead.** This skill used to say to, and glossed tier `A` as "anchor —
carries a finding, a distractor, or is otherwise load-bearing". Tier is nothing of the sort:
`/vdr-scope` assigns it positionally, before the findings registry exists, so the first ~35%
of each section's slots are `A` whatever they end up carrying. In the shipped `xs-room`
fixture 4 of the 10 load-bearing paths are tier `F`; in the XS build that surfaced this it was
6 of 10, including both distractor documents. Tier is a depth and prominence tier — it is what
gate 10's floors are keyed off — and `authoring_order` still uses it, as the second key.

## Resume

Read `_key/build-status.md` first, before doing anything else. It lists the waves already
completed, the slots each one authored, and the next wave to run. Re-running this skill
**continues from there** — it never restarts a finished wave, and it never re-authors a slot
that a completed wave already recorded. If the file does not exist yet, this is wave 1 of a
fresh build.

The literal shape to read and to write back (copy and adapt — the "Next wave" line is the
resume pointer everything else in this skill trusts, so keep the arithmetic exact: it always
names exactly one more than the highest wave recorded above it, and no wave is ever added to
this table until its own gate run above (Step 7) has come back all-PASS):

```markdown
# Build status — Project Ashfell

## Waves completed

| Wave | Slots authored | Gate result |
|---|---|---|
| 1 | 45 | PASS |
| 2 | 45 | PASS |

## Anchors

Complete as of wave 2 — every finding's and distractor's evidence path has been authored.
Gate 8 is a real check from wave 3 onward; gate 2 stays excepted until the LAST wave (it
checks the room's finished size, fixed since `/vdr-scope`, not what has been authored so far).

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
be "declared in," and the durable idempotency check Step 4 reads before allocating anything.
It is written by Step 4 itself, **immediately** when a discovery is allocated — never deferred
to Step 8 — because Step 4 can succeed while Step 7's gate check afterwards fails, and a
resumed build must be able to tell "already allocated in a prior attempt" from "not yet seen"
without waiting for the wave to fully complete. An empty build (nothing discovered yet) omits
the section entirely rather than leaving it with no rows.

"Anchors" is written **once**, the wave Step 1 first has to reach past the load-bearing block
(see Step 1) — never rewritten afterwards. Absent before that point (a fresh build, or one still mid-anchor). This
is the fact Steps 6–8 read to know whether gates 2/7/8/15's mid-build exceptions still apply.

"Gate result" in "Waves completed" records **PASS once every gate outside Step 7's named
mid-build exceptions is clean** — not "every one of the eighteen gates," which (see Step 7)
no wave before the last one can ever produce. Wave 1 and wave 2 above both legitimately show
PASS with gates 2 and 15 excepted throughout, and gates 7/8 additionally excepted before
"Anchors" is recorded; that is not a weaker PASS, it is what "clean" is defined to mean before the room
reaches its final size.

A wave is only added to the "Waves completed" table once its gate run is clean **against the
gates that are not currently excepted** (see Step 7) — that is what the "(... excepted)" note
in the table above records, so a resumed build (or a human reading the file) can tell a
genuinely clean wave from one that was accepted with a named, expected gap.

## Per wave

### 1. Select the next batch

At most **5 subagents**, roughly 40–50 slots each, taken off the front of the
`authoring_order` list the ordering rule above computes. Cross-reference `_key/findings.yaml`
and `_key/distractors.yaml` for which slots in this batch carry a finding's
`source`/`corroboration` or a distractor's `location`/`resolution` — those are this wave's
registry rows, and by construction they are the slots at the front of that list.

**Note the exact wave this batch selection first has to reach past the load-bearing block,
because no load-bearing slot is left.** That is "anchors complete" — see Steps 6–8 below,
which behave differently before and after it. Note what this now means, because the old
tier-based reading got it wrong in a way that mattered: anchors-complete is the point every
document the answer key depends on has been authored, so it is exactly the point the flagged
tree becomes worth building and gate 8's carrier census becomes a real check. Under the tier
rule it fired while findings' evidence was still unwritten. For an `M`-size room (200 documents) this is usually wave 1
itself, since a wave's capacity (up to 250 slots) already exceeds the whole room; for `L`/`XL`
it can take several waves. Record it in `_key/build-status.md`'s `## Anchors` line the moment
it happens (see the literal shape in "Resume" above) — this is the one fact Steps 6–8 need
that nothing else in the file states directly.

### 2. Dispatch the authors

Dispatch `vdr-author` subagents in parallel, one per batch. Give each one: its slot list with
tier and finding/distractor class, the relevant `_key/index-src/` sections, the fact-sheet
extracts it needs from `_key/fact-sheet.md`, and only the registry rows for findings/
distractors whose evidence falls inside its own batch — not the whole `_key/findings.yaml`,
and never the flagged tree or its path. A subagent that cannot see a finding outside its own
batch cannot leak it into the wrong document by accident, and it has no route to the flagged
tree to write to even if it wanted to; see `agents/vdr-author.md` for why that separation is
load-bearing, not just tidy scoping.

**Also give every author the four room-level invariants, by value.** These are not per-batch,
so it is easy to leave them out — and each one is a gate the wave fails without them.
`agents/vdr-author.md` tells the author to respect the first two, but only in the abstract
("the annotation strings configured in `room.conf`"); the author has no reason to open
`room.conf` and no way to know the values unless you say them.

| Invariant | Where it lives | The wave fails on |
|---|---|---|
| The two flag strings | `room.conf` `FLAG_STRING_1`/`FLAG_STRING_2` | gate 3, annotation-string leakage |
| The finding-ID prefix alphabet | `room.conf` `FINDING_PREFIXES` | gate 4, blind-tree vocabulary |
| The declared gap refs | `_key/gaps.yaml` | gate 9, dangling cross-reference |
| The closed name list | the fact sheet's `## Cast` and `## Invented names` | gate 14, unchecked name |

Compose the paragraph once and paste it verbatim into every author's prompt:

```python
from pathlib import Path
from synthvdr.names import cast_list
from synthvdr.qa.structural import parse_gaps_allowlist
from synthvdr.roomconf import load_room_conf

conf = load_room_conf(Path("room.conf"))
gaps = sorted(parse_gaps_allowlist(Path("_key/gaps.yaml").read_text(encoding="utf-8")))
names = sorted(cast_list(Path("_key/name-check.md"), kind=None))

print(f'''Room invariants, all four of which a gate checks:
- Never write these two strings into any document: {conf.get("FLAG_STRING_1")!r},
  {conf.get("FLAG_STRING_2")!r}.
- Never use a token shaped like a finding ID as an in-room reference. The prefixes in
  play are: {conf.get("FINDING_PREFIXES")}.
- The only slot references that may be left unresolved are: {", ".join(gaps) or "none"}.
  Every other reference you write must point at a slot the index declares.
- Every entity, brand, product, site or domain name you use must already be one of
  these: {", ".join(names)}. Invent no others.''')
```

`cast_list(..., kind=None)` is deliberate here: gate 14 masks with the entity rows only, but
an author needs the whole closed list, people included, because it must not invent a person
either.

### 3. Measure the batch against its depth floors

**Before consolidating anything**, run gate 10's own check over just the slots this wave
authored, and re-dispatch whatever came up short:

```python
from pathlib import Path
from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.qa.depth import depth_problems
from synthvdr.roomconf import load_room_conf
from synthvdr.slots import read_anchors_csv

conf = load_room_conf(Path("room.conf"))

problems = depth_problems(
    sorted(Path(conf.get("BLIND_TREE")).rglob("*.md")),
    read_anchors_csv(Path("_key/anchors.csv")),
    load_domain(DEFAULT_DOMAIN_ROOT),
    conf.get("FLAG_STRING_1"),
)
for problem in problems:
    print(problem)
```

This sweeps every document authored so far, not just this wave's. That is deliberate and
costs nothing: every earlier wave already cleared this check before it was recorded, so
anything printed here belongs to the batch that just came back. Taking the whole tree keeps
the snippet self-contained — there is no wave-membership list to thread through it, and no
way to get that list subtly wrong and measure the wrong documents.

Re-dispatch a `vdr-author` for every slot named, telling it the measured count and the floor,
then measure again. Only move on when this prints nothing.

**Do not skip this because the authors reported their word counts.** They cannot have
measured them. `agents/vdr-author.md` grants `Read, Write, Edit, Grep, Glob` and no Bash, so
an author has no way to run `wordcount()` and every figure it reports is a visual estimate —
and in the build that surfaced this, every estimate was HIGH:

| Slot | Author's figure | Measured | Floor |
|---|---|---|---|
| 16.1.1 | ~1,450 | 1,190 | 1,200 |
| 6.1.1 | ~1,350 | 1,103 | 1,200 |
| 10.1.1 | ~1,300 | 1,050 | 1,200 |
| 17.1.1 | ~3,050 | 2,447 | 2,500 |

Seven of 40 documents landed under floor and cost a whole remediation wave with two extra
subagents. Catching it here costs one re-dispatch inside the wave that caused it.

`depth_problems` is the function `gate_10_depth` itself calls, not a reimplementation of it,
so a wave that clears this check clears gate 10 in Step 7 for the same reason. Note it also
reports placeholder tokens and slots missing from `anchors.csv` — both are real defects in
what the author returned, and both are fixed the same way, by re-dispatching.

### 4. Consolidate the answer-key refinements

Consolidate `_key/incoming/*.yaml` into `_key/findings.yaml`. Each file carries two different
things, handled two different ways:

- **`findings:`** — refinements of finding IDs that already exist in the Gate-B registry.
  This is a **narrow upsert**: `location` and `substance` — and only those two — are the
  author's to settle, once the real document exists to point at, and they overwrite those
  fields on the matching finding. Every other field on the row was fixed when the user signed
  the registry off at Gate B; consolidation compares each one against the registry and
  **raises** if it differs, rather than taking the author's version. An author echoing its
  brief back unchanged is fine and expected — an author that returns a rewritten `workstream`,
  `title` or `corroboration` has misunderstood its brief, and you want to see that rather than
  ship a registry that `validate()` will pass clean. A `findings:` row whose ID has no match in
  the master registry is a defect in the batch this wave was given, not a new finding — stop
  and fix the batch rather than silently adding it.
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
reconstruct it from memory. The two keys have deliberately different shapes: a `findings:`
row carries **`id`, `location` and `substance` only**, because that is all the author owns;
a `new_findings:` row carries the full `findings.yaml` shape, because nothing about it exists
in the registry yet, just with a provisional ID in place of a real one.

A `new_findings:` row's `source` and `corroboration` are relative to the blind tree root,
never prefixed with `BLIND_TREE`'s own name — see `/vdr-findings`' fuller note on this. An author who has just
written a real file knows its path *within* `BLIND_TREE`, not the tree's own name, so this
is usually automatic; the failure mode when it is not is `build_flagged_tree` (Step 6 below)
raising `TwinError` for the batch.

```yaml
# Three keys per row, no more. `title`, `severity`, `workstream`, `multi_document`,
# `source` and `corroboration` are the registry's, not the author's — sending one that
# differs from the registry raises rather than overwrites it.
findings:
  - id: FIN-2
    location: "Clause 4.2"
    substance: >
      The escrow release notice references a completion accounts adjustment mechanism that
      is not itself present anywhere else in the room's financial section.
  - id: OPS-1
    location: "Row 3, annual spend column"
    substance: >
      One supplier accounts for the majority of a key input's annual spend, and the master
      supply agreement contains no minimum-volume or exclusivity carve-out addressing that
      concentration.
# A discovery has no registry row to defer to, so this one carries everything.
new_findings:
  - id: wave2-batch-a-NEW-1
    title: Undisclosed related-party balance surfaced in the intercompany schedule
    severity: high
    workstream: environmental
    multi_document: false
    source: 11_environmental-hs/11.1_permits/11.1.2_permits-02.md
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
  succeed at this step and then fail its gate at Step 7; resuming re-runs Step 4 over the
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
    render_findings_md,
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

# Note the glob takes EVERY incoming file, including every prior wave's. That is deliberate,
# not an oversight: consumed intake is never moved or deleted, so wave n re-reads waves 1..n-1
# as well. It is safe for two distinct reasons, and both are load-bearing —
#   * a `findings:` row is now a narrow, idempotent upsert of `location`/`substance`, so
#     re-applying an already-applied refinement is a no-op; and
#   * a `new_findings:` row is guarded by `already_mapped`, read from the build-status ledger
#     above, so a discovery allocated in an earlier wave is skipped rather than re-allocated.
# Deleting either guard turns this line into duplicate findings on every wave. The cost is
# O(waves^2) reads — noticeable on an XL room at twenty-odd waves, and the reason to archive
# consumed files under `_key/incoming/consumed/` if that ever matters. Correctness does not
# depend on archiving them; it depends on the two guards above.

# Persist the new mapping to the ledger IMMEDIATELY, before Step 7's gate even runs — this is
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

# _key/findings.md is GENERATED from findings.yaml (see ARCHITECTURE.md §7) — regenerate it
# every time this step touches findings.yaml, not just at Gate B. Without this, a wave that
# discovers ENV-2/OPS-1 updates the YAML but leaves findings.md showing only the Gate-B
# registry, and anyone reading findings.md (rather than the YAML) sees a stale, incomplete
# answer key with nothing to say so.
Path("_key/findings.md").write_text(render_findings_md(f, conf.get("ROOM_CODENAME")))
```

### 5. Reconcile new canonical facts

Reconcile any new canonical fact a subagent's manifest reported into the fact sheet's
`## Canonical figures` table. New facts go in the fact sheet **first**; grep the room before
introducing a value gate 13 has not seen yet.

### 6. Rebuild the flagged tree — only once Anchors is complete

**Skip this step entirely for every wave before the one you recorded in `_key/build-status.md`'s
`## Anchors` line (Step 1).** `build_flagged_tree` requires every finding's and every
distractor's evidence path to already exist under `BLIND_TREE` and raises `TwinError` naming
whichever ones do not — correct behaviour once the room is meant to be complete, but a wave
that has not yet cleared the load-bearing block has not yet planted every finding's evidence
by definition, so calling this before "Anchors" is recorded always raises. That "by
definition" is only true because Step 1 orders by `authoring_order`: under the tier rule this
skill used to give, "Anchors" could be recorded with several findings' evidence still
unwritten, and this step then raised `TwinError` for the rest of the build. This is not a corpus
bug at that stage; it is simply too early to build the tree at all. Gates 7 and 8 (Step 7)
SKIP as a direct consequence — see there.

From the wave "Anchors" names onward, this is the *only* writer of the flagged tree in the
whole plugin; nothing a subagent produced touches it directly:

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

### 7. Run the gates — with named, mid-build exceptions

`bash tools/check.sh .`

**Multi-wave is the normal case** (`M` = 200 documents, `L` = 800, `XL` = 2,000+): most builds
take several waves, and three of the eighteen gates check something that genuinely does not
exist yet before the room is finished. Naming all three exactly, rather than leaving "do not
proceed on any failure" as a rule the room's own size makes impossible to satisfy:

- **Gate 2 (tree counts)** compares the blind/flagged tree's actual document count against
  `BLIND_TOTAL`/`FLAGGED_TOTAL` — the room's *finished* size, fixed by `/vdr-scope` before a
  single document existed. It is **expected to FAIL on every wave except the last one**, and
  clearing on the last wave (not before) is exactly what tells you the room is done.
- **Gate 15 (discoverability audit)** fails on any finding whose `discoverable_from_blind`
  is still unset, and nothing sets it until `vdr-auditor` runs — which this skill dispatches
  only "After the last wave" (see below). So it is **expected to FAIL on every wave until the
  audit runs**, exactly like gate 2, and clearing is what tells you the audit is done. An
  unaudited finding is not presumed reachable, which is correct: the gate is refusing to
  vouch for a blind room nobody has read.
- **Gates 7 (twin diff) and 8 (carrier census)** both require the flagged tree to exist. Before
  the wave "Anchors" names, Step 6 above is skipped on purpose, so the flagged tree does not
  exist yet and both gates **SKIP** — this is not the "we forgot to build something" SKIP
  `--strict` should ever convert to a failure at release, it is the expected shape of every
  wave before Anchors. From the Anchors wave onward, Step 6 runs every wave, the flagged tree
  exists, and gate 8 is a real, un-excepted check: a FAIL from that point on is a genuine
  corpus defect (a wrongly authored evidence path, a stripped block), never an artefact of
  build order.

**Every gate other than these three is a real check on every wave, including the first**, and
a FAIL on any of them is always a real defect — never wave the whole gate run through because
"gate 2 usually fails mid-build" without checking which gate actually failed.

### 8. Update the build status

Update `_key/build-status.md`: append this wave's number, the slots it authored, and the gate
result to the "Waves completed" table, then rewrite "Next wave" to name exactly one more than
the wave you just appended (see the literal shape above) — never leave the file pointing at a
wave number that has already run, and never skip a number. "New findings" is **not** touched
here — Step 4 already appended to it, unconditionally, before this wave's gate even ran. If
this wave is the one Step 1 identified as anchors-complete and `## Anchors` is not yet
recorded, write it now.

**Do not start the next wave while any gate is failing OUTSIDE Step 7's named exceptions.** A
wave whose gate run failed on anything else is not recorded in "Waves completed" at all; it
stays the resume target until it passes. A wave that fails ONLY on the excepted gates (gates
2 and 15 always; gates 7/8 before Anchors) is recorded as PASS — see the "Gate result" note above the
literal example for what PASS means at that point in a build.

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
