---
name: vdr-scope
description: Scope a new synthetic M&A data room — a very light-touch interview, an invented deal fact sheet, room.conf, the slot index, and a name-collision check that blocks Gate A on any unresolved collision. Run this first, before any other /vdr- command.
---

# Scope a synthetic data room

Produces the room's foundations — the fact sheet, `room.conf`, the slot index, and the
name-collision record — and stops at **Gate A**. Nothing after this skill runs until the
user signs Gate A off.

## 0. Check whether this room is already scoped

Before touching anything, check the current directory for `room.conf`, `_key/fact-sheet.md`
and `_key/name-check.md`. If any of them already exist, **stop and tell the user exactly
what you found** — the room codename, and (if `_key/name-check.md` exists) how many names it
already has verdicts for — rather than silently proceeding into step 1. A fresh run of this
skill invents a brand-new deal from scratch, and writing over an already-scoped room
overwrites a fact sheet that may already be signed off at Gate A, and a name-check record
whose verdicts each cost a real WebSearch — work a silent re-run would throw away with
nothing to show for it. Only continue past an existing room after the user explicitly says
to rescope or start over; otherwise stop here and ask what they want instead (e.g. resume at
`/vdr-findings`, since Gate A may already be closed).

## 1. One question, then get on with it

This is not a questionnaire. Ask the user roughly one thing:

> "Do you have a specific sector or scenario in mind, or shall I make one up?"

In the same message, mention — as a default they can override in passing, not as a second
formal question — that you'll build a mid-size room (`M`, ~200 documents) unless they say
otherwise, and that the other sizes are `XS` 40 / `S` 60 / `L` 800 / `XL` 2,000+. A single
line of reply, or no reply beyond "make one up", is enough to proceed. Invent everything
else yourself: deal structure, entity tree, cast, sites, financials, dates, section budget.

If the user hands you a `room.conf` to seed a repeatable build (distinct from step 0's case
of one already sitting in the working directory from a prior run), skip the interview
entirely and scope from the values it supplies.

## 2. Invent the deal and write the fact sheet

Write `_key/fact-sheet.md`. Every document in the room reconciles to this one file — nothing
anywhere else invents a figure, a name, or a date that is not declared here first. It needs:

- A narrative: deal structure, entity tree, sites, headline financials, key dates.
- **`## Cast`** — a `| Name | Role |` table. Every named individual in the room appears here.
  These rows are always treated as people, automatically.
- **`## Invented names`** — a `| Name | Kind |` table, Kind one of `entity`, `brand`,
  `product`, `site`, or `domain`. **This table is load-bearing, not cosmetic.**
  `synthvdr.namecheck.extract_candidates` reads it as one of three sources of names to check,
  and it is the *only* source that sees a brand, product, site or domain name: the automatic
  scan (`entity_tokens`) only catches a capitalised phrase ending in a corporate suffix
  (Ltd, GmbH, Inc, ...), and the `## Cast` table only covers people. If you invent a brand
  ("Solmark"), a product line ("Vantiq Edge"), a site name, or a domain and do not add a row
  here, nothing downstream ever checks it for a collision — it sails straight through Gate A
  unchecked, and gate 14 later in the harness cannot catch it either, since gate 14 only
  watches for corporate-suffix shapes. Declare every distinctive invented name of any of
  these five kinds here, even ones that also happen to carry a corporate suffix (declaring
  them is harmless; a name reaching the fact sheet only through the automatic scan and never
  declared is the actual gap this table exists to close).
  A generic placeholder ("Site A", "NewCo") does not need a row — there is nothing to collide
  with. Everything else you would type into a search bar to check does.
  Do not declare a `## Cast` name here with a non-person Kind: a name that is both a cast
  member and a declared entity/brand/product/site/domain is self-contradictory, and
  `extract_candidates` raises rather than guessing which is right. (Kind `person` may also be
  declared here — that is reserved for the rare case where a cast member's exact name doubles
  as an invented entity name on purpose; it is not something you need for an ordinary room.)
- **`## Canonical figures`** — a `| Key | Value | Superseded |` table. Every figure any
  document states must trace back to a row here; when a figure is corrected mid-build, move
  the old value into `Superseded` (semicolon-separated if there is more than one) rather than
  overwriting it — write `—` when there is nothing superseded yet. Gate 13 greps every
  canonical value into the room and every superseded value out of it; this is the
  anti-thin-filler gate, and a fact sheet that never gets this table right makes every later
  document a guess instead of a fact.

## 3. Check every invented name

Run the extractor over the fact sheet exactly as written — it reads all three sources (the
automatic corporate-suffix scan, `## Cast`, `## Invented names`) and applies the declared
table's precedence:

```bash
python3 -c "
from pathlib import Path
from synthvdr.namecheck import extract_candidates
text = Path('_key/fact-sheet.md').read_text(encoding='utf-8')
for c in extract_candidates(text):
    print(c.kind, c.text)
"
```

This raises `NameCheckError` — and must be fixed before you go any further, not caught and
ignored — if `## Invented names` declares a blank or unrecognised Kind, or declares a name
with a non-person Kind that also appears in `## Cast`. Both mean the fact sheet contradicts
itself; resolve it by editing `_key/fact-sheet.md`, then re-run the extractor.

For each candidate, the test differs by Kind:

- **Not a person** (`entity`, `brand`, `product`, `site`, `domain`): a **collision** check —
  does a real company, brand, product, site or domain of this name already exist? Run one
  WebSearch per distinctive name.
- **`person`**: a **notability** check only — is this a public figure? Never a collision
  check: every plausible surname exists somewhere, and treating an ordinary name as a hit
  would make the check impossible to pass.

