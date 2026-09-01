---
name: vdr-qa
description: Run the nineteen room QA gates — index regeneration, leakage sweeps, twin invariants, carrier census, cross-references, depth lint, subset, fact-sheet and answer-key reconciliation, unchecked-name sweep, discoverability, render parity, the room's exemplar/eval role declaration and the eval room's classification answer key. Use --strict before any release.
---

# Run the QA gates

```bash
python3 -m synthvdr.qa --room .            # normal run
python3 -m synthvdr.qa --room . --strict   # release mode
```

`tools/check.sh` is a thin wrapper around the same command (`bash tools/check.sh .` and
`bash tools/check.sh . --strict`) — use whichever is at hand, they run the identical
nineteen gates.

If either form reports that it cannot import `synthvdr`, the room's `python3` is not the
interpreter the package was installed into — which is the normal case, since a room is a
directory of its own. Name the right one and carry on:

```bash
SYNTHVDR_PYTHON=/path/to/.venv/bin/python bash tools/check.sh .
```

## Reading the output

Every gate prints exactly one line: `PASS`, `FAIL`, `SKIP` or `WARN`, its number, its name
and a one-line detail. The run ends with a summary counting failures, skips and warnings.

**Take `SKIP` seriously.** A gate whose inputs are absent says so out loud and is counted
in the summary rather than silently omitted. This exists because in an earlier build the
render gates printed *nothing at all* when their inputs were missing — silence
indistinguishable from a pass — and three renderer defects, one of them clipping
answer-key evidence out of the PDFs, survived behind it for two phases.

`--strict` turns every skip into a hard failure. That is the correct mode before a
release, where "we did not check" is not an acceptable answer — and it is the mode
`/vdr-package` runs internally before it will freeze a room. A room may legitimately show
skips in normal, mid-build runs (renders not built yet, subset not built yet); it must
show none at release.

Exit codes: `0` clean; `1` on any `FAIL`, or any `SKIP` under `--strict`; `2` if the room
could not even be loaded (missing or malformed `room.conf`, or a malformed answer key) —
distinct from `1` so you can tell "the checks found a problem" apart from "the checks
never ran."

## The nineteen gates, briefly

| # | Gate | Checks |
|---|---|---|
| 1 | Index count and regeneration | `index.md` matches a fresh regeneration from `_key/index-src/` |
| 2 | Tree counts | Document counts match `room.conf`'s declared totals |
| 3 | Annotation-string leakage | The flag strings never appear in the blind tree |
| 4 | Blind-tree vocabulary sweep | Answer-key vocabulary never appears in the blind tree |
| 5 | Index.md vocabulary sweep | A wider vocabulary sweep of `index.md` itself |
| 6 | Directory canon | Blind and flagged trees mirror the same directory shape |
| 7 | Twin diff | A blind document and its flagged twin differ only by an annotation block |
| 8 | Annotation-carrier census | Every finding's/distractor's evidence path exists, and every markdown evidence path carries its annotation block in the flagged twin |
| 9 | Cross-reference resolution | Every cross-reference in the room resolves to a real slot |
| 10 | Depth lint | Document depth and density are inside the room's declared bounds |
| 11 | Subset reconciliation | `subset/`, if built, reproduces every finding with its full evidence chain |
| 12 | Answer-key containment | Nothing under `_key/` leaks into the blind tree |
| 13 | Fact-sheet reconciliation | Canonical figures in `_key/fact-sheet.md` appear consistently, with no superseded value surviving |
| 14 | Unchecked names | Every entity-shaped token in the room is on the fact-sheet cast list, and no recorded verdict is a collision |
| 15 | Discoverability audit | Every registered finding has a recorded `discoverable_from_blind` verdict |
| 16 | Render parity | DOCX/PDF renders, if built, mirror the blind tree's document set by filename, in both directions (never checks rendered content) |
| 17 | Answer-key validation | `_key/findings.yaml`/`_key/distractors.yaml` pass `synthvdr.schema.validate()`'s own internal-consistency checks |
| 18 | Room role declared | `room.conf` says whether this is an `exemplar` room (may teach the downstream classifier) or an `eval` room (only ever scores it) — the train/test split, enforced |
| 19 | Eval answer key | An `eval` room carries `_key/answer-key.jsonl`, and the file matches a fresh rebuild from `_key/labels.yaml` — an eval room without a complete, current classification key cannot score the classifier, which is the one thing it exists to do. Exemplar rooms pass: the key does not apply to them |

