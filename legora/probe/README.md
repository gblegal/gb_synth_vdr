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
cd legora && rm -f ../dist/legora/vdr-probe-0.2.0.zip && mkdir -p ../dist/legora && zip -Xrq ../dist/legora/vdr-probe-0.2.0.zip probe -x '*.DS_Store'
```

Name the skill **vdr probe 0.2.0** in Legora. The folder Legora creates is
named from the skill's name, and that name is the only record of which build
ran; the version is in `scripts/probe.txt` as `VERSION` too.

## The three conversations

The persistence and zip-in checks need more than one run. Do these in order.

**Run 1, project A, a new conversation.** Say "run the vdr probe". Read the
pasted report. Expect UNKNOWN for persistence and for the zip in. Accept the
three saves (the zip, the report, the checksum file). Download the zip and
check it:

```bash
shasum -a 256 ~/Downloads/probe-room-*.zip
```

It should equal the `ZIP_SHA256` line. Upload that same zip back into
project A.

**Run 2, project A, a new conversation.** "Run the vdr probe" again. The
start line should now list run 1 as an earlier run, which means scratch
outlives a conversation. Step 8 should find the zip and the report should
say the tree came back intact.

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

## Afterwards

Each fact goes on `gb-docclass/docs/legora-environment.md`, dated and saying
which run established it, written about Legora rather than about this skill.
The decision on Tier B of `docs/legora-bundle-plan.md` is made from those
entries, not from the agent's replies.
