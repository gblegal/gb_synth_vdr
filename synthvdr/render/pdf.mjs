#!/usr/bin/env node
// Optional PDF render. Never invoked at core-build time.
//
// Usage: node synthvdr/render/pdf.mjs --src <blind-tree> --out <out-tree>
//
// Renders every markdown file under --src to a PDF twin under --out,
// mirroring --src's relative layout. If a `scanned.csv` file exists at
// `<parent of --src>/_key/scanned.csv`, listing one `slot` per row (the
// source file's path relative to --src, without extension and with forward
// slashes), the matching document is rendered as image-only pages — a
// screenshot of each page, slightly rotated — instead of live text, so a
// tool under test has to OCR it. Absent `scanned.csv` is not an error: it
// just means nothing in this run is scanned.
//
// THE UNIT IS THE DOCUMENT, NOT THE PAGE, and that is a correction. The
// manifest used to carry a `slot,page` pair, and this renderer read every
// row, stored it, and then asked only `scannedPages.has(1)` — so a row
// naming page 3 parsed cleanly and did nothing at all, with no warning.
// A real data room scans whole documents anyway (nobody scans page 3 of a
// deed), and mixing image pages with live-text pages inside one PDF needs
// page-level splicing this toolchain has no library for — so the unit
// became the document and the dead column went.
//
// What the old page-1 branch did NOT do, despite appearances, is lose
// content: it screenshotted the whole document with `fullPage: true` into a
// single tall image, and Chrome then flowed that image across as many PDF
// pages as it needed. A 2,629-word deed came out of both the old and the new
// renderer as 5 image pages of near-identical total size. What it did do was
// rotate that one document-tall image about ITS OWN centre, so the sideways
// displacement grew with the length of the document — about +/-46px at the
// extremes of that 5-page deed against +/-10px per page now, and worse the
// longer the document, clipping the margins at the top and bottom. And every
// page carried the SAME skew, which is why `rotationFor`'s page argument was
// effectively dead: it was only ever called with 1.
//
// Deterministic and idempotent: rotation angles come from `rotationFor`,
// a direct JS port of `synthvdr.render.docx.rotation_for` (same sha256
// formula, same +/-0.4-1.1 degree range) so the two renderers agree on the
// same (slot, page) pair without one importing the other across a
// language boundary. Each page of a scanned document gets its own angle
// from its own page number, which is both what the formula was built for
// and what a real sheet-fed scanner does. No RNG, no clock, no
// clock-derived filenames.
//
// This writer is non-destructive, the same as `render_tree_docx`: it only
// creates directories and writes/overwrites the PDFs it is responsible
// for. It never deletes a stale PDF left behind by a renamed or deleted
// source — that is a corpus-consistency question for
// `synthvdr.qa.renders.gate_16_render_parity`, not a rendering one.
//
// Exits non-zero with a clear, single-line message when Node cannot find
// a local Chrome/Chromium for puppeteer to drive, or when puppeteer
// itself is not installed — this toolchain is optional, and its absence
// must never be a silent no-op or a stack trace.

import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

function rotationFor(slotId, page) {
  const digest = createHash("sha256").update(`${slotId}:${page}`).digest();
  const magnitude = 0.4 + (digest[0] / 255) * 0.7;
  return digest[1] % 2 === 0 ? magnitude : -magnitude;
}

function parseArgs(argv) {
  const args = { src: null, out: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--src") args.src = argv[++i];
    else if (argv[i] === "--out") args.out = argv[++i];
  }
  if (!args.src || !args.out) {
    throw new Error("usage: node pdf.mjs --src <blind-tree> --out <out-tree>");
  }
  return args;
}

