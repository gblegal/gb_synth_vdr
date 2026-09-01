---
name: vdr-score
description: Score a document-review tool's output against the room's answer key — recall by severity, precision, false alarms against the distractors, partial credit on multi-document trails, provenance verification, and a baseline diff between two tool runs. Also scores a classification tool's output against the room's classification answer key via score-classification.
---

# Score a tool against the answer key

```bash
python3 -m synthvdr score "<tool-output.json>" --room .
```

Accepts the shipped JSON schema (`schemas/tool-output.schema.json`) or, leniently, a markdown
report with one `##`/`###`/`####` heading per finding. A tool genuinely reporting zero
findings must use the JSON format with an explicit empty `"findings": []` — an empty or
prose-only markdown file is treated as unparseable, not as a zero-finding run, because there
is no way to tell those two apart from the file alone.

Exit codes: `0` on success — including a run whose provenance could not be verified, since the
normal reason is that this room has not been through `/vdr-package` yet, and that is reported
plainly inside the rendered scorecard rather than treated as a failure. `2` if the room's
answer key, the tool output, or `_key/adjudications.yaml` could not be loaded or reconciled —
grouped together because in every one of those cases no trustworthy scorecard was produced at
all, never printed alongside a partial result.

## Check the room hash first

If the tool output carries a `room_hash`, `/vdr-score` compares it against this room's
`_key/manifest.json` `content_hash` (written by `/vdr-package`) before printing anything else.

- **Both present and they differ:** the run **refuses to score**, exits `2`, and the error
  names both hashes. This is not a warning — the output was proven to have been produced
  against a different room, and scoring it against this room's answer key would be a
  confident, precise, and entirely meaningless number.
- **Either is missing** (no `_key/manifest.json` yet — the normal case before `/vdr-package`
  has run — or the tool output carries an empty `room_hash`): scoring proceeds, but the
  scorecard's provenance line reads `UNVERIFIED`, plainly, rather than silently assuming a
  match.
- **Both present and equal:** the scorecard's provenance line reads `verified`.

Tell the user plainly whenever a scorecard comes back `UNVERIFIED` — scoring against the wrong
room, or against no confirmed room at all, is a silent way to be wrong, and the whole point of
this check is that it never is silent.

## Two-stage matching

**Stage one is deterministic.** A reported finding citing a registered finding's `source` or
`corroboration` document is matched to it — to every finding whose evidence it cites, not just
one, since a single report can legitimately evidence more than one planted finding at once.

**Stage two is yours.** Anything that cites no known document at all is listed on the
scorecard as `unadjudicated`. For each, read the tool's title and summary against the
finding's `substance` and decide whether it is the same issue. Record your judgement — do not
just remember it, and do not re-derive it on a re-run — in `_key/adjudications.yaml`, which
`/vdr-score` **auto-loads from the room** on every run. There is no `--adjudications` flag: the
room is where every other answer-key artefact (`findings.yaml`, `distractors.yaml`) already
lives, so this is the one convention rather than a second one.

The literal shape to write — copy and adapt, do not reconstruct it from memory, since a
malformed file or an unresolvable entry is a loud, run-stopping error by design, never a
silently-dropped adjudication:

```yaml
# _key/adjudications.yaml
adjudications:
  - tool_index: 4
    finding_id: EMP-2
    reason: "Describes the same consultancy misclassification; cites no document."
  - tool_index: 6
    finding_id: [ENV-1, FIN-3]
    reason: "One report evidencing two distinct planted issues at once."
  - tool_index: 7
    finding_id: null
    reason: "Generic observation about contract length; matches no registered finding."
```

