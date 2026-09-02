# Technical notes

Installation, command surfaces, file formats, the test fixture, and — most importantly —
the limits of what this project actually checks. For what synth-vdr is, see
[README.md](README.md); for how it fits together, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. Install and requirements

```bash
pip install -e .              # the plugin's Python package (synthvdr) — required before any /vdr-* skill runs
pip install -e ".[dev]"       # adds pytest and ruff, for the test suite and the lint gate
pip install -e ".[docx]"      # adds python-docx, only if you want DOCX renders (see §5)
```

Those install into whichever Python you are already in, which assumes you have somewhere to
install *to*. On a system Python that marks itself externally managed they fail outright. If
you would rather not think about it, `make test` creates `.venv`, installs the package with
both extras into it, and runs the suite — see §4. `make venv` stops after the install, for
driving the CLIs by hand afterwards, and `make clean` removes the environment again.

Python 3.9 or later. `PyYAML` is the only hard dependency; `python-docx` and the Node/PDF
toolchain are optional extras.

Every `/vdr-*` skill shells out to `python3 -c "from synthvdr..."` or `python3 -m
synthvdr...` — none of them work until `synthvdr` is importable. This is a Claude Code
plugin, not a package on PyPI: install it from a checkout of this repository, not
`pip install synthvdr`.

**Create and `cd` into an empty directory for the room before you start.** `/vdr-scope` is
the first skill and writes into the **current working directory** — `room.conf`, `_key/`,
and eventually `data-room/` with up to 2,000 documents. Nothing in this plugin creates that
directory for you, and a 200+ document tree lands wherever your shell happens to be.

---

## 2. The two CLI surfaces

They are separate entry points with separate conventions; `python3 -m synthvdr.qa` never
executes `synthvdr/__main__.py`, because `synthvdr.qa` is a package with its own
`__main__.py`.

### `python3 -m synthvdr.qa` — the gate runner

```bash
python3 -m synthvdr.qa --room <room-dir> --strict    # verifies the room; use this
python3 -m synthvdr.qa --room <room-dir>             # mid-build diagnostic only
```

**Prefer `--strict`.** The plain form is a mid-build diagnostic: it legitimately skips gates
whose inputs — a subset, a render tree — do not exist yet, and its summary line can read as
a clean pass even when most gates never ran at all. `--strict` treats every skip as a
failure, and is what actually verifies a room.

Exit codes: **0** clean; **1** on any gate FAIL, or any SKIP under `--strict`; **2** if the
room could not even be loaded (a missing or malformed `room.conf`, or a malformed answer
key) — distinct from 1 so a caller can tell "the checks found a problem" from "the checks
never ran".

`tools/check.sh <room-dir> [args...]` is a thin wrapper around exactly this command; all
the logic lives in `synthvdr.qa` so there is one implementation and one set of tests.

It is copied into each room, and a room sits nowhere near whatever environment synthvdr was
installed into, so `python3` on the PATH there is often the wrong interpreter. The wrapper
checks it can import `synthvdr.qa` before running anything and, if it cannot, says so and
names the two ways out rather than emitting a `ModuleNotFoundError` traceback from inside the
package. Set `SYNTHVDR_PYTHON` to choose the interpreter explicitly:

```bash
SYNTHVDR_PYTHON=/path/to/.venv/bin/python bash tools/check.sh <room-dir>
```

### `python3 -m synthvdr score` — the scorer

```bash
python3 -m synthvdr score <tool-output.json> --room <room-dir> [--baseline <previous.json>]
```

Exit codes: **0** on success — *including* a run whose provenance could not be verified,
because the usual reason `_key/manifest.json` is missing is that the room has not been
through `/vdr-package` yet, and that is reported inside the scorecard rather than treated as
a failure. **2** if `room.conf` or the answer key could not be loaded; if the tool output
(or `--baseline` file) could not be read or parsed; if the tool output's `room_hash`
provably names a different room than the one being scored; or if `_key/adjudications.yaml`
exists but is malformed. All of these are grouped together because in every one of them, no
trustworthy scorecard was produced at all. **There is deliberately no exit code 1 here**:
unlike the gate runner, the scorer has no "ran fine, found problems" state — a scorecard
reporting poor recall is a successful run, so the only two outcomes are a trustworthy
scorecard and none at all.

