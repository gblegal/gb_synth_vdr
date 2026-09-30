---
name: review-a-test-data-room
description: Review a synthetic test data room as a buyer's lawyer would, and hand back every issue found, each with its severity and the documents it rests on, as a saved report. Use when asked to review a test data room, run due diligence on a synth-vdr room, or list the issues in a benchmark room. For synthetic test rooms only, never a live matter.
---

# Review a test data room

The documents in this project are a synthetic data room: an invented company
being sold, written to read like a real seller's room. Review it as a buyer's
lawyer would on a real deal, and hand back every issue you find in the shape
`2-what-to-hand-back.md` sets out. A program reads the report before a person
does, so the shape matters as much as the reading.

## What to read

- Only the documents in this project. Do not open, search or cite any mounted
  database, whether a firm knowledge base, a contract database such as
  "CUAD Database" or any other, and do not search the web or draw on earlier
  conversations. A review that reads outside the room is not a review of the
  room.
- Every document. The room is organised in numbered section folders, listed in
  `1-the-twenty-sections.md`, and every document sits in one of them. Keep a
  list of every path you open, as you open it.
- Skip any folder named `_review`. It holds earlier reports, and anything
  saved to a project is read as a document next time.
- If the room is too large to read in one pass, split it among sub-agents by
  section folder. Give each one this skill's two references and its folders;
  each reads every document in them and returns its issues in the hand-back
  shape, with the list of files it read. Then read their issues together:
  merge any two that are the same issue, and where issues in different
  sections turn out to be one issue, report it once, citing every document.

## What to hand back

One report, in the shape `2-what-to-hand-back.md` sets out. In short:

- One issue per `##` heading, with `Severity:` as the first line of its body:
  critical, high, medium or low, as that page defines them.
- Every document the issue rests on, as a backticked path starting at its
  section folder, extension included, exactly as the file is named in this
  project.
- `# Files read` last, listing every path you opened.
- No tables anywhere.

At the top, two lines. `Model:` gives the exact identifier of the model you
are running on, but only if something in your context states it; otherwise
write `not known`. Do not guess, and do not copy an identifier from anywhere
else. `Skill:` gives the line beneath this skill's heading that begins "Built
from", copied exactly; it is the only record of which build of this skill ran.

## Saving it

Save the report as a Markdown file in a folder named `_review` in this
project, using suggest-save, named `review-<project name>-<date>.md`. Word is
acceptable if Markdown is not available. The report must be saved as a file:
a report left in your reply is not a deliverable, because nothing can mark it.
