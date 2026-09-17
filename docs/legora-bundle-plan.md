# A Legora skill bundle for synth-vdr

Plan, 16 September 2026. Modelled on gb-docclass's `legora/` pack and on what
`gb-docclass/docs/legora-environment.md` records about the platform. Nothing
here is built yet.

One correction to that record, from Greg on 16 September: Legora's agent has
web access and sub-agents in normal running. The limits recorded on that page
are the skill tester's sandbox, not the platform's. That fact belongs on the
environment page, dated, and this plan assumes it.

## The shape of it

Two tiers, because they carry different risk and one is useful without the
other.

**Tier A: Legora as the tool under test.** Legora reviews a blind room and
hands back findings that `python3 -m synthvdr score` reads. Prose skills only,
no engine in the sandbox, and it works today whatever the probe below finds.

**Tier B: the whole pipeline in Legora.** The six `/vdr-*` skills and the two
sub-agent briefs, with the engine shipped as `.txt` modules the sandbox can
run. Legora's agent scopes, plants, authors in waves, gates and packages a
room. Gated on a probe run, because three things it depends on are not yet
established about the platform.

Both tiers share one builder, one version line and one README.

## Tier A: two prose skills and an importer

### `review-a-test-data-room`

The description is written for the question a user asks ("review this data
room and list the issues") and says it is for a synthetic test room, so it
is not offered on live matters.

The body tells the agent to read every document, keep the list of paths it
opened, and report each issue under its own `##` heading with one severity
word in the body and every cited document as a backticked path relative to
the room root, extension included. That is exactly what
`parse_markdown_report` in `synthvdr/score.py` reads. No tables, no merged
findings, no path without its folder. Name the model with an exact
identifier and an example of one. Save the report as a file; text in a
reply is not a deliverable.

It is told nothing from the key: no archetypes, no distractors. It coaches
format, never content.

References, generated at build time:

- `1-the-twenty-sections.md` from `domain/ma/sections.yaml`, as a list.
- `2-what-to-hand-back.md`: the output contract with a worked example. A test
  runs the example through `parse_markdown_report` to prove it yields the
  findings it claims. Severity words are read from
  `schemas/tool-output.schema.json`.

New prose to agree: a four-line severity rubric. The registry defines
severity only as a ratio (1:3:4:3), never in words.

### `explain-a-planted-finding`

The counterpart to gb-docclass's `explain-a-filing`. Runs in a project
holding only the key side: `_key/flagged/`, `findings.md`, a rendered
`distractors.md`. Answers "what was planted here", "where is CORP-1", "was
that a red herring", quoting the key and never re-reviewing or scoring. Its
first rule: the project must not also hold a blind room.

### `python3 -m synthvdr import-legora-review`

```
python3 -m synthvdr import-legora-review <report.md|.docx> --room . --out tool-output.json
```

New module `synthvdr/legora_import.py`, the shape of gb-docclass's
`import_legora_run.py`. It reads Markdown or Word (the `python-docx` extra
already declared), parses with `parse_markdown_report`, then normalises
cited paths: strips a mount or `data-room/`, `data-room-pdf/`, `subset/`
prefix and maps `.pdf` and `.docx` back to `.md`. That is necessary, not
cosmetic: `prematch` matches by exact string against `.md` evidence paths,
so a run over the PDF tree scores zero today. It refuses on any cited path
the room does not hold, reports every room document the files-read list
does not name, stamps `room_hash` from `_key/manifest.json` only when every
path resolves, and takes the tool name from the model line. `score` stays
strict; leniency lives here.

## Tier B: the pipeline in Legora

### What has to be true, and is not yet known

Everything on the environment page was learned in the skill tester's
sandbox. The pipeline needs answers to five questions about normal running,
each cheap to ask with a probe skill before any port is attempted:

1. **Can a sub-agent write a file** under `/workspace/scratch/`, or does it
   only return text? `vdr-author` writes the blind document and its
   refinement YAML; `vdr-auditor` only reads.
2. **Does a sub-agent start blind?** The discoverability guarantee rests on
   an author never seeing the full registry. If a sub-agent inherits the
   parent's conversation, the guarantee is void and the port stops there.
3. **Does scratch survive a new conversation?** A 200-document build spans
   several sessions. The page says it survives between calls and that
   nobody has tried a new conversation.
4. **Can a `.zip` saved from scratch be downloaded intact?** A room is
   hundreds of files in twenty folders and downloads come back flat, one
   file already lost in transit once. A zip written by Python's `zipfile`
   keeps the tree; whether Legora's save accepts one is untested.
5. **How many sub-agents run in parallel, with what timeout?** Sets the
   wave size.

The probe that asks them is built: `legora/probe/` (a `SKILL.md`, a
stdlib-only `scripts/probe.txt`, and a README with the three-conversation
procedure), zipped to `dist/legora/vdr-probe-0.1.0.zip`. Every verdict it
gives comes from a file the script wrote, never from the agent's account.

Each answer goes on gb-docclass's environment page, dated. The fallbacks:
if (1) is no, authors return their documents and the orchestrator writes
them through a hand-back file the runner reads; if (3) is no, every session
ends by zipping the room into the project and starts by restoring it, which
`_key/build-status.md` already makes safe; if (2) is no, Tier B is not built.

### The engine in the sandbox

The `synthvdr` package ships under `scripts/synthvdr/` renamed `.txt`,
with `domain/ma/` beside it, as gb-docclass ships its modules. Three
differences from that pack:

- **PyYAML.** Six modules import it and every key file is YAML. The
  sandbox has none. Vendor PyYAML's pure-Python package as `.txt` files
  under `scripts/yaml/`, with its MIT notice added to `NOTICE`. It is the
  one third-party dependency, and it is small.
- **A bootstrap, `scripts/run.txt`.** Installs a meta-path finder that maps
  `synthvdr.*` and `yaml.*` to the `.txt` files, sets
  `sys.dont_write_bytecode`, then does what `python3` would: `run.txt -m
  synthvdr.qa …` or `run.txt -c "…"`. Every shell fence in the six skills
  becomes `python3 scripts/run.txt …` and nothing else in the fence
  changes.
- **A `room` subcommand.** `run.txt room new <name>` makes the room under
  `/workspace/scratch/`; `room zip` writes one archive the agent saves out;
  `room restore <zip>` unpacks it back. The zip in the project is the
  durable state.

Not available there: the PDF render (Node), so `/vdr-package` renders DOCX
only in Legora and says so. The sandbox is around thirty times slower than
a laptop, so gate runs over a large room take minutes; the five-minute call
ceiling still holds a 200-document room.

### The skills: transformed, not forked

The six skills are 107 KB of prose that must not exist twice. The builder
derives each Legora skill from the plugin's own `SKILL.md`:

- Rewrites every shell fence to go through `run.txt`. A test proves no
  fence in the built pack calls `python3 -c` or `python3 -m synthvdr`
  directly.
- Prepends one Legora preamble: `cd` to the skill folder on every call,
  rooms live in scratch, ask for the longest timeout, one foreground line,
  `-u`, save the zip, name the model.
- Converts tables to lists. `vdr-build` and `vdr-qa` each carry a
  21-row table, and the builder breaks tables into one-row fragments.
- Ships `agents/vdr-author.md` and `agents/vdr-auditor.md` as references,
  the way gb-docclass ships its reviewer's rules, so the orchestrator hands
  each sub-agent its brief verbatim. The dispatch prose stays as written:
  "dispatch authors in parallel, one per batch" is a generic instruction
  Legora's agent maps to its own delegation.

Anything that genuinely differs between the two homes is written once, in
the preamble, never patched into the body.

### What the builder does

`tools/build_legora_pack.py`, Python, the gb-docclass shape:

- Sources: `legora/skills/<tier A name>/SKILL.md`, `legora/preamble.md`,
  `legora/README.md`. Tier B skills have no source; they are derived.
- Writes `Built from synth-vdr <version> · domain pack ma · tool-output
  schema` under every heading, every number read from its owning file.
- Pins timestamps (1980-01-01) so two builds of one commit are the same
  bytes; pandoc Word copies, `--no-docx` to skip; one zip per skill.
- `--check` fails if any derived file lags its source or a built pack lags.

### Tests

- `tests/test_legora_pack.py`: pack builds; derived skills match a fresh
  transform; version line current; byte-identical rebuilds; the hand-back
  example round-trips through the parser; no direct `python3 -c` fence
  survives; every table became a list.
- `tests/test_legora_engine.py`: from the built pack's folder, with the repo
  off `sys.path` and PyYAML uninstalled in a subprocess, `run.txt -m
  synthvdr.qa` runs over `fixtures/xs-room` and `run.txt room zip` then
  `room restore` round-trips a tree byte for byte.
- `tests/test_legora_import.py`: extension mapping, prefix stripping,
  refusal on an unknown path, `room_hash` stamping both ways, Word input,
  and import-then-score against the fixture.
- `tests/test_plugin_surface.py`: add the Tier A skills under their names.

### Housekeeping

- `tools/version-check.sh`: add `legora` to `SURFACE`.
- `Makefile`: `legora`, `legora-check` targets. `dist/` already ignored.
- `NOTICE` for vendored PyYAML. README and TECHNICAL-NOTES sections.
- Lessons about Legora go on gb-docclass's environment page; this repo's
  `legora/README.md` keeps only what the runs taught these skills.

## `legora/README.md`: the two procedures

**Testing a tool (Tier A):** upload `data-room/`, `subset/` or
`data-room-pdf/` only. Never `_key/`. Name the project with the room and
the cut, and the skill with its build. Run, save the report, download,
`import-legora-review`, `score`, adjudicate. For classification, use
gb-docclass's Legora skills; `score-classification` reads its manifest
as-is.

**Building a room (Tier B):** one project per room. `/vdr-scope` first, in
one conversation, with the web name check; Gate A is you reading the fact
sheet. Then findings, Gate B, then build sessions, each ending with a saved
zip. Package renders DOCX only. Download the zip, unpack at home, run
`/vdr-qa --strict` there before calling the room frozen: the home run is the
one whose PDF render and full gate speed you trust.

Rooms: Quern's subset for the Tier A tester, Cairn for the first scored
run, Ellsgarth untouched as the held-out benchmark. The Tier B probe builds
an XS room and nothing more.

## Order of work

1. `legora_import.py` and its subcommand, test-first, against
   `fixtures/xs-room`. Useful before any Legora run.
2. Tier A skills and the hand-back reference, with the round-trip test.
3. `build_legora_pack.py` for Tier A only, `--check`, Makefile, version
   surface. Bump, tag, build from the tag, upload, run the skill tester on
   Quern's subset. Record what it taught.
4. **The probe.** Five questions, one small skill, answers on the
   environment page. Decide Tier B on the answers.
5. The engine in the sandbox: vendored PyYAML, `run.txt`, `room`
   subcommand, `test_legora_engine.py`.
6. The skill transform in the builder and its tests.
7. Build an XS room end to end in Legora. Unpack at home, gate it, score
   the exercise honestly: what the agent did, from its files, not its
   account.

Steps 1 to 3 are one PR and stand alone. Steps 5 and 6 are a second PR that
waits on step 4.

## Decisions to confirm

- Tier A first and shipped on its own, with Tier B behind the probe.
- Include `explain-a-planted-finding` in Tier A. Recommended yes.
- Accept Word as well as Markdown from Legora. Recommended yes.
- The severity rubric wording.
- Vendoring PyYAML rather than rewriting the key to JSON. Recommended:
  vendor. It leaves rooms and `synthvdr` untouched.
- Deriving the six Legora skills from the plugin's own files at build time
  rather than keeping separate sources, at the cost of a slightly cleverer
  builder. Recommended: derive.
