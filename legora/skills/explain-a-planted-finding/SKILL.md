---
name: explain-a-planted-finding
description: Answer questions about what was planted in a synthetic test data room, such as "what was planted in this document", "where is CORP-1", "was that a red herring" or "what does ENV-2 rest on", by quoting the room's answer key. Use when adjudicating a review of a synth-vdr room. Reads the key only, and never reviews the room or scores a run.
---

# Explain a planted finding

This project holds one synthetic data room's answer key, exported with
`python3 -m synthvdr export-key`: `findings.md`, every planted finding;
`distractors.md`, every red herring; and `flagged/`, the documents that carry
a finding's evidence or a red herring, each with its findings written into it
under "Key diligence points". Someone adjudicating a review of that room is
asking what the key says. Answer from the key, quoting it, and stop.

## First, check the project

If the project holds anything that looks like the blind room, such as
numbered section folders like `01_corporate/` outside `flagged/`, stop. Say
that the answer key and the blind room must never share a project, because a
review run in it could read the answers, and answer nothing else until it is
fixed.

## What to answer, and from where

- **"What was planted in this document?"** Every finding whose source or
  corroboration names it, from `findings.md`, and the document's "Key
  diligence points" block from `flagged/`, quoted. If a red herring sits there
  or is resolved there, say so from `distractors.md`. If the document is in
  none of the three, say nothing was planted in it.
- **"Where is CORP-1?"** Its source document, its corroboration, its location
  and its substance, quoted from `findings.md`.
- **"Was that a red herring?"** The distractor from `distractors.md`: where it
  sits, the document that resolves it, and which finding it imitates. If the
  document is not a distractor's location, say it is not one.
- **"How severe is it?"** The severity recorded in `findings.md`, and nothing
  more.

## Rules that always apply

- Quote the key. Do not paraphrase it into something it does not say, and do
  not add reasons of your own for why a finding matters.
- Do not review the room. If asked whether something else in a document is
  an issue, say the key does not cover it.
- Do not score a run, and do not say whether a tool's issue matches a
  finding. Matching is done by `python3 -m synthvdr score` and its
  adjudication step, which record every judgement; an answer here would be an
  unrecorded one.
- If the key does not answer the question, say so plainly.
