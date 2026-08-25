# synth-vdr

A Claude Code plugin that invents a complete, fictional M&A data room, hides a known set of
problems inside it, and proves those problems can genuinely be found — so you can measure
how well a document-review AI does at finding them.

## Why it exists

To know how good a diligence tool really is, you need a data room where you already know
every answer. Real data rooms are out: they are confidential, and you cannot hand one to a
vendor to benchmark against. Made-up rooms usually fail the other way — whoever wrote the
room simply declares what the answers are, and nobody ever checks that a reviewer working
from the documents alone could actually have found them.

synth-vdr builds the room and the marking scheme together, then checks the marking scheme
against the room. A separate reviewer is sent into the documents without being told where
anything is. If it cannot find a planted problem, that problem does not count as findable,
and the room does not pass.

## What you get

- **The room a tool sees** — a realistic seller-side data room, organised into the usual
  twenty diligence workstreams, anywhere from 40 documents up to 2,000.
- **The answer key** — every planted problem: what it is, how serious it is, and which
  documents carry the evidence.
- **The same room, annotated** — an identical copy in which each document carrying a
  planted problem has the answer written at the foot of it, for reviewing the room by eye.
- **Deliberate red herrings** — things that look alarming and are entirely fine, so you can
  measure false alarms as well as hits.
- **A small sample room** — a smaller, reproducible cut of the room that still contains
  every planted problem, for a quicker or cheaper evaluation run.
- **A scorecard** — run a tool over the room, hand its output back, and get recall,
  precision, false alarms, and a comparison against a previous run.
- Optionally, Word and PDF versions of every document, including pages rendered as scans so
  a tool has to read them the hard way.

## How it works

Six commands, run in order. Each stops when its own job is done; nothing runs ahead of you.

1. **`/vdr-scope`** — asks you roughly one question (a sector you have in mind, or shall it
   invent one?), then makes up the deal: the companies, the people, the sites, the numbers
   and the dates. It also searches the web for every invented name, to check it has not
   accidentally landed a fictional scandal on a real business. **You sign this off before
   anything else happens.**
2. **`/vdr-findings`** — decides what is wrong with this business: the planted problems,
   how serious each one is, which documents will carry the evidence, and which red herrings
   sit alongside them. This is the marking scheme, and it is fixed before a single document
   is written. **You sign this off too.**
3. **`/vdr-build`** — writes the documents, in batches, using a team of subagents. Each
   writer sees only its own handful of documents and writes them as ordinary seller-side
   paperwork: no note to the buyer, no highlighting, nothing that gives the game away. The
   evidence-carrying documents are written first, so an interrupted build never leaves a
   problem half-planted. Stop it and start it again and it picks up where it left off.
4. **`/vdr-qa`** — runs seventeen automated checks over the room: that the answers have not
   leaked into the documents, that nothing is a half-finished stub, that every figure
   agrees with every other, and that each planted problem really was reachable by a
   reviewer who was never told where to look.
5. **`/vdr-package`** — re-runs those checks in their strictest form, builds the sample
   room, optionally renders Word and PDF, and stamps the room with a fingerprint so a
   scorecard can never be attributed to the wrong room. It refuses to release a room with
   any check failing — or even skipped.
6. **`/vdr-score`** — marks a tool's output: how many planted problems it found, how many
   of its reports were right, how many red herrings it fell for, and whether it has
   improved on the last run.

## What this is not

This is not a real data room, and it must never contain real client, deal or personal data.
Every company, person, brand, product, site and domain name in it is invented for the room.
It is a test rig for evaluating software — not a precedent bank, and not a store of real
documents.

## Getting started

Install the plugin's Python package from a checkout of this repository. Then make an empty
folder for the room, open a Claude Code session in that folder, and run `/vdr-scope`.
Everything after that follows the six steps above.

The room is written into whatever folder you happen to be in, and can run to thousands of
files, so the empty folder matters. The exact commands are in
[TECHNICAL-NOTES.md](TECHNICAL-NOTES.md).

## Further reading

- [ARCHITECTURE.md](ARCHITECTURE.md) — how the pieces fit together, with a diagram of the
  workflow.
- [TECHNICAL-NOTES.md](TECHNICAL-NOTES.md) — installation, commands, file formats, the test
  fixture, and the known limits of what this project can check.
- [docs/superpowers/build-record/](docs/superpowers/build-record/) — why the design is the
  way it is, including the options that were deliberately not taken.
