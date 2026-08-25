# synth-vdr

A Claude Code plugin that generates **synthetic M&A virtual data rooms** with a verified,
machine-checked ground-truth answer key, for evaluating document-review and due-diligence
AI tools.

## Install

```bash
pip install -e .              # the plugin's Python package (synthvdr) — required before any /vdr-* skill runs
pip install -e ".[dev]"       # adds pytest, for the test suite
pip install -e ".[docx]"      # adds python-docx, only if you want DOCX renders (see "Optional render toolchains")
```

Every `/vdr-*` skill shells out to `python3 -c "from synthvdr...` or `python3 -m synthvdr...`
— none of them work until `synthvdr` is importable. This is a Claude Code plugin, not a
package on PyPI: install it from a checkout of this repository, not `pip install synthvdr`.

`/vdr-scope` is the first skill and creates the room in the **current working directory** —
`room.conf`, `_key/`, and eventually `data-room/` with up to 2,000 documents. Create and `cd`
into an empty directory for the room before running it; nothing in this plugin will do that
for you, and a 200+ document tree lands wherever your shell happens to be.

## Who this is for

Anyone building or evaluating a document-review / due-diligence tool who needs a realistic
data room to test it against — realistic section structure, realistic document volume and
depth, and a small number of deliberately planted findings and distractors — **with proof
that the findings are actually discoverable from the room a tool would see**, not just
declared true by the person who built it. It is not a real data room, and it must never
contain real client, deal or personal data: everything in it, including every company,
person, brand, product, site and domain name, is invented for the room.

## The pipeline

Six `/vdr-*` skills, run in this order, each gated on the previous one closing off:

1. **`/vdr-scope`** — a very light-touch interview, then an invented deal fact sheet
   (`_key/fact-sheet.md`), `room.conf`, the slot index, and a name-collision check for
   every invented name. Stops at **Gate A** for sign-off.
2. **`/vdr-findings`** — the findings registry, distractors and evidence-chain map
   (`_key/findings.yaml`, `_key/distractors.yaml`, `_key/gaps.yaml`) — the eval
   specification, fixed *before* a single document is authored. Stops at **Gate B**.
3. **`/vdr-build`** — authors the room in waves of subagents (one `vdr-author` per
   document, one `vdr-auditor` confirming each finding is reachable without the key),
   consolidating any mid-authoring discoveries and running the QA gates after every wave.
   Resumable: re-running continues from `_key/build-status.md` rather than starting over.
4. **`/vdr-qa`** — runs the seventeen QA gates (`python3 -m synthvdr.qa --room .`) that hold
   the room together: index regeneration, leakage sweeps, twin invariants, carrier census,
   cross-references, depth lint, subset and fact-sheet reconciliation, the unchecked-name
   sweep, discoverability and render parity.
5. **`/vdr-package`** — re-runs the gates in `--strict` mode (refuses to freeze a room with
   any failure *or any skip*), builds the deterministic subset, optionally renders DOCX/PDF
   trees, and writes `_key/manifest.json`'s content hash.
6. **`/vdr-score`** — scores a tool's output against the answer key: recall by severity,
   precision, false alarms against the distractors, partial credit on multi-document trails,
   provenance verification against the packaged manifest, and a baseline diff between two
   tool runs.

## Worked example: the XS fixture

`fixtures/xs-room/` is the hand-authored ANSWER KEY for an XS room (4 findings, 2
distractors) checked into this repository — `room.conf`, `_key/fact-sheet.md`,
`_key/findings.yaml`, `_key/distractors.yaml`, `_key/name-check.md` and a sample tool output
(`tool-output-sample.json`) — six files, no documents yet. `tests/conftest.py`'s
`build_fixture_room()` is what turns this key into an actual 40-document room: generating
filler prose above every slot's depth floor, deriving the flagged (annotated) tree, and
building the 10-document subset. `tests/test_end_to_end.py` runs `build_fixture_room()` and
then every gate against the room it produces, proves each gate is load-bearing by breaking
the room in one specific way per gate and checking the *right* gate catches it, and scores
the sample output to confirm it reports exactly recall 75%, precision 75% and one false
alarm. It is the closest thing this project has to a smoke test for the whole pipeline; run
it with:

```bash
pip install -e ".[dev]"    # pytest is in the dev extra, not a base dependency
python3 -m pytest tests/test_end_to_end.py -v
```

To try the CLI surface by hand against a built copy of it — **prefer `--strict`**: the
plain form is a mid-build diagnostic (it legitimately skips gates whose inputs, like a
subset or a render tree, do not exist yet) and its summary line can read as a clean pass
even when most gates never ran at all — `--strict` is what actually verifies the room:

```bash
python3 -m synthvdr.qa --room <built-room-dir> --strict    # verifies the room; use this
python3 -m synthvdr.qa --room <built-room-dir>              # mid-build diagnostic only
python3 -m synthvdr score fixtures/xs-room/tool-output-sample.json --room <built-room-dir>
```