// A small, deterministic markdown-to-HTML pass covering exactly what the
// generator's markdown uses (ATX headings and paragraphs) — mirrors
// synthvdr.render.docx.render_tree_docx's own minimal handling, so the
// DOCX and PDF renders present the same structure. No external markdown
// dependency: puppeteer is this toolchain's one optional dependency.
//
// ATX_HEADING is a character-for-character port of
// `synthvdr.render.docx._ATX_HEADING`, and the separator MUST stay `[ \t]+`
// — read that constant's own comment before touching this one. It records
// the separator going wrong twice on the Python side (`startswith("#")`
// too loose one way, `\s` too loose another) and ends: "name the exact
// separator set the spec names, never the one that merely reads
// naturally."
//
// This port shipped with `\s*`, which is BOTH of the rejected spellings at
// once — optional, so `#MeToo` and `#1 supplier` became headings that the
// DOCX render correctly left as paragraphs, and `#1 supplier` additionally
// lost its leading "#" to the capture; and Unicode-wide, so `# Title`
// became a heading on an NBSP that copy-pasted prose carries constantly.
// Gate 16 checks filename parity only and never opens a rendered file, so
// nothing in the harness would have caught the divergence;
// test_pdf_mjs_headings_match_python_exactly now runs this regex against
// the Python one over a shared corpus and is what does.
const ATX_HEADING = /^(#{1,6})[ \t]+(.*)$/;

// FENCE is the second half of the same port, and shipped missing entirely.
// `synthvdr.render.docx._FENCE` tracks ``` and ~~~ across lines so that
// nothing inside a fenced block is read as a heading — its comment names the
// case: "# a shell comment" is the single most common line in any shell or
// Python snippet, and it is not a heading just because it starts a fresh
// line inside a fence. Without the state machine this file turned every such
// line into an <h1> and the DOCX and PDF renders of the same document
// disagreed about its structure, with gate 16 (filename parity only) unable
// to see it.
//
// Fence markers themselves render as verbatim paragraphs, exactly as
// docx.py renders them: this project's rule is that content survives the
// render, and dropping the marker lines to make the output prettier is the
// trade docx.py explicitly refuses. An unclosed fence falls out of the same
// state machine for free — every remaining line stays fenced through to EOF.
const FENCE = /^(`{3,}|~{3,})/;

function mdToHtml(markdown) {
  const escape = (s) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = markdown.split(/\r?\n/);
  const body = [];
  let inFence = false;
  let fenceChar = null;
  for (const raw of lines) {
    const line = raw.trimEnd();

    if (inFence) {
      // Verbatim, no exceptions — including a line that would otherwise look
      // like a heading. The opening marker went through this same append.
      if (line.trim()) body.push(`<p>${escape(line)}</p>`);
      const closing = FENCE.exec(line);
      if (closing && closing[1][0] === fenceChar) {
        inFence = false;
        fenceChar = null;
      }
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      inFence = true;
      fenceChar = fence[1][0];
      body.push(`<p>${escape(line)}</p>`);
      continue;
    }

    const heading = ATX_HEADING.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length, 4);
      // group(2) verbatim, never re-trimmed: `[ \t]+` is greedy and has
      // already consumed the separator, so anything left is the title —
      // including a title that legitimately begins with "#".
      body.push(`<h${level}>${escape(heading[2])}</h${level}>`);
    } else if (line.trim()) {
      body.push(`<p>${escape(line.trim())}</p>`);
    }
  }
  return `<!doctype html><html><head><meta charset="utf-8"></head>` +
    `<body>${body.join("\n")}</body></html>`;
}

async function findMarkdownFiles(root) {
  const out = [];
  async function walk(dir) {
    const entries = await readdir(dir, { withFileTypes: true });
    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) await walk(full);
      else if (entry.isFile() && entry.name.endsWith(".md")) out.push(full);
    }
  }
  await walk(root);
  return out;
}

// The slots to scan, plus whatever this loader could not make sense of.
// Returns { slots: Set<string>, legacyPageRows: number }. Nothing is dropped
// in silence: `main` reports both the legacy second column and any slot that
// matched no document, because an unmatched slot renders a whole document as
// live text when the answer key expected a scan, and the run otherwise looks
// like a complete success.
async function loadScannedSlots(src) {
  const csvPath = path.join(path.dirname(src), "_key", "scanned.csv");
  if (!existsSync(csvPath)) return { slots: new Set(), legacyPageRows: 0 };
  const text = await readFile(csvPath, "utf-8");
  const slots = new Set();
  let legacyPageRows = 0;
  for (const line of text.split(/\r?\n/).slice(1)) {
    if (!line.trim()) continue;
    // Split on every comma, not just the first: a second column means this
    // is a manifest from before scanning became per-document, and its page
    // number no longer selects anything. Counted and reported rather than
    // ignored — a silently obsolete column is how the old page-1-only
    // behaviour hid in the first place.
    const cells = line.split(",");
    const slot = cells[0].trim();
    if (!slot) continue;
    if (cells.length > 1 && cells[1].trim()) legacyPageRows += 1;
    slots.add(slot);
  }
  return { slots, legacyPageRows };
}

async function loadPuppeteer() {
  try {
    return (await import("puppeteer")).default;
  } catch {
    throw new Error(
      "puppeteer is not installed — this render toolchain is optional; " +
        "run `npm install puppeteer` to enable PDF rendering"
    );
  }
}

// A4 at 96 CSS px/in, the density Chrome lays out at. The viewport is set to
// exactly these dimensions so the browser paginates the content the same way
// the text render does, and each screenshot below lines up with one real
// page rather than an arbitrary slice.
const PAGE_WIDTH_PX = 794;
const PAGE_HEIGHT_PX = 1123;

async function renderScannedDocument(browser, html, slotId, outPath) {
  // An image-only PDF: lay the document out at A4, screenshot it one page at
  // a time, and rebuild it as one image per page — no selectable text is
  // left anywhere for a tool to read without OCR.
  //
  // Page AT A TIME is the correction, and it is about GEOMETRY, not lost
  // content — see the header comment: the old single `fullPage: true`
  // screenshot did reach every page, because Chrome flowed the tall image
  // across them. Clipping per page instead bounds the skew displacement by
  // the page rather than by the document (so it stops growing with length),
  // aligns each image with the page box it sits in, and gives every page its
  // own angle below.
  const page = await browser.newPage();
  await page.setViewport({ width: PAGE_WIDTH_PX, height: PAGE_HEIGHT_PX });
  await page.setContent(html, { waitUntil: "networkidle0" });

  const contentHeight = await page.evaluate(
    () => document.documentElement.scrollHeight
  );
  const pageCount = Math.max(1, Math.ceil(contentHeight / PAGE_HEIGHT_PX));

  const shots = [];
  for (let i = 0; i < pageCount; i += 1) {
    shots.push(
      await page.screenshot({
        encoding: "base64",
        captureBeyondViewport: true,
        clip: {
          x: 0,
          y: i * PAGE_HEIGHT_PX,
          width: PAGE_WIDTH_PX,
          height: PAGE_HEIGHT_PX,
        },
      })
    );
  }
  await page.close();

  // Each page carries its OWN skew, from its own 1-based page number —
  // rotationFor's (slot, page) signature was always built for this, and a
  // sheet-fed scanner really does sit every sheet down differently.
  // `page-break-after` keeps one image to one PDF page (the break after the
  // last one collapses rather than adding a blank page).
  //
  // The rotation does still swing the corners outside the box, and
  // `overflow:hidden` clips them — about 10px at +/-1 degree over a
  // 1123px page. That is bounded by the PAGE now rather than by the whole
  // document, which is the point: the old form rotated one document-tall
  // image about its centre, so the same angle displaced the extremes by
  // ~46px on a 5-page deed and by more on anything longer. A few clipped
  // millimetres at the margin is what a real scan looks like; a clip that
  // grows with page count is not.
  const pages = shots
    .map((shot, i) => {
      const deg = rotationFor(slotId, i + 1);
      return (
        `<div style="width:${PAGE_WIDTH_PX}px;height:${PAGE_HEIGHT_PX}px;` +
        `overflow:hidden;page-break-after:always;">` +
        `<img src="data:image/png;base64,${shot}" ` +
        `style="width:100%;height:100%;object-fit:contain;` +
        `transform:rotate(${deg}deg);transform-origin:center;">` +
        `</div>`
      );
    })
    .join("");

  const wrapper = await browser.newPage();
  await wrapper.setViewport({ width: PAGE_WIDTH_PX, height: PAGE_HEIGHT_PX });
  await wrapper.setContent(
    `<!doctype html><html><body style="margin:0">${pages}</body></html>`,
    { waitUntil: "networkidle0" }
  );
  await wrapper.pdf({ path: outPath, format: "A4", printBackground: true });
  await wrapper.close();
  return pageCount;
}