`finding_id` is a string, a list of strings (a single report can be adjudicated to more than
one finding, the same many-to-many truth the deterministic stage can express), or `null` — a
**positive confirmation that this report matches nothing**, not the same as leaving it out.
`finding_id` is read as the **complete** answer for that `tool_index`, not an addition to
whatever the deterministic stage already found: the adjudicator is only ever shown reports the
deterministic stage could not resolve at all, so naming a `tool_index` it already resolved is,
by definition, a correction, and the scorecard's "Adjudications that overrode a pre-match"
table shows exactly what was replaced — before and after — so an adjudicator who meant to add
credit on top sees immediately that they replaced it instead. A `tool_index` outside the tool
output's findings, a `finding_id` absent from the answer key, or the same `tool_index`
adjudicated twice are all errors that stop the run rather than silently dropping the entry —
a dropped adjudication is a silently wrong score, which this project does not allow anywhere.

Re-run the score once `_key/adjudications.yaml` is written or updated. Record adjudications
rather than re-deriving them each time: a disputed score should be inspectable, and a judgement
made twice by different people (or by the same person on different days) is a judgement made
inconsistently.

## Reading the scorecard

- **Recall** — distinct findings matched, over the total in the answer key. Marked
  **provisional** whenever any report is still `unadjudicated`: a recall of 0% because nothing
  has been judged yet is not the same claim as a recall of 0% after every report was
  positively confirmed to match nothing, and the scorecard says which one it is rather than
  printing the same number for both.
- **Precision** — reports that matched at least one finding, over all reports. Two correct
  reports of the same finding both count as right; a single duplicate is not penalised as a
  mistake.
- **False alarms** — distractors cited by a report that matched nothing else. **Distractor
  citations** — a separate, distinct line for a distractor cited *inside* an otherwise-matched
  report: a genuine find that also fell for part of a trap, kept out of false-alarms (which
  would wrongly tank precision on a true positive) but never hidden either.
- **Partial trails** — a multi-document finding matched, but not by the full union of
  documents its evidence chain requires, across every report credited with it.

## Comparing runs

```bash
python3 -m synthvdr score "<new-output.json>" --room . --baseline "<earlier-output.json>"
```

`--baseline` takes **another tool output, scored fresh against this same room** — not a saved
scorecard. Comparing two runs' outputs against one answer key is what a baseline is for: "did
this tool improve?" Adjudications apply only to the primary run being scored, never to the
baseline, since they are recorded per `tool_index` position in one specific tool output and a
baseline is ordinarily a different run entirely. The diff reports the change in recall and
precision, plus which findings were newly found and newly missed — the view that actually
matters when tracking a tool over time.

## Scoring classification

```bash
python3 -m synthvdr score-classification "<classification-output>" --room .
```

The classification twin of `score`, graded against `_key/answer-key.jsonl` (built by
`python3 -m synthvdr answerkey`; gate 19 guarantees a packaged eval room carries it) rather
than findings.yaml. Two input shapes:

- a `.json` object pinned by `schemas/classification-output.schema.json` — `tool`,
  optional `room_hash`, and one record per document under `classifications`;
- anything else is read as JSONL, one record per line — which is exactly the downstream
  classifier's native manifest, so that file scores as-is. Extra per-record fields are
  ignored; with no `room_hash` the scorecard reads `UNVERIFIED`, same as the findings side.

Provenance follows the same discipline as `score`: a `room_hash` that provably names a
different room refuses to score and exits `2`; a missing hash or manifest scores with an
`UNVERIFIED` line. Coverage must match the key exactly, in both directions — a skipped
document would be graded against silence, and a path the key does not know is a typo or a
stale key — so a mismatch refuses with names rather than quietly scoring the intersection.
A tool that cannot classify a document says so with an `unsure` record, never by omission.

The scorecard reports document-type accuracy, primary-pile accuracy, the not-sure count,
recall and precision per workstream on the primary pile, and a confusion table of where
misfiled documents went (not-sure is its own destination, never a pile). **Secondary
deliveries are deliberately not scored**: the key's `secondary_workstreams` is empty by
design — who else should see a document is the downstream project's routing policy, not a
fact about the room — and the scorecard says so in its own text rather than leaving the
absent column to be guessed at. For the same reason `sent_to_all_workstreams` never makes
a wrong primary right. The classifier's own eval (which owns the routing table) is where
delivery breadth is scored.
