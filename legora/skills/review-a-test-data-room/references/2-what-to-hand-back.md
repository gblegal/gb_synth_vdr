# What to hand back

A program reads the report before a person does. It finds each issue by its
heading, the issue's severity by the first severity word in it, and its
documents by their paths. Anything outside this shape is either lost or,
worse, read as something it is not.

## The shape

- A title line: `# Review of` and the project's name.
- `Model:` and `Skill:`, as the skill describes, each on its own line with a
  blank line between them, so that Word keeps them apart.
- One issue per `##` heading. The heading names the issue in one line. No
  other headings inside an issue: use plain paragraphs and lists.
- The first line of each issue's body is `Severity:` and one word: critical,
  high, medium or low. Put nothing before it, because the first of those four
  words anywhere in the issue is the one that counts.
- Then what the issue is and why it matters, in a paragraph or two: the
  clause, the figure, the date, and which document shows each.
- Then `Documents:` and a list of every document the issue rests on, one per
  line, each a backticked path starting at the numbered section folder, with
  its extension, exactly as the file is named in this project. Never a path
  starting `/workspace/`, and never the file name alone.
- One issue, one heading. If two documents show one problem, that is one
  issue citing both, even when they sit in different sections. If one
  document shows two problems, those are two issues, each citing it.
- `# Files read` comes last, with one `#`, listing every document you opened,
  one backticked path per line, in the same form.
- No tables anywhere in the report.

## Severity

- **critical**: goes to whether the deal happens, or at what price or on what
  structure. A buyer would walk away, re-price, or make it a condition of
  signing.
- **high**: a material exposure, usually one you can put a number on, that
  needs its own contractual protection: a specific indemnity, a price
  adjustment or a condition to completion.
- **medium**: a real defect to fix or cover by completion: a warranty, a
  disclosure against it, or a completion deliverable.
- **low**: housekeeping. A register, filing or record out of step, to tidy
  after completion or raise as an enquiry.

## A worked example

The shape, with the words in angle brackets standing for your own. The paths
are invented, numbered past any real document: it says nothing about what is
in any room.

```markdown
# Review of <project name>

Model: not known

Skill: <the line beneath the skill's heading that begins "Built from">

## <One line naming the first issue>

Severity: high

<What the issue is and why it matters, in a paragraph or two: the clause,
the figure, the date, and which document shows each.>

Documents:
- `09_employment/9.2_policies/9.2.99_policies-99.md`
- `12_insurance/12.1_schedule/12.1.99_schedule-99.md`

## <One line naming the second issue>

Severity: low

<What the issue is and why it matters.>

Documents:
- `16_operations-quality/16.1_qms/16.1.99_qms-99.md`

# Files read

- `09_employment/9.2_policies/9.2.99_policies-99.md`
- `12_insurance/12.1_schedule/12.1.99_schedule-99.md`
- `16_operations-quality/16.1_qms/16.1.99_qms-99.md`
```

On a room of PDFs the same paths end `.pdf`, and on a room of Word files
`.docx`. Cite them as they are named.
