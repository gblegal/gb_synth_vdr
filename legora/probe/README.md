# Running the vdr-build probe

What it answers, in the order the pipeline needs them:

- Sub-agents can run the shipped script and write under `/workspace/scratch/`.
- Sub-agents run in parallel, and one survives a two-minute call.
- Sub-agents start blind: a passphrase that exists only in the
  orchestrator's conversation, sealed to disk as a hash, is not reproduced.
- Scratch outlives a conversation, and whether it is per project or shared.
- A room-shaped zip (204 files, five levels deep) round-trips inside the
  sandbox, downloads with its hash intact, and comes back in readable.
- Foreground calls of 75 and 330 seconds finish, and whether a cut-off call's
  script survives the cut.
- Sandbox speed against this laptop, and the libraries and tools present.

Every verdict comes from a file the script wrote. The agent's own
observations (its model, the timeout it got, the save command's name) are
printed under their own heading and never become a verdict.

## Upload

Zip `legora/probe/` so the zip's root holds `SKILL.md` and `scripts/`:

```bash
cd legora && rm -f ../dist/legora/vdr-probe-0.2.1.zip && mkdir -p ../dist/legora && zip -Xrq ../dist/legora/vdr-probe-0.2.1.zip probe -x '*.DS_Store'
```

Name the skill **vdr probe 0.2.1** in Legora, exactly: 0.2.0 went up as
"0.20.0" and its folder still reads `0-20-0`. The folder Legora creates is
named from the skill's name, and that name is the only record of which build
ran; the version is in `scripts/probe.txt` as `VERSION` too.

## The three conversations

The persistence and zip-in checks need more than one run. Do these in order.

**Run 1, project A, a new conversation.** Say "run the vdr probe". Read the
pasted report. Expect UNKNOWN for persistence and for the zip in. Accept the
four saves (the zip, its `.b64.txt`, the report, the checksum file).
Download the zip and check it:

```bash
shasum -a 256 ~/Downloads/probe-room-*.zip
```

It should equal the `ZIP_SHA256` line. Do not upload the zip back: Legora
unpacks it. The `.b64.txt` the run saved should already be in project A;
if the project listing does not show it, make one at home from the zip and
upload that:

```bash
base64 -i ~/Downloads/probe-room-*.zip -o ~/Downloads/probe-room.b64.txt
```

**Run 2, project A, a new conversation.** "Run the vdr probe" again. The
start line should now list run 1 as an earlier run, which means scratch
outlives a conversation. Step 8 should find the `.b64.txt` and the report
should say the tree came back intact.

**Run 3, project B, a new conversation.** Once more. If run 1 and run 2 are
listed as earlier runs, scratch is shared across projects, and a room built
in Legora must carry its project in its folder name. If none are listed,
scratch is per project.

## What run 1 established (17 September 2026, probe 0.1.0)

From the report and the agent's run log, both in Drive under
`Legora Skills/Runs/2026-09-17 synth-vdr probe 0.1.0 Run 1`:

- Four sub-agents ran the script and wrote to scratch, in parallel, one of
  them through a 120-second hold. None could name its own model.
- Two blind sub-agents answered NONE. Each knew only that "the primary
  agent dispatched me to run this probe".
- A 75-second call finished. A 330-second call was cut off at the tool's
  300,000 ms ceiling and the script finished anyway, 30 seconds later.
- The zip round-tripped, downloaded with its hash intact, and verified at
  home against its own manifest.
- CPU is about twice as slow as the laptop. File I/O is the problem: 204
  small files written, zipped, unzipped and hashed took over five minutes,
  three times running, and cut-off calls that were re-run raced each other
  and left FUSE placeholder files that `rm` could not clear. Hence 0.2.0's
  rule never to re-run a cut-off call, and its `io-bench` and `/tmp` checks.
- Python 3.13.5, pypdf 6.16.2 (so `extraction_mode="layout"` is available),
  python-docx 1.2.0, Node present, no PyYAML. Scratch is writable; the
  skill folder and the working directory are not.
- Web search works from the orchestrator. Saving is `suggest-save`, one
  acceptance per file.
- Run 2, a new conversation in the same project, saw no earlier runs.
  Scratch does not outlive a conversation, so every build session must
  restore the room from the project's zip first. Whether it is per project
  as well no longer matters.

## What run 1 established (17 September 2026, probe 0.2.0)

From the report, its checksum file and the agent's pasted reply, in Drive
under `Legora Skills/Runs/2026-09-17 synth-vdr probe 0.2.0 Run 1`. The
skill was uploaded as "vdr-synth-probe 0.20.0", so its folder reads
`0-20-0`; the report's own version line says 0.2.0, which is the build that
ran. Run `20260917-072349-15ca`, project `vdr-synth-probe 0.2.0`.

- Everything 0.1.0 found held. Four sub-agents wrote in parallel, one
  through a 120-second hold, all four starting in the skill folder. Two
  blind sub-agents answered NONE. The 75-second call finished; the
  330-second call was cut off at the tool's ceiling and finished anyway.
  The zip round-tripped inside the sandbox: 204 files, five deep, 61,132
  bytes.
- The shell tool's setting is `timeoutMs`, schema maximum 300,000, default
  60,000. The orchestrator cannot name its model: nothing in its context
  carries an identifier. So the dated identifier 0.1.0's orchestrator wrote
  was the skill's own example handed back.
- The file system is the cost, not the CPU. The fixed workload took 1.58 s
  against 0.58 s on the laptop, under three times slower. A hundred small
  files in scratch: 525 ms per write, 208 ms per read, 0.9 s to list, 14 s
  to delete. The same hundred in `/tmp`: 0.1 ms per write and per read.
- `/tmp` is writable and fast but does not outlive a call: a marker written
  in one call was gone four seconds later in the next. So a build can work
  in `/tmp` within a call, but must bring its state in from scratch at the
  start of every call and put it back at the end. One zip each way is the
  cheap shape; a file per document in scratch is not.
- The zip-out call was cut off at 300 s and `zip/out.json` appeared about
  four minutes later, outside the three 30-second waits the skill allows.
  0.2.1 should wait longer on that step, or build the tree in `/tmp` and
  write only the zip to scratch.
- Zip in: NO, but not as the skill meant to test it. The zip was saved to
  the project and accepted mid-run, before step 8, so step 8 found it. The
  project listing showed 61,132 bytes; the script's own read found 0 bytes
  and "not a zip". Whether a zip uploaded from outside reads any better is
  what run 2 is for. Run 2 can use the intact 0.1.0 zip from Drive: the
  check verifies against the manifest inside the zip, not against the run.
- The project download did not come down as the zip. It came as
  `legora-download-20260917T084641.zip`, holding the tree unpacked: 203 of
  204 files, folders intact five levels deep, all 202 documents matching
  the manifest, and `room.conf` missing. So a zip saved to a project is
  unpacked by Legora on the way in or on the way out, and one file was
  lost. Nothing said so; the manifest did.
- Environment as before: Python 3.13.5, pypdf 6.16.2, python-docx 1.2.0,
  Node v24.7.0 without puppeteer, no PyYAML. Scratch and `/tmp` writable;
  the skill folder and the working directory not. Web search works from
  the orchestrator. Saving is `suggest-save`, mode create, one acceptance
  per file.
- Persistence: UNKNOWN on a first run, as expected. 0.1.0's run 2 already
  showed scratch does not outlive a conversation.

## What was tried by hand (17 September 2026, after run 1)

Three uploads into the project, in a new conversation, with no probe
involved. None of these is a script's verdict, so they are recorded here
and on the environment page as Greg's observations, not in a report.

- The intact 0.1.0 zip, uploaded from outside: Legora unpacked it on the
  way in. The project held the tree, not the archive. So the zip that read
  as 0 bytes on run 1 was not a mid-run accident; a zip is never stored as
  a zip.
- The same bytes renamed `.txt`: refused, "contents do not match file
  type". Legora checks the content against the extension.
- The zip as base64 text, `probe-room.b64.txt`, 81,512 bytes: accepted and
  listed. But 0.2.0's step 8 looks only for a name ending `.zip`, so the
  run did not pick it up.

Hence 0.2.1: `zip-out` writes the base64 text beside the zip and builds
the tree under `/tmp`, `zip-in` reads either form and decodes the text
itself, and step 8 looks for the `.b64.txt` first.

## What run 2 established (17 September 2026, probe 0.2.0, project A)

From the report and its checksum file, in Drive under `Legora Skills/Runs/
2026-09-17 synth-vdr probe 0.2.0 Run 2 w zip`. Run `20260917-134332-64a6`,
a new conversation in the same project, with `probe-room.b64.txt` uploaded
beforehand.

- **The base64 text in the project reads back as the zip, byte for byte.**
  The orchestrator, told by step 8 to look for a `.zip`, found the `.b64.txt`
  instead, decoded it itself into scratch, and ran `zip-in` on the result.
  The decoded zip was 61,132 bytes with sha256 `4a2e5226…`, the hash of the
  0.1.0 zip it was made from; all 203 manifest entries matched, none
  missing, none wrong, depth 5. The verdict is from `zip/in.json`, so it
  counts, though the decoding step was the agent's own and 0.2.1 makes it
  the script's.
- No earlier run visible at start: the second confirmation that scratch
  does not outlive a conversation.
- Everything else as run 1: four sub-agents in parallel, two blind, the 75
  and 330-second holds finished, the shell ceiling 300 s, the workload
  1.295 s against 0.58 s at home, scratch 591 ms per write and 26 s to
  delete a hundred files, `/tmp` gone by the next call.
- The model note reads `claude-sonnet-4-5-20250929`, which is the skill's
  own example at `SKILL.md`. Handed back again, as on 0.1.0.

So the plan's question 4 is answered yes, and Tier B is built on the
zip-in-scratch shape with the base64 text as the project's copy.

## What run 2 established (18 September 2026, probe 0.2.1, project A)

From the report, its checksum file and the project download, in Drive under
`Legora Skills/Runs/2016-09-19 probe 0.2.1 Run 2` (the folder's date is a
slip; the run is 18 September). Run `20260918-092034-e49c`, a new
conversation in the same project. Before it, run 1 of 0.2.1
(`20260918-085959-f262`) had run in that project, its zip had been
downloaded, encoded at home with `base64 -i … -o probe-room.b64.txt` and
uploaded.

- **The script found and decoded the text itself.** Step 8 pointed
  `zip-in` at `probe-room.b64.txt` in the project. It read 81,513 bytes,
  recognised the form as base64, decoded it to a 61,132-byte zip with
  sha256 `7a9b872c…`, and verified all 203 manifest entries, depth 5. That
  hash is the run 1 zip's, checked at home. No agent improvisation this
  time; the improvisation of 0.2.0's run 2 is now the script's job.
- The extra byte is the trailing newline macOS `base64` writes. The
  decoder strips whitespace, so it did no harm.
- **The saved zip came down as a zip.** The project download,
  `legora-files-2026-09-18.zip`, held the run's own
  `probe-room-….zip` intact at the printed hash, beside the `.b64.txt`,
  the report and the checksum file. On 0.2.0 run 1 the download
  (`legora-download-<stamp>.zip`) came unpacked with a file missing. The
  names differ, so this may be a different export path; one observation
  each way, not a conclusion.
- **The model note is honest this time:** "exact identifier not known to
  me; running as Legora Agent on a Claude Sonnet family model". Same
  question as before, so the earlier reading stands: a dated identifier
  in a note is a claim, not a reading.
- Everything else as before: four sub-agents in parallel, two blind, the
  75 and 330-second holds finished (only the 330 was cut off), no earlier
  run visible at start, workload 1.244 s, scratch 555 ms per write,
  `/tmp` gone by the next call. The zip-out step logged no cut-off.

## Outstanding, 18 September 2026

Done: the 0.2.1 confirmation run, the Tier B decision, and the environment
page entries. Left:

1. **Run 3, project B, a new conversation.** Whether scratch is per
   project or shared. Low priority: scratch does not outlive a
   conversation, so a build restores from the project either way.
2. **The size ceiling.** A 200-document room zips to a few megabytes and
   base64 adds a third. Whether Legora accepts a file that size, and reads
   it back whole, is untested; the probe's room is 61 KB. The first real
   build in Legora will answer it, so `room restore` must verify the
   manifest and refuse a short read.

## Afterwards

Each fact goes on `gb-docclass/docs/legora-environment.md`, dated and saying
which run established it, written about Legora rather than about this skill.
The decision on Tier B of `docs/legora-bundle-plan.md` is made from those
entries, not from the agent's replies.