async function main() {
  const { src, out } = parseArgs(process.argv.slice(2));
  const srcRoot = path.resolve(src);
  const outRoot = path.resolve(out);

  if (!existsSync(srcRoot)) {
    console.error(`pdf.mjs: source tree not found: ${srcRoot}`);
    process.exitCode = 1;
    return;
  }

  let puppeteer;
  let browser;
  try {
    puppeteer = await loadPuppeteer();
    browser = await puppeteer.launch({ headless: "new" });
  } catch (err) {
    console.error(`pdf.mjs: ${err.message}`);
    process.exitCode = 1;
    return;
  }

  try {
    const { slots: scannedSlots, legacyPageRows } = await loadScannedSlots(srcRoot);
    const files = await findMarkdownFiles(srcRoot);
    const matchedSlots = new Set();
    let written = 0;
    let scanned = 0;
    let scannedPages = 0;

    for (const file of files) {
      const rel = path.relative(srcRoot, file);
      const slotId = rel.replace(/\.md$/, "").split(path.sep).join("/");
      const target = path.join(outRoot, rel.replace(/\.md$/, ".pdf"));
      await mkdir(path.dirname(target), { recursive: true });

      const markdown = await readFile(file, "utf-8");
      const html = mdToHtml(markdown);

      if (scannedSlots.has(slotId)) {
        matchedSlots.add(slotId);
        scannedPages += await renderScannedDocument(browser, html, slotId, target);
        scanned += 1;
      } else {
        const page = await browser.newPage();
        await page.setContent(html, { waitUntil: "networkidle0" });
        await page.pdf({ path: target, format: "A4", printBackground: true });
        await page.close();
      }
      written += 1;
    }

    // A slot in the manifest that matched no document renders that document
    // as live text while the answer key believes it is a scan, and the run
    // otherwise reports a clean success — the same silence this whole
    // mechanism was rebuilt to remove. Named, not counted.
    const unmatched = [...scannedSlots].filter((s) => !matchedSlots.has(s)).sort();
    if (unmatched.length) {
      console.error(
        `pdf.mjs: ${unmatched.length} slot(s) in _key/scanned.csv matched no ` +
          `document under ${srcRoot} and were NOT scanned: ${unmatched.join(", ")}`
      );
      process.exitCode = 1;
    }
    if (legacyPageRows) {
      console.error(
        `pdf.mjs: _key/scanned.csv carries a page column on ${legacyPageRows} row(s). ` +
          "Scanning is per document now, so that column selects nothing — every page " +
          "of a listed slot is scanned. Regenerate it with " +
          "synthvdr.render.docx.write_scanned_csv."
      );
    }

    console.log(
      `pdf.mjs: wrote ${written} PDF(s) to ${outRoot}` +
        (scanned
          ? `; ${scanned} rendered as scans (${scannedPages} image page(s))`
          : "; none scanned")
    );
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(`pdf.mjs: ${err.message}`);
  process.exitCode = 1;
});
