---
name: vdr-findings
description: Design the findings registry, distractors and evidence-chain map for a scoped data room — the eval specification, fixed before a single document is authored. This is a hard gate; no authoring proceeds until the registry is signed off.
---

# Design the findings registry

This is the eval specification for the whole room. **Before any authoring** happens — not
after a first draft, not "roughly" before — the registry, the distractors and the declared
gaps are fixed and signed off. This is a hard gate. Requires `/vdr-scope` to have completed
and Gate A to be closed; read `_key/fact-sheet.md` and `room.conf` before starting.

## 1. Budget

Find the size preset the room was scoped at by matching `room.conf`'s `INDEX_TOTAL` against
`synthvdr.slots.SIZE_PRESETS` (there is no separate "preset name" stored anywhere — the
document count is the record of which preset was used):

```python
from synthvdr.roomconf import load_room_conf
from synthvdr.slots import SIZE_PRESETS
from pathlib import Path

conf = load_room_conf(Path("room.conf"))
total = conf.get_int("INDEX_TOTAL")
preset = next(p for p in SIZE_PRESETS.values() if p.docs == total)
print(preset.findings, preset.distractors)
```

Target `preset.findings` findings and `preset.distractors` distractors: XS 4/2, S 12/5,
M 25/10, L 60/18, XL 90/25. Aim for roughly a 1 : 3 : 4 : 3 split across
critical / high / medium / low severity, with **at least half** of all findings
multi-document.

## 2. Draft the registry

Seed ideas from `domain/ma/finding-archetypes.yaml` — one list of archetypal issues per
workstream — but do not copy an archetype's wording verbatim into a finding. Every finding
must bind to the specific facts already declared in `_key/fact-sheet.md` (the entity tree,
the cast, the canonical figures, the dates): a finding that invents its own unstated fact
is a fact the rest of the room can never reconcile to, which is exactly what gate 13 exists
to catch.

Write `_key/findings.yaml` (schema: `synthvdr.schema.Finding`, loaded by
`synthvdr.schema.load_findings`). A description of the fields is not enough to write this
file correctly — copy the shape below and adapt it, do not reconstruct it from memory:

```yaml
schema_version: 1
room: "Project Example"
findings:
  - id: ENV-1
    title: Site contamination under-provisioned
    severity: critical
    workstream: environmental
    multi_document: true
    source: 11_environmental-hs/11.2_site-reports/11.2.4_phase-2.md
    location: "Table 4, remediation estimate"
    corroboration:
      - 02_financial/2.4_provisions/2.4.2_environmental-provision.md
    substance: >
      Phase 2 estimates a remediation cost far above the balance-sheet provision, and the
      indemnity that would have covered the gap expired before signing.
  - id: FIN-1
    title: Provision materially below the underlying estimate
    severity: high
    workstream: financial
    multi_document: false
    source: 02_financial/2.4_provisions/2.4.1_provisions-note.md
    location: "Note 14"
    substance: >
      The disclosed provision does not reflect the remediation estimate carried elsewhere in
      the room.
    cross_links: [ENV-1]
```

`id`, `title`, `severity`, `workstream`, `source` and `substance` are required —
`load_findings` raises `SchemaError` naming the missing field if any is absent. `severity`
must be one of `critical`/`high`/`medium`/`low`. `location` is free text describing where in
`source` to look. `corroboration` is the rest of a multi-document finding's trail — required
non-empty when `multi_document: true`, and must be empty/absent when it is `false` (both
directions are enforced by `synthvdr.schema.validate`, not just documented here). `cross_links`
names other finding IDs this one relates to, never a second ID for the same issue — see the
first rule below. `id` is prefixed by the finding's workstream (e.g. `ENV-1`), matching a
token you declared in `room.conf`'s `FINDING_PREFIXES` at scope time.

**`source` and every `corroboration` entry are paths relative to the blind tree root —
never prefixed with `BLIND_TREE`'s own name (`data-room` by convention).** A path like
`11_environmental-hs/11.2_site-reports/11.2.4_phase-2.md` is correct; the same path with a
leading `data-room/` is not, and is silently wrong in a way nothing here catches: it still
loads, still validates, and only fails later, when `/vdr-build` calls
`synthvdr.twin.build_flagged_tree` and it raises `TwinError: finding evidence path(s) not
found under BLIND_TREE` for every finding at once, because `build_flagged_tree` computes
each real file's key as its path *relative to* `BLIND_TREE`, which never includes
`BLIND_TREE`'s own name. This is the same convention gate 8's carrier census, the subset
builder and `/vdr-score`'s deterministic pre-match all rely on — one prefix in one finding's
`source` is one finding that cannot be built, scored, or found by any of them.

Rules that do not appear anywhere else and must be followed exactly, because the harness
enforces some of them mechanically and the rest are invisible to it:

- **One distinct issue is one finding ID**, owned by whichever workstream it most naturally
  belongs to. If the same issue is relevant to a second workstream, reference it there with
  `cross_links` — never give the same issue a second ID under a different workstream.
  `synthvdr.schema.validate` will not catch a duplicated issue; it only catches a duplicated
  *ID*, which is a different, narrower thing.
- **For a multi-document finding, no single contributing document may state the
  conclusion.** Each document that appears in `source` or `corroboration` carries its
  fragment as ordinary, neutral seller-side fact — the tension between the fragments, and the
  conclusion that follows from it, exists only in the answer key and (later) in the flagged
  twin's annotation block. A document that pre-resolves its own fragment defeats the entire
  point of a multi-document finding: there would be nothing left to corroborate.
