# Known issues

Defects and costs that are real, understood, and deliberately not fixed yet. Each entry says what
was measured, why it was left, and what fixing it would cost — so the next person decides with the
evidence rather than rediscovering it.

An entry leaves this file when it is fixed, or when a decision is taken that it never will be.

---

## 1. The scanned render drops a line at every page seam

**Found:** 18 September 2026, while measuring the `office` scan profile against a pristine control.

`renderScannedDocument` in `synthvdr/render/pdf.mjs` lays a document out at A4, then screenshots it
one page at a time by clipping a fixed `1123px` window. There is no CSS pagination, so the clip
falls wherever it falls — and a line of text straddling the boundary is cut through its glyphs,
leaving the top half on one page and the bottom half on the next. OCR reads neither.

**Measured.** On slot `05_commercial/5.1_customer-contracts/5.1.1_customer-contracts-01`, the
**pristine** scan — no degradation at all, just the existing sub-degree rotation — scores 0.8824
token survival against its own source markdown. The missing 0.12 is entirely page seams.

**Why it was left.** It affects both scan profiles equally, so it cancels out of any
pristine-against-degraded comparison, which is what the profile exists to support. It also predates
the profile work by some margin. Most importantly, fixing it changes the pixel layout of every
scanned page, so **every existing rendered tree's bytes change** — including rooms already frozen
at a tag with a `content_hash` published against them.

**What fixing it looks like.** Paginate the source properly — `page-break-inside: avoid` on block
elements, or lay the document out with real CSS paged media — so the clip lands between lines
rather than through them. Then re-render, and accept that any room wanting the fix must be
re-rendered and re-frozen deliberately.

**What it costs to leave.** A scanned document is slightly harder to read than a real scan of the
same document would be, in a way that has nothing to do with scan quality. Any absolute claim about
OCR accuracy on a scanned tree is therefore pessimistic by roughly this much. Relative claims —
profile A against profile B — are unaffected.

---

## 2. A degraded scan tree is roughly 16× the size of a pristine one

**Found:** 18 September 2026, in the final review of the `office` scan profile.

**Measured.** A four-page scanned document: **564KB** under `--scan-profile none`, **9.05MB** under
`--scan-profile office`.

**Why.** It is not the JPEG artefacts — those make files smaller. Applying any CSS `filter:` to the
page image forces Chrome to composite and resample it, from `794px` wide to `2488px`, and then
embed the result losslessly as Flate RGB plus a full 8-bit alpha mask. The profile that exists to
*add* compression artefacts produces a substantially larger PDF than the pristine one.

**Projected.** On an 800-document room with 37 scanned slots across 203 pages — Project
Frithcombe's shape — that is roughly **460MB** of degraded scans against about **29MB** pristine,
in a tree that gets frozen and distributed.

**Why it was left.** It is inherent to the CSS-filter approach the design chose, and that choice
was deliberate: the alternative is a real image pipeline (Pillow, or Augraphy as
`synthvdr/corrupt.py`'s module docstring anticipated), which means a new dependency and a
Python/Node boundary to cross. That is the fax-quality tier, explicitly out of scope in
`docs/superpowers/specs/2026-09-18-corrupted-scan-profile-design.md` §3 and §12.

**What fixing it looks like.** Either re-encode the composited page as JPEG before embedding it
(cheap, stays inside the current approach, needs the same PDF-bytes surgery the metadata
normalisation already does), or move degradation into a real image pipeline and get both smaller
files and the fax tier at once.

**What it costs to leave.** Any room building a degraded tree needs the disk for it, and whoever
distributes that room needs to know before they build rather than after. This is the reason the
degraded render should be opt-in rather than always-on.
