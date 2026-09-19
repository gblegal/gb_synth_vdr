# Known issues

Defects and costs that are real, understood, and deliberately not fixed yet. Each entry says what
was measured, why it was left, and what fixing it would cost — so the next person decides with the
evidence rather than rediscovering it.

An entry leaves this file when it is fixed, or when a decision is taken that it never will be.

---

## 1. A degraded scan tree is roughly 16× the size of a pristine one

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
degraded render is opt-in rather than always-on: `/vdr-package` builds it only for a room whose
`room.conf` declares `SCAN_PROFILE="office"`, and the figures above are stated in the skill's own
Step 3, where whoever runs it meets them before they run it.
