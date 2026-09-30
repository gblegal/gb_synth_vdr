# Using synth-vdr in Legora

Two skills in the open SKILL.md format, for Legora's skill builder. They put
Legora in the seat of the tool under test: it reviews a blind synthetic room,
and its report is marked at home by `python3 -m synthvdr score`.

- `review-a-test-data-room` reviews the room in its project and saves a report
  in the shape the importer reads. Its references are
  `1-the-twenty-sections.md` (the room's folders) and `2-what-to-hand-back.md`
  (the report's shape, the four severity words and a worked example). It is
  told nothing from any answer key: it coaches the shape of the report, never
  what to look for.
- `explain-a-planted-finding` answers "what was planted here", "where is
  CORP-1" and "was that a red herring" from a room's exported key, in a
  project of its own. It has no references.

`probe/` is the vdr-build probe, a separate skill that asked the platform the
questions Tier B of `docs/legora-bundle-plan.md` needed answering. See its
own README.

## Where this lives, and how it is built

The sources are here: each skill's `SKILL.md`, the hand-back page under
`skills/review-a-test-data-room/references/`, and this README. The sections
page is generated from `domain/ma/sections.yaml` at build time, so the
folders it names are the folders rooms are built with.

```bash
make legora         # writes dist/legora: this README, a folder and a zip per skill, Word copies of the references
make legora-check   # fails if dist/legora lags its sources
```

`make legora ARGS=--no-docx` skips the Word copies, which need pandoc.

Each built `SKILL.md` carries one line under its heading, `Built from
synth-vdr <version> · domain pack ma`, and the review skill asks the agent to
copy it into the report, so a report names the build that made it. Build
from a release tag, and name the skill in Legora with its build, for example
"review a test data room 0.20.0": the folder Legora creates is named from the
skill's name, and that name is the only record in Legora of which build ran.

## Testing a tool: a Legora review, end to end

1. **Choose the cut.** Upload one of the room's trees, as a folder: `subset/`
   for a first run, `data-room/` for the full room, or `data-room-pdf/` or
   `data-room-docx/` to test Legora on rendered documents. Never `_key/`, and
   never anything with "flagged" in its path: those are the answers.
2. **Name the project** with the room and the cut: "Tarnwold subset",
   "Quern data-room-pdf". The skill copies the project's name into the
   report's title, and the importer stamps provenance only when that title
   names the room: rooms share their folder names, so paths alone cannot.
3. **Run it.** In a new conversation: "Review this test data room and list the
   issues." Accept the save when it is suggested; the report goes to
   `_review/`.
4. **Download** the report: Markdown, or Word if that is what came back.
5. **Import it**, naming the cut that was uploaded:

   ```bash
   python3 -m synthvdr import-legora-review ~/Downloads/review-tarnwold-subset.md \
       --room ~/Dev/ll_vdr_09 --cut subset --out ~/Dev/ll_vdr_09/eval-runs/legora-subset.json
   ```

   It refuses on any path the cut does not hold, whether from a wrong
   `--cut`, a stale copy in Legora or an invented path, and lists every one;
   `--drop-unknown` imports the rest and leaves the run UNVERIFIED. It refuses
   outright a report citing anything from `_key/` or a flagged tree, and an
   issue citing more than 20 documents unless `--allow-wide-issues`. It
   reports what the files-read list leaves out, and which issues cite nothing.
   If the report says the model is "not known", `--tool legora/<model>`
   records the one Legora's settings show.
6. **Score it:** `python3 -m synthvdr score <the --out file> --room <the room>`.
7. **Adjudicate** what the pre-match left unresolved, with `/vdr-score`.

For classification, use gb-docclass's Legora skills; `score-classification`
reads their manifest as it is.

## Explaining a finding

```bash
python3 -m synthvdr export-key --room ~/Dev/ll_vdr_09 --out ~/Desktop/tarnwold-key
```

The folder must sit outside the room. Upload it, and only it, to a project
of its own, and use `explain-a-planted-finding` there. It holds
`findings.md`, `distractors.md` and the flagged copies of the documents that
carry evidence or a red herring. Never put it in a project that holds a blind
room.

## Building a room in Legora

Tier B of `docs/legora-bundle-plan.md`. Not built yet.