### `python3 -m synthvdr manifest` — the room's content hash

```bash
python3 -m synthvdr manifest --room <room-dir> [--built YYYY-MM-DD]
```

Writes `_key/manifest.json` and prints the `content_hash` to hand to whoever produces the
tool output scored against the room. This is `/vdr-package` step 4, and it is a command
rather than a code block inside that skill because `check_provenance` compares the hash as a
plain string: a hash constructed even slightly differently does not fail loudly, it reports
that a correct output came from a different room. Exit **2** if `room.conf` cannot be
loaded, if the blind tree is missing (a manifest there would certify an empty room), or if
`findings.yaml` exists but is malformed.

`--built` exists because the clock is read at this boundary and nowhere deeper:
`synthvdr.manifest` takes the date as a value, so nothing inside the package reads a clock.
The corrupted twin's manifest, written by `corrupt`, carries no date at all — it must stay
byte-identical for a given room, seed and profile.

---

## 3. File formats

Three JSON Schemas ship under `schemas/`:

- **`schemas/findings.schema.json`** — the answer-key findings document
  (`_key/findings.yaml`).
- **`schemas/distractors.schema.json`** — the answer-key distractors document
  (`_key/distractors.yaml`).
- **`schemas/tool-output.schema.json`** — the shape a tool's output is read in as for
  `/vdr-score`: JSON with `tool`, an optional `room_hash` (the packaged manifest's content
  hash, for provenance verification), and a `findings` list of
  `{title, severity, documents, summary}`. `severity` is a closed enum — **`critical`,
  `high`, `medium` or `low`**, and nothing else. Of the four finding fields only `summary`
  is optional; `title`, `severity` and `documents` are all required, and no additional
  properties are accepted at either level.

A lenient markdown fallback is also accepted — one `##`/`###`/`####` heading per finding —
but **a tool that genuinely found nothing must say so with the JSON format's explicit empty
`"findings": []`.** An empty or prose-only markdown file is treated as unparseable, not as a
zero-finding run, because the two are not distinguishable from the file alone.

YAML is canonical for the answer key; `_key/findings.md` is generated from it and must never
be hand-edited.

### `_key/manifest.json`, and the twin's own

