#!/usr/bin/env python3
"""Print the duplicate-paragraph distribution gate 20 is calibrated against.

Usage: python3 tools/calibrate_repetition.py DIR [DIR ...]

Each DIR is walked for .md and .txt files: a room's blind tree, or the
full_contract_txt/ directory of a CUAD download you supply — no corpus text is
kept in this repository. Documents under gate 20's MIN_WORDS are left out,
exactly as the gate leaves them out. Read-only: it never writes anywhere.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from synthvdr.qa.depth import wordcount
from synthvdr.qa.repetition import MIN_WORDS, THRESHOLD, duplicate_paragraph_share


def summarise(directory: Path) -> str:
    rows = []
    for path in sorted(p for p in directory.rglob("*") if p.is_file() and p.suffix in (".md", ".txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if wordcount(text) >= MIN_WORDS:
            rows.append((duplicate_paragraph_share(text), path.relative_to(directory).as_posix()))
    if not rows:
        return f"{directory}: no document of {MIN_WORDS}+ words"
    shares = sorted(share for share, _ in rows)
    over = [(share, name) for share, name in sorted(rows, reverse=True) if share > THRESHOLD]
    lines = [
        f"{directory}: n={len(rows)} median={statistics.median(shares):.3f} "
        f"max={shares[-1]:.3f} above {THRESHOLD}: {len(over)}"
    ]
    lines += [f"  {share:.3f} {name}" for share, name in over[:10]]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Gate 20 calibration: duplicate-paragraph share.")
    parser.add_argument("dirs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    missing = [str(d) for d in args.dirs if not d.is_dir()]
    if missing:
        print(f"calibrate_repetition: not a directory: {', '.join(missing)}", file=sys.stderr)
        return 2
    for directory in args.dirs:
        print(summarise(directory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