Record each result as `clear`, `collision`, or `ambiguous`. If a name comes back `collision`,
invent a replacement and re-check the replacement — do not keep the colliding name. An
`ambiguous` result is not something to resolve yourself: surface it to the user with whatever
you found, and let them decide whether to accept it or ask for a different name.

If WebSearch is unavailable in this session, say so to the user in plain terms and record
every affected name's verdict as `unchecked` with a note explaining why — never leave the
record silent about a name that was never actually checked, and never present an unchecked
name as if it were clear.

Write every verdict — one row per name, including any that came from an earlier round in
this same scoping session — to `_key/name-check.md` using the module that owns this record
format, with today's real date:

```python
from datetime import date
from pathlib import Path
from synthvdr.namecheck import Verdict, render_name_check_md

verdicts = [
    Verdict(text="Ashfell Advanced Materials Limited", kind="entity", verdict="clear",
            checked=date.today().isoformat(), note=""),
    # ... one row per candidate from step 3
]
Path("_key/name-check.md").write_text(render_name_check_md(verdicts, room_codename))
```

Use `render_name_check_md` rather than hand-writing the table: it is the exact format
`synthvdr.names.cast_list` parses back out later (gate 14 depends on that round trip), and it
raises rather than silently corrupting the record if a name cannot survive being written into
a pipe-table cell and read back unchanged.

Tell the user the check's real limit, in these terms or close to them: a search returning
nothing is not proof a name doesn't exist — dormant companies and non-English markets won't
surface. This reduces collision risk; it does not eliminate it.

## 4. Generate the structure

```python
from pathlib import Path
from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.slots import SIZE_PRESETS, build_slot_manifest, write_anchors_csv
from synthvdr.index_build import write_index_sources, render_index

pack = load_domain(DEFAULT_DOMAIN_ROOT)
preset = SIZE_PRESETS["M"]  # or whichever size the user picked in step 1
slots = build_slot_manifest(pack, preset)
write_anchors_csv(slots, Path("_key/anchors.csv"))
write_index_sources(slots, pack, Path("_key/index-src"))
Path("index.md").write_text(render_index(Path("_key/index-src")))

print(len(slots))                      # -> INDEX_TOTAL / BLIND_TOTAL / FLAGGED_TOTAL
print(" ".join(pack.section_dirs()))   # -> SECTION_DIRS, space-separated
```

`len(slots)` equals `preset.docs` — every slot the room will ever hold is already accounted
for at this stage, even though no document body has been authored yet.

## 5. Write `room.conf`

Every constant the harness reads comes from here — no tool hardcodes a room fact. Use the
values step 4 just printed, plus:

- `ROOM_CODENAME` — e.g. `"Project Ashfell"`. Room-wide, used in generated headers.
- `BLIND_TREE="data-room"`, `FLAGGED_TREE="_key/flagged"`, `KEY_ROOT="_key"` — this exact
  layout is the one the loader accepts by construction (`FLAGGED_TREE` nested under
  `KEY_ROOT` is the one sanctioned overlap); do not invent a different arrangement.
- `FLAG_STRING_1` and `FLAG_STRING_2` — the two literal strings gate 3 sweeps the blind room
  for, so they must never occur there by coincidence. `"Key diligence points"` and `"DD flag"`
  are the strings this project's own fixtures use and are a safe choice unless the domain
  already uses that exact phrasing incidentally.
- `FINDING_PREFIXES` — one `|`-separated, uppercase-letter-starting token **per workstream, in
  the domain pack's own order** (`pack.workstreams()` — the same order `sections.yaml` and
  `finding-archetypes.yaml` both declare and `load_domain` requires them to agree on), e.g.
  `CORP|FIN|TAX|FING|COMM|IP|IT|PROP|EMPL|REG|ENV|INS|PEN|DATA|LIT|OPS|MGMT|TXN|ESG|JV` for the
  shipped M&A domain pack — covering **every** workstream, not just the ones you expect to
  carry a finding — `/vdr-findings` has not run yet, so you do not yet know which workstreams
  will. A workstream left out here is a workstream whose finding IDs the leakage gate cannot
  recognise if one ever leaked into the blind room. **The order is load-bearing, not
  cosmetic**: `/vdr-build` pairs this list positionally with `pack.workstreams()` to work out
  which prefix a mid-authoring discovery gets — get the token count right but the order wrong
  and every discovered finding is silently numbered under the wrong workstream's prefix.
- `EXPECTED_KDP_CARRIERS=0` — no findings exist yet; `/vdr-findings` sets the real number.
- `INDEX_TOTAL`, `BLIND_TOTAL`, `FLAGGED_TOTAL` — all equal to `len(slots)` from step 4.
- `SECTION_DIRS` — the space-separated `pack.section_dirs()` string from step 4.

Then copy the harness into the room so it is self-contained: copy
`${CLAUDE_PLUGIN_ROOT}/tools/check.sh` (this plugin's own harness entry point) to
`<room>/tools/check.sh`. It is a thin wrapper around `python3 -m synthvdr.qa`; nothing else
in `tools/` is required.

## Gate A — stop here

Show the user the fact sheet and the `_key/name-check.md` table together. Do not close Gate A
while any name's verdict is `collision` — that is a hard block: regenerate the name and
re-check it, there is no sign-off that waives a collision. An `ambiguous` or `unchecked`
verdict does not block automatically, but must be shown to the user by name with its note,
and closing Gate A with either still outstanding requires the user's explicit acknowledgement
that they are accepting that risk, recorded in the conversation.

Ask for sign-off on the fact sheet and the name check in as many words. Only `/vdr-findings`
runs next — nothing else in this plugin authors a single document before Gate A closes.