Two manifests, one hash construction (`synthvdr.manifest.compute_content_hash`). The
packaged room's carries `room`, `content_hash`, `documents`, `findings` and `built`. The
corrupted twin's — written at the twin's root by `corrupt`, beside its rewritten answer key
— carries `room`, `content_hash` (over the twin's own tree), `documents`, `seed` and
`derived_from` (the clean room's `content_hash`, where the room has been packaged). It
carries **no `built` date**, deliberately: the twin is byte-identical for a given room, seed
and profile, and a clock would break that.

The twin needs a hash of its own because it *is* a different room — its documents are
renamed, misfiled, noised and truncated, so a run against it can never carry the clean
room's `content_hash`. `score-classification --key corrupted-heavy/answer-key.jsonl` checks
provenance against `corrupted-heavy/manifest.json`, the manifest beside the key it was
given. Before the twin had one, every twin run scored `UNVERIFIED` — the one provenance
state that cannot be checked, and so the state a mismatched run hides in.

### `_key/adjudications.yaml`

Scoring is two-stage. Stage one is deterministic: a tool that cites a finding's source or
corroboration documents is matched to that finding — to *every* finding whose evidence it
cites, not just one, because a single report can legitimately evidence more than one planted
finding and the cited paths say so plainly. Stage two is LLM adjudication of what is left,
performed by the `/vdr-score` skill and passed back as adjudication rows, so the scoring
logic stays deterministic and every judgement is recorded rather than re-derived.

Those rows live in `_key/adjudications.yaml`, which is **auto-loaded from the room** the same
way `findings.yaml` and `distractors.yaml` are. There is deliberately no `--adjudications`
flag, so there is only one convention for how an answer-key artefact reaches the CLI. It is
applied only to the primary tool output, never to `--baseline`: adjudications reference
`tool_index` positions in one specific findings list, and a baseline run is ordinarily a
different tool output entirely.

### How recall and precision are counted

Recall is the count of *distinct* findings matched by anything, over the total findings in
the key. Precision is the count of reports that matched *at least one* finding, over the
total reports — so two correct reports of the same finding score precision 1.0 (both were
right), not 0.5 (as if one were a duplicate mistake), while still crediting that finding
towards recall only once. A multi-document finding's partial-trail check is computed over
the *union* of documents cited by every report matched to it, so a trail split across two
reports is still a complete trail.

---

## 4. The XS fixture and the end-to-end test

`fixtures/xs-room/` is the hand-authored **answer key** for an XS room (4 findings, 2
distractors) checked into this repository: `room.conf`, `_key/fact-sheet.md`,
`_key/findings.yaml`, `_key/distractors.yaml`, `_key/name-check.md` and a sample tool output
(`tool-output-sample.json`) — six files, no documents yet.

`tests/conftest.py`'s `build_fixture_room()` is what turns that key into an actual
40-document room: generating filler prose above every slot's depth floor, deriving the
flagged tree, and building the 10-document subset.

`tests/test_end_to_end.py` then runs `build_fixture_room()` and:

- runs every gate against the room it produces;
- proves each gate is load-bearing by breaking the room in one specific way per gate and
  checking the *right* gate catches it;
- scores the sample output and confirms it reports exactly recall 75%, precision 75% and one
  false alarm (`DX-1`);
- rebuilds the room in a separate process under a different `PYTHONHASHSEED` and compares
  byte-for-byte, which is the only way to observe a set/dict-ordering dependence.

It is the closest thing this project has to a smoke test for the whole pipeline:

```bash
make test                                     # creates .venv on first use, runs everything
make test ARGS="tests/test_end_to_end.py -v"  # ARGS reaches pytest unchanged
make lint                                     # the same ruff check CI runs
```

CI runs the suite on Python 3.9, 3.11 and 3.13, and runs `ruff check .` as a
separate job. The rule set lives in `pyproject.toml` under `[tool.ruff.lint]`
rather than in either caller, so `make lint` and CI cannot disagree about what
passing means — and the comment there says why it is named rather than
inherited from whichever ruff version resolves.

Or drive pytest yourself, if you manage your own environment:

```bash
pip install -e ".[dev]"    # pytest is in the dev extra, not a base dependency
python3 -m pytest tests/test_end_to_end.py -v
```

To try the CLI surface by hand against a built copy of it:

```bash
python3 -m synthvdr.qa --room <built-room-dir> --strict
python3 -m synthvdr score fixtures/xs-room/tool-output-sample.json --room <built-room-dir>
```

---

## 5. Optional render toolchains

A room is markdown at heart, and a tool can be evaluated against clean markdown alone.
`/vdr-package` can additionally render:

- **DOCX**, via `synthvdr.render.docx` (requires the `python-docx` package — install with
  the `docx` extra: `pip install -e ".[docx]"`), into `<blind-tree>-docx/`.
- **PDF**, via `synthvdr/render/pdf.mjs`, a separate Node process (requires Node and a local
  Chrome/Chromium for Puppeteer), into `<blind-tree>-pdf/`.

Both are deterministic and idempotent: no RNG, no clock, no clock-derived filenames.

`/vdr-package` writes `_key/scanned.csv` — one `slot` per row — immediately before running
`pdf.mjs`, via `synthvdr.render.docx.write_scanned_csv`, and every page of each named
document is then re-rendered as an image-only page, slightly rotated, so a tool under test
has to OCR it. `default_scanned_count` sets how many documents (a quarter of the room's
markdown evidence, rounded, never zero while there is any), and `scanned_slots` chooses
which — drawn only from evidence documents, so OCR failure costs a planted finding rather
than a filler document nobody is scored on. Its absence is not an error; the render simply
produces live text throughout.

**The unit is the document, not the page.** The manifest briefly carried a `slot,page`
pair, and `pdf.mjs` read every row, stored it, and then honoured only page 1 — a row naming
page 3 parsed cleanly and did nothing, silently. Since a real data room scans whole
documents, and since mixing image pages with live-text pages in one PDF needs page-level
splicing this toolchain has no library for, the unit became the document and the dead column
went. A listed slot is scanned in full, page by page, each page taking its own skew from
`rotation_for(slot, page)`. `pdf.mjs` still reads a stale two-column manifest — the first
cell is the slot either way — and says so rather than ignoring the dead column.

That change is about geometry, not lost content, and the distinction is worth stating
because it is easy to assume otherwise: the old single `fullPage: true` screenshot did reach
every page, because Chrome flowed the one tall image across as many PDF pages as it needed.
A 2,629-word deed renders as 5 image pages under both the old and the new code, at
near-identical file size. What the old form did was rotate that document-tall image about
its own centre, so the sideways displacement grew with the document's length — roughly ±46px
at the extremes of that deed against ±10px per page now, and worse the longer the document,
clipping the margins at top and bottom. Every page also shared one angle, which is why
`rotation_for`'s page argument was effectively dead: it was only ever called with 1.

Two limits remain. `pdf.mjs` resolves the manifest to a literal `_key/scanned.csv` beside
the blind tree rather than reading `KEY_ROOT`, which is right for the one sanctioned layout
(`KEY_ROOT="_key"`) and finds nothing under any other. And there is no scanned page in the
DOCX tree at all: scanning is a PDF-only mechanism. A slot that matches no document under
the blind tree is reported and exits non-zero, never skipped in silence — an unmatched slot
renders that document as live text while the answer key believes it is a scan.

The two renderers share the rotation formula, and the ATX-heading rule, by direct port rather
than by importing across a language boundary — so they agree on the same `(slot, page)` pair
and on what a heading is. Both ports are pinned by cross-language tests that read the shipped
`pdf.mjs` and run it under `node` (`test_pdf_mjs_rotation_matches_python_exactly`,
`test_pdf_mjs_headings_match_python_exactly`); they SKIP, never silently pass, where `node`
is unavailable.

Both renderers are optional and **non-destructive** — they only create or overwrite the
files they are responsible for, and never delete a stale render — and neither is imported at
core build time, so a missing `python-docx` or missing Node/Chrome never blocks generating
or QA-checking a room.

**`gate_16_render_parity` checks filename parity only, in both directions — it never opens a
rendered file to compare content.** Whenever a render tree is present it confirms every
blind-tree document has a same-named render and every render has a same-named source, and it
reports the two directions distinctly: "3 sources with no render" (a renderer that has not
run) and "2 renders with no source" (a stale leftover) are different problems with different
fixes. It SKIPs loudly, never silently, when neither render tree exists. A render whose
*content* has drifted from its markdown source — a stale render left behind after the source
was edited — is not something this gate can see. Re-render after any source edit rather than
relying on gate 16 to catch it.

---

## 6. Limits

**The flagged-tree ownership marker is a safety interlock against misconfiguration, not a
security control.** `synthvdr.twin` (and, under their own marker names, `synthvdr.subset`
and `synthvdr.index_build`) refuses to delete a non-empty directory it did not create,
proven by a marker file it writes at that directory's root on every build. This stops a
mistyped `room.conf` path from silently destroying an unrelated directory; it does **not**
stop a deliberate attacker, since anyone able to plant the marker file could already delete
the directory themselves. The marker must be a **real file, never a symlink**, and is
matched by **exact, case-sensitive name** — a symlink at that name, or a same-named entry
differing only in case, does not count as the marker, on any filesystem.

**Renders are DOCX and PDF only — there is no XLSX render.** A room that plants evidence
inside a spreadsheet-shaped document (a register, a schedule) still ships that evidence as
markdown; `gate_16_render_parity` covers the two render trees above and has no XLSX
equivalent to check.

**The name-collision check reduces risk; it does not eliminate it.** `/vdr-scope`'s
WebSearch-based check, and `gate_14_unchecked_names`'s corpus-wide safety net behind it, can
only ever prove a *hit* — that a search returned something. **A search returning nothing is
not proof that no such company, brand, product, site or domain exists**: dormant companies,
recently deregistered entities and non-English-language markets will not reliably surface in
a web search. Gate 14's entity-suffix pattern can also both miss a genuine unchecked name
with an unlisted suffix, and — for any suffix whose own upper- or lower-case spelling happens
to coincide with an ordinary English or business word — flag ordinary prose that never named
a company at all. This project's own domain pack tripped exactly that case during the first
end-to-end run: a document heading containing "draft SPA", the standard M&A shorthand for a
Share Purchase Agreement, was read as the Italian "S.p.A." corporate suffix. It was fixed by
matching that one suffix in its exact canonical case only — which carries its own cost: the
list holds `SpA` undotted, so the dotted **`S.p.A.`**, which is how an Italian company most
often writes it, matches nothing and passes gate 14 unflagged. `synthvdr.names.ENTITY_SUFFIXES`
still carries a small, closed list of corporate-suffix tokens, and any future addition to it
should be checked against the same question before being matched case-insensitively. Treat a
clean check as lowered risk, never as a guarantee.

**Two narrowings of that pattern, and what each gives up.** `Limited` is the one suffix on
the list that is also an ordinary English adjective, and it was reading ordinary prose as
company names — "a Private Limited Company", "Independent Limited Assurance Report", and
Companies House's own "Private Company Limited by Shares". It is now declined in front of a
**closed list** of the words the adjective qualifies (`synthvdr.names._ADJECTIVAL_CONTINUATIONS`).
The list can only ever be one word behind the next ordinary phrase, which leaves a false
positive — deliberately, because the broader rule that would need no list ("any capitalised
word after the suffix ends the name") also swallows `<Unchecked Name> Limited Retirement
Benefits Scheme`, and a counterparty named only in that shape would pass in silence.
Separately, `abbreviates_a_cast_name` drops a candidate that is a trailing sub-phrase of a
name already on the cast list, reading `Ltd`/`Limited` and `Inc`/`Incorporated` as one
suffix — an org chart's box too narrow for "Helmswick Imaging Limited" holds "Imaging Ltd",
and masking cannot help because the full name is not in the text to remove. What that gives
up is a genuinely unchecked entity whose *whole* name is a tail of a checked one; the tail
is by construction the generic end of an invented name, and a one-word or bare-suffix cast
row, which would make the rule dangerous, is already rejected by `malformed_cast_entries`.
Note the direction — only a candidate no **longer** than the cast entry is excused, so the
rejected walk that let a cast "Holdings Limited" cover a different "Ashfell Trading Holdings
Limited" stays rejected.

**A suffix can still be read out of an ordinary English word in the other direction, and is
not fixed.** `Incorporated` is matched case-insensitively, so a capitalised phrase followed
by the ordinary participle — "Ashfell Advanced Materials Limited incorporated in 2004" —
reads the participle as the suffix and returns one over-long token. The `SpA` remedy (match
the canonical case only) does not transfer: it would lose `INCORPORATED` in an execution
block, and a per-suffix table of following words is a third mechanism this has not yet
earned. In the corpus sweep it is harmless — masking removes the registered name first — so
it surfaces only in `/vdr-scope`'s fact-sheet extraction, as a candidate with a trailing
"incorporated" that an author can see and correct.

**Coverage of invented names depends on the fact sheet declaring them.** The automatic scan
only catches a capitalised phrase ending in a corporate suffix, and the `## Cast` table only
covers people. A brand, product line, site or domain name reaches the check *only* through
the fact sheet's `## Invented names` table. Omit a row there and nothing downstream ever
checks that name — it passes Gate A unchecked, and gate 14 cannot catch it either, because
gate 14 only watches for corporate-suffix shapes.

**Determinism is a claim about structure, not about prose.** Every *structural* artefact this
project generates — the slot manifest, the section/subsection layout, the index, the flagged
tree's derivation from the blind tree, the subset selection — is required to be
byte-identical across repeated runs and across processes (no RNG, no clock, no bare
`hash()`; see `tests/test_end_to_end.py`'s cross-process, cross-`PYTHONHASHSEED` build
comparison). The *prose* inside a document that a `vdr-author` subagent actually writes is
not: two builds of the same room can legitimately produce different wording for the same
finding, and that variation is exactly what the QA gates — leakage sweeps, depth lint, the
carrier census, discoverability — are there to check, in place of a byte-for-byte comparison
that would be both impossible to satisfy and beside the point.

**Depth lint is a floor, not a quality signal.** Gate 10 catches accidental thinness (nobody
finished writing the document) and residual template artefacts (a placeholder marker left
behind). It does **not** catch deliberate padding: repeated boilerplate, YAML front matter,
fenced code blocks and long cross-reference lists all inflate the word count and all pass.
Two metric caveats apply to any band quoted against it — markdown table pipes tokenise as
words, so table-heavy documents read 15–25% longer than their prose, and CJK characters are
counted at half weight rather than as a single token.