## Common failures and what they mean

- **Gate 1 regeneration diff** — someone hand-edited `index.md`. Fix
  `_key/index-src/` and regenerate; never patch `index.md` directly.
- **Gate 3 or 4** — answer-key material has reached the blind eval input. Find it and
  remove it from the blind document; the corpus is not usable until it is clean.
- **Gate 5** — build vocabulary in `index.md`. Its token list is *wider* than gate
  4's on purpose. Do not trim it to match: the leak this gate exists to catch used none of
  gate 4's tokens.
- **Gate 8 carrier census** — either a distractor's `location`/`resolution`, or a finding's
  `source`/`corroboration`, names a document that does not exist under `BLIND_TREE` (fix the
  answer key or author the missing document); or a planted finding's annotation block has
  been deleted from its flagged twin. Gate 7 cannot catch the second case on its own: a
  stripped twin is byte-identical to its blind twin, which is exactly what a benign document
  looks like.
- **Gate 11** — `subset/` was built against a stale `_key/findings.yaml`. Rebuild it with
  `synthvdr.subset.build_subset` (see `/vdr-package`) before re-running the gate.
- **Gate 13** — a canonical figure appears nowhere, or a superseded one survived a
  correction. Fix the room, not the fact sheet.
- **Gate 14** — one of two things, and the detail says which. Either an entity-shaped name
  has crept into the room since `/vdr-scope`'s collision check ran — register it in the fact
  sheet's `## Cast` or `## Invented names` table and name-check it, or remove it. Or
  `_key/name-check.md` records a **collision**: a name the check positively found to belong
  to a real company. That is `/vdr-scope`'s Gate A hard block, and there is no sign-off that
  waives it — invent a replacement name, re-check it, and rebuild every document that used
  the old one. This is reported however far the build has got, including before any document
  exists, because that is when replacing the name is cheapest.
- **Gate 14 WARN** — a name is recorded `ambiguous`, `unchecked` (what `/vdr-scope` writes
  when WebSearch was unavailable), or with a verdict that is neither of those nor `clear`.
  None of these blocks automatically and none affects the exit code, but Gate A requires the
  user's explicit acknowledgement of each, so the warning exists to stop a room quietly
  shipping with one forgotten. Resolve the name or confirm with the user that they accept it.
- **Gate 15** — a finding is not yet audited. Run the `vdr-auditor` subagent (see
  `/vdr-build`'s "After the last wave" section) and write its verdict into
  `_key/findings.yaml`.
- **Gate 16** — a render exists but no longer matches its blind source. Re-render; the
  renderer is non-destructive by design, so a stale render is never auto-corrected for you.
- **Gate 17** — the answer key itself is internally inconsistent (a dangling `cross_links`
  entry, a `multi_document`/`corroboration` mismatch, a distractor whose `location` or
  `resolution` doubles as real evidence for a finding, ...). The FAIL detail names the exact
  problem `synthvdr.schema.validate()` returned; fix `_key/findings.yaml` or
  `_key/distractors.yaml` directly.

- **Gate 19** — an eval room is missing its classification answer key, or the key no
  longer matches the room. Run `python3 -m synthvdr answerkey --room .` (see
  `/vdr-package` Step 5); if the rebuild itself refuses, the message names the
  unlabelled document or the label that points at nothing.
## Before release

Run `python3 -m synthvdr.qa --room . --strict` yourself before handing a room to
`/vdr-package` — packaging refuses to freeze a room with any failure or any skip, so
finding one here first is faster than waiting for the packaging step to refuse.
