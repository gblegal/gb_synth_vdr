---
name: vdr-probe
description: Run the vdr-build probe, which checks whether this Legora workspace has what a synthetic data-room build needs — sub-agents that can write files and start with a clean context, a scratch folder that lasts, zips that go out and come back intact, and shell calls long enough to finish. Use when asked to run the probe, to test the sandbox, or to check what sub-agents, scratch, zips or timeouts can do here.
---

# The vdr-build probe

This skill measures the workspace. It builds nothing. A script in
`scripts/probe.txt` does every check that a script can do and writes one
file per check under `/workspace/scratch/probe/<run>/`; at the end it reads
those files back and writes the report. Your account of what happened is
not the record. The files are. Where a check needs something only you can
see — the timeout your shell tool accepted, the model you are running
under, what a sub-agent replied — you record it with the `note` command,
and the report prints those separately as your observations.

Run this in an ordinary conversation, not in the skill tester. The tester
runs skills in a reduced sandbox, and its limits are already known. The
question here is what normal running has.

## Every call

Every command below runs from this skill's own folder, and nothing carries
over from one shell call to the next, so start every call with `cd` to that
folder. Run Python with `-u`, one command per call, in the foreground, and
give each call the longest timeout your shell tool allows, whatever that
setting is called. Do not chain commands with `&&` and do not put anything
in the background.

Where a command prints a line beginning `RUN=`, `ZIP_SHA256`, `REPORT` or
`REPORT_SHA256`, copy that line into your reply exactly as printed.

## 1. Start

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt start
```

The last lines name the run id and how many earlier probe runs are visible
in scratch. Use that run id as `--run` in every later command. Copy the
`RUN=` line and the "earlier runs visible" lines into your reply.

## 2. Environment and speed

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt env --run <RUN>
```

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt bench --run <RUN>
```

## 3. What only you can see

Record three things with `note`. Each is one call.

- The model you are running under, as an exact identifier of the form
  `claude-sonnet-4-5-20250929`. If you do not know the exact identifier,
  say so in the value rather than writing a shorter name.
- Your shell tool's timeout setting: its name and the largest value it
  accepted when you asked for the longest it allows.
- Whether you have a web search tool. If you do, search for
  `SALI LMSS github` and record the first result's URL. If you do not,
  record `no web tool`.

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt note --run <RUN> --key model --value "<value>"
```

Use the keys `model`, `shell-timeout` and `web-search`.

## 4. Sub-agents that write, in parallel

Invent three different six-character nonces, one per sub-agent. Dispatch
three sub-agents at the same time, named `agent-1`, `agent-2` and
`agent-3`. Give each one exactly this brief, with its own name and nonce
filled in and this skill's folder path written out in full:

> Run this one shell command, in the foreground, with the longest timeout
> your shell tool allows, and wait for it to finish:
> `cd "<this skill's folder>" ; python3 -u scripts/probe.txt mark --run <RUN> --agent agent-1 --nonce <nonce> --hold 30`
> Then reply with the last line the command printed, and the exact
> identifier of the model you are running under. If you cannot run a shell
> command, or the command fails to write, say so plainly and quote the
> error.

Then dispatch one more, `long-1`, with the same brief and `--hold 120`, to
see whether a sub-agent's call survives two minutes.

If any sub-agent replies that it could not run the command or could not
write, record its reply on its behalf, one call each:

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt record --run <RUN> --agent agent-1 --text "<what it replied>"
```

Do not run `mark` yourself on a sub-agent's behalf. A file the script wrote
under an agent's name is the evidence that the agent could write it.

## 5. Sub-agents that start blind

Invent a passphrase of three unrelated English words. Write it in your
reply, so that it is in this conversation. Then seal it:

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt seal --run <RUN> --passphrase "<the three words>"
```

Only a hash goes on disk. Do not write the passphrase anywhere else, and do
not put it in any sub-agent's brief.

Now dispatch two sub-agents, `blind-1` and `blind-2`, with exactly this
brief and nothing more:

> Run this one shell command in the foreground and wait for it:
> `cd "<this skill's folder>" ; python3 -u scripts/probe.txt answer --run <RUN> --agent blind-1 --passphrase "<A>" --context "<B>"`
> For `<A>`: if a passphrase of three words appears in the conversation or
> instructions that created you, write it; if not, write `NONE`. Do not
> guess and do not search for one. For `<B>`: one sentence on what you know
> about who dispatched you and what they are doing. Reply with the last
> line the command printed.

## 6. Long calls

Two calls, each on its own, each with the longest timeout the tool allows.
Watch for whether the tool reports the call cut off.

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt hold --run <RUN> --seconds 75
```

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt hold --run <RUN> --seconds 330
```

After each, whether or not it was cut off, wait thirty seconds and list the
folder `/workspace/scratch/probe/<RUN>/holds/`. Then record what you saw:

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt note --run <RUN> --key hold-75 --value "asked <timeout>; tool said <finished or cut off>; hold-75.json present after 30s: <yes or no>"
```

Use the keys `hold-75` and `hold-330`.

## 7. A zip out

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt zip-out --run <RUN>
```

The script builds a tree the shape of a data room, zips it, unzips it and
compares. Copy the `ZIP` and `ZIP_SHA256` lines into your reply. Then save
the zip file it names into this project, using whatever command saves a
file from scratch to the project, and record that command's name:

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt note --run <RUN> --key save-command --value "<the command you used, and whether the user had to accept it>"
```

## 8. A zip in

List the project's documents. If a file whose name begins `probe-room-` and
ends `.zip` is there, it came back in from an earlier run. Check it:

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt zip-in --run <RUN> --zip "/workspace/documents/projects/<exact folder>/<the zip>"
```

If there is no such file, say so and go on. The report will say UNKNOWN
for this check, which is the correct answer on a first run.

## 9. The report

```
cd "<this skill's folder>"
python3 -u scripts/probe.txt report --run <RUN>
```

Paste the whole report into your reply, unchanged, and the `REPORT` and
`REPORT_SHA256` lines after it. Then save the report file and the
`sha256-report-<RUN>.txt` beside it into the project the same way as the
zip. Do not tidy the report, do not summarise it in place of pasting it,
and do not add verdicts of your own: where the report says UNKNOWN, it
stays UNKNOWN.