- **Never use a finding-ID-shaped token as an in-room reference code.** Gate 4's leakage
  sweep recognises any token matching `<one of your FINDING_PREFIXES>-<digits>` (or `DX-<
  digits>`) anywhere in the blind room, with no way to tell a real finding ID from an
  in-room document reference that happens to look like one. A supply agreement numbered
  "ENV-1" for entirely unrelated reasons will either falsely trip the leakage gate or — far
  worse — hide a genuine leaked finding ID behind a false-positive the author has learned to
  ignore. Pick reference numbering that cannot collide with the prefix alphabet you declared.

## 3. Draft the distractors

Write `_key/distractors.yaml` (schema: `synthvdr.schema.Distractor`, loaded by
`synthvdr.schema.load_distractors`). Again, the shape, not a description of it:

```yaml
distractors:
  - id: DX-1
    title: Alarming-looking regulator notice, fully remediated
    shape_matches: ENV-1
    location: 11_environmental-hs/11.4_hse-notices/11.4.2_improvement-notice.md
    resolution: 11_environmental-hs/11.4_hse-notices/11.4.3_closure-letter.md
```

`id`, `title`, `location` and `resolution` are required — `load_distractors` raises
`SchemaError` naming the missing field if any is absent. **`location` and `resolution` follow
the exact same convention as a finding's `source`/`corroboration` above: relative to the
blind tree root, never prefixed with `BLIND_TREE`'s own name.** `shape_matches` is optional but
should name a real finding `id` whenever the distractor is that finding's benign twin — see
below. **At least a third** of all distractors must be shape-matched benign twins of real
findings this way — a document that looks exactly as alarming as a genuine finding but
resolves to nothing. These are what measures a tool's false-alarm rate; a room with only
"obviously fine" distractors measures nothing.

Every distractor's `resolution` must be a *different* document (never the same document as
`location`) that carries the benign explanation as ordinary, unannotated content — not a
note saying "this is fine," just the same kind of neutral fact a real corroborating document
would carry. `synthvdr.schema.validate` checks this mechanically: it rejects a distractor
whose `resolution` equals its `location`, and it rejects a `location` or `resolution` that
is also a finding's evidence path (`source` or `corroboration`) — a document cannot
simultaneously be a planted trap and real evidence, and a distractor whose resolving
document does not actually exist and stand apart from every finding is not a distractor at
all, it is a second, unlisted finding.

## 4. Declare deliberate gaps

Write `_key/gaps.yaml`: any cross-reference the room will deliberately leave unresolved,
with a reason. This is gate 9's allowlist (`synthvdr.qa.structural.gate_09_xrefs`, via
`parse_gaps_allowlist`) — it allows exactly the refs declared here and nothing else, so the
shape must match what that parser actually reads, exactly:

```yaml
gaps:
  - ref: "3.2.9"
    reason: "Referenced in the VAT correspondence narrative; no board-minutes slot for FY2019 exists in this room by design — the year predates incorporation."
```

`ref` is the slot-shaped token (`<section>.<subsection>.<ordinal>`, matching the numbering
your document filenames already use) that appears in the room's prose but resolves to no
real document — quote it, since YAML would otherwise misparse a two-dot token. `reason` is
required by this skill's own discipline, even though the gate itself does not read it: it is
what stops the allowlist from silently accumulating entries nobody can explain a year later.
A row missing `ref` is not caught by anything until `/vdr-qa` runs gate 9 against the built
room — get the key name right now, not then. An undeclared dangling reference is a corpus
defect, not a deliberate design choice, and the gate cannot tell the two apart unless you
tell it first, in exactly this shape.

## 5. Validate

```python
from pathlib import Path
from synthvdr.schema import load_findings, load_distractors, validate, render_findings_md

f = load_findings(Path("_key/findings.yaml"))
d = load_distractors(Path("_key/distractors.yaml"))
errors = validate(f, d)
print("\n".join(errors) if errors else "valid")
Path("_key/findings.md").write_text(render_findings_md(f, room_codename))
```

Fix every error `validate` reports before proceeding — do not hand-wave past one because it
looks cosmetic; each one corresponds to a real downstream gate that would otherwise fail
much later, with far less context about why. `/vdr-qa`'s gate 17 runs this exact check again
as an automated backstop, so an error left unfixed here does not ship silently — but a real
error is far cheaper to fix now, before a single document exists, than after a full build.

Then set `EXPECTED_KDP_CARRIERS` in `room.conf` to `len(f.all_evidence_paths())` — the
number of *distinct* documents named across every finding's `source` and `corroboration`
combined (a document corroborating two findings still counts once). This is the number
`/vdr-build`'s per-wave QA checks the flagged tree's annotation-carrier count against; get it
from the loaded `FindingSet`, not by counting rows in the YAML by eye — a document shared
between findings makes those two different numbers.

## Gate B — hard stop

Show the user the complete registry: every finding with its severity, its source and
corroborating documents, and its substance in plain language, plus the distractor list and
the declared gaps. **No authoring begins until they sign it off.** This is deliberately the
most expensive gate to get wrong in the whole plugin: a registry changed after documents
have been authored against it means rewriting those documents, so any cost of getting the
registry wrong here is paid later, in full, and by someone re-doing finished work.
