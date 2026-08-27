#!/usr/bin/env node
// Optional PDF render. Never invoked at core-build time.
//
// Usage: node synthvdr/render/pdf.mjs --src <blind-tree> --out <out-tree>
//
// Renders every markdown file under --src to a PDF twin under --out,
// mirroring --src's relative layout. If a `scanned.csv` file exists at
// `<parent of --src>/_key/scanned.csv`, listing `slot,page` rows (slot is
// the source file's path relative to --src, without extension and with
// forward slashes; page is the 1-based page number to replace), the named
// pages of the matching PDF are re-rendered as image-only pages — a
// screenshot of the page, slightly rotated — instead of live text, so a
// tool under test has to OCR them. Absent `scanned.csv` is not an error:
// it just means no page in this run is scanned.
//
// Deterministic and idempotent: rotation angles come from `rotationFor`,
// a direct JS port of `synthvdr.render.docx.rotation_for` (same sha256
// formula, same +/-0.4-1.1 degree range) so the two renderers agree on the
// same (slot, page) pair without one importing the other across a
// language boundary. No RNG, no clock, no clock-derived filenames.
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

function mdToHtml(markdown) {
  const escape = (s) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = markdown.split(/\r?\n/);
  const body = [];
  for (const raw of lines) {
    const line = raw.trimEnd();
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

async function loadScannedSlots(src) {
  const csvPath = path.join(path.dirname(src), "_key", "scanned.csv");
  if (!existsSync(csvPath)) return new Map();
  const text = await readFile(csvPath, "utf-8");
  const slots = new Map();
  for (const line of text.split(/\r?\n/).slice(1)) {
    if (!line.trim()) continue;
    const [slot, pageStr] = line.split(",");
    const page = parseInt(pageStr, 10);
    if (!slot || Number.isNaN(page)) continue;
    if (!slots.has(slot.trim())) slots.set(slot.trim(), new Set());
    slots.get(slot.trim()).add(page);
  }
  return slots;
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

async function renderScannedPage(browser, html, rotationDeg, outPath) {
  // An image-only page: screenshot the rendered content, then place that
  // image, rotated, into a fresh page and export it as a one-page PDF —
  // there is no selectable text left for a tool to read without OCR.
  const page = await browser.newPage();
  await page.setContent(html, { waitUntil: "networkidle0" });
  const screenshot = await page.screenshot({ encoding: "base64", fullPage: true });
  await page.close();

  const wrapper = await browser.newPage();
  await wrapper.setContent(
    `<!doctype html><html><body style="margin:0">` +
      `<img src="data:image/png;base64,${screenshot}" ` +
      `style="transform: rotate(${rotationDeg}deg); transform-origin: center;">` +
      `</body></html>`,
    { waitUntil: "networkidle0" }
  );
  await wrapper.pdf({ path: outPath, format: "A4", printBackground: true });
  await wrapper.close();
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
    const scannedSlots = await loadScannedSlots(srcRoot);
    const files = await findMarkdownFiles(srcRoot);
    let written = 0;

    for (const file of files) {
      const rel = path.relative(srcRoot, file);
      const slotId = rel.replace(/\.md$/, "").split(path.sep).join("/");
      const target = path.join(outRoot, rel.replace(/\.md$/, ".pdf"));
      await mkdir(path.dirname(target), { recursive: true });

      const markdown = await readFile(file, "utf-8");
      const html = mdToHtml(markdown);
      const scannedPages = scannedSlots.get(slotId);

      if (scannedPages && scannedPages.has(1)) {
        // Single-page markdown sources render as a single scanned page.
        await renderScannedPage(browser, html, rotationFor(slotId, 1), target);
      } else {
        const page = await browser.newPage();
        await page.setContent(html, { waitUntil: "networkidle0" });
        await page.pdf({ path: target, format: "A4", printBackground: true });
        await page.close();
      }
      written += 1;
    }

    console.log(`pdf.mjs: wrote ${written} PDF(s) to ${outRoot}`);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(`pdf.mjs: ${err.message}`);
  process.exitCode = 1;
});