## Schemas

- `schemas/findings.schema.json` — the answer-key findings document (`_key/findings.yaml`).
- `schemas/distractors.schema.json` — the answer-key distractors document
  (`_key/distractors.yaml`).
- `schemas/tool-output.schema.json` — the shape a tool's output is read in as for
  `/vdr-score`: JSON with `tool`, an optional `room_hash` (the packaged manifest's content
  hash, for provenance verification), and a `findings` list of `{title, severity, documents,
  summary}`. A lenient markdown fallback is also accepted — one `##`/`###`/`####` heading per
  finding — but a tool that genuinely found nothing must say so with the JSON format's
  explicit empty `"findings": []`; an empty or prose-only markdown file is treated as
  unparseable, not as a zero-finding run, because the two are not distinguishable from the
  file alone.

YAML is canonical for the answer key; `findings.md` is generated from it and must never be
hand-edited.

## Optional render toolchains

A room is markdown at heart, and a tool can be evaluated against clean markdown alone.
`/vdr-package` can additionally render:

- **DOCX**, via `synthvdr.render.docx` (requires the `python-docx` package — install with
  the `docx` extra: `pip install -e ".[docx]"`).
- **PDF**, via `synthvdr/render/pdf.mjs`, a separate Node process (requires Node and a local
  Chrome/Chromium for Puppeteer).

Both renderers are optional and non-destructive — they only create or overwrite the files
they are responsible for, never delete a stale render — and neither is imported at core
build time, so a missing `python-docx` or missing Node/Chrome never blocks generating or
QA-checking a room. **`synthvdr.qa.gate_16_render_parity` checks filename parity only, in
both directions — it never opens a rendered file to compare content.** Whenever a render tree
is present it confirms every blind-tree document has a same-named render and every render has
a same-named source; it SKIPs loudly (never silently) when neither render tree exists. A
render whose *content* has drifted from its markdown source (a stale render left behind after
the source was edited, say) is not something this gate can see — re-render after any source
edit rather than relying on gate 16 to catch it.

## Limits

**The flagged-tree ownership marker is a safety interlock against misconfiguration, not a
security control.** `synthvdr.twin` (and, under their own marker names, `synthvdr.subset`
and `synthvdr.index_build`) refuses to delete a non-empty directory it did not create,
proven by a marker file it writes at that directory's root on every build. This stops a
mistyped `room.conf` path from silently destroying an unrelated directory; it does **not**
stop a deliberate attacker, since anyone able to plant the marker file could already delete
the directory themselves. The marker must be a **real file, never a symlink**, and is matched
by **exact, case-sensitive name** — a symlink at that name, or a same-named entry differing
only in case, does not count as the marker, on any filesystem.

**Renders are DOCX and PDF only — there is no XLSX render.** A room that plants evidence
inside a spreadsheet-shaped document (a register, a schedule) still ships that evidence as
markdown; `gate_16_render_parity` covers the two render trees above and has no XLSX
equivalent to check.

**The name-collision check reduces risk; it does not eliminate it.** `/vdr-scope`'s
WebSearch-based check, and `gate_14_unchecked_names`'s corpus-wide safety net behind it, can
only ever prove a *hit* — that a search returned something. **A search returning nothing is
not proof that no such company, brand, product, site or domain exists**: dormant companies,
recently deregistered entities and non-English-language markets will not reliably surface in
a web search, and gate 14's entity-suffix pattern can also both miss a genuine unchecked name
with an unlisted suffix, and — for any suffix whose own upper- or lower-case spelling happens
to coincide with an ordinary English or business word — flag ordinary prose that never named a
company at all (this project's own domain pack tripped exactly that case during Task 20's
first end-to-end run: a document heading containing "draft SPA", the standard M&A shorthand
for a Share Purchase Agreement, was read as the Italian "S.p.A." corporate suffix. Fixed by
matching that one suffix in its exact canonical case only, `synthvdr.names.ENTITY_SUFFIXES`
still carries a small, closed list of corporate-suffix tokens, and any future addition to it
should be checked against the same question before being matched case-insensitively). Treat a
clean check as lowered risk, never as a guarantee.

**Determinism is a claim about structure, not about prose.** Every *structural* artefact this
project generates — the slot manifest, the section/subsection layout, the index, the flagged
tree's derivation from the blind tree, the subset selection — is required to be byte-identical
across repeated runs and across processes (no RNG, no clock, no bare `hash()`; see
`tests/test_end_to_end.py`'s cross-process, cross-`PYTHONHASHSEED` build comparison). The
*prose* inside a document that a `vdr-author` subagent actually writes is not: two builds of
the same room can legitimately produce different wording for the same finding, and that
variation is exactly what the QA gates — leakage sweeps, depth lint, the carrier census,
discoverability — are there to check, in place of a byte-for-byte comparison that would be
both impossible to satisfy and beside the point.
