"""The gate runner.

A gate whose inputs are absent must SKIP loudly. Silence is indistinguishable
from a pass, and that silence has already hidden real defects for two phases of
a previous build. --strict turns every skip into a hard failure.

Two further disciplines are enforced here, once, rather than left to
individual gates:

  - An empty gate list is refused, not reported as a clean pass. That is the
    same silence-as-pass failure this module exists to eliminate, one level
    up: not one gate skipping quietly, but zero gates ever having run.
  - Every gate's `detail` prints as exactly one line, regardless of what it
    contains. Embedded newlines are collapsed to spaces here so the
    one-line-per-gate invariant holds no matter what any of the seventeen
    gates puts in `detail` — a leak sweep naming several offending paths at
    once should not be able to break the transcript just by joining them
    with newlines.

WARN is informational and never affects the exit code, but IS counted in the
summary line, so a warn-only run cannot be misread as one where nothing was
worth mentioning.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"


@dataclass(frozen=True)
class GateResult:
    number: str
    name: str
    status: str
    detail: str = ""


def ok(number: str, name: str, detail: str = "") -> GateResult:
    return GateResult(number, name, PASS, detail)


def fail(number: str, name: str, detail: str) -> GateResult:
    return GateResult(number, name, FAIL, detail)


def skip(number: str, name: str, reason: str) -> GateResult:
    return GateResult(number, name, SKIP, reason)


def warn(number: str, name: str, detail: str) -> GateResult:
    return GateResult(number, name, WARN, detail)


@dataclass
class GateContext:
    room: Path
    conf: object
    findings: object
    distractors: list
    strict: bool = False

    @property
    def blind_root(self) -> Path:
        return self.room / self.conf.get("BLIND_TREE")

    @property
    def flagged_root(self) -> Path:
        return self.room / self.conf.get("FLAGGED_TREE")

    @property
    def key_root(self) -> Path:
        return self.room / self.conf.get("KEY_ROOT")

    def blind_files(self) -> List[Path]:
        """Every file under BLIND_TREE, unfiltered by suffix.

        Suffix filtering is the CALLER's job, not this method's — different
        gates care about different suffixes, and handing back everything
        keeps one implementation usable by all of them. A gate that walks
        BLIND_TREE or FLAGGED_TREE with this same `rglob("*") if p.is_file()`
        shape but skips adding its own suffix filter will pick up
        synthvdr.twin.MARKER_NAME (suffix '') as if it were a document —
        keep the filter gate 2 already applies.
        """
        if not self.blind_root.is_dir():
            return []
        return sorted(p for p in self.blind_root.rglob("*") if p.is_file())


def _one_line(text: str) -> str:
    """Collapse all whitespace, including embedded newlines, to single spaces.

    Applied once, here, at print time, so the one-line-per-gate invariant
    holds regardless of what any gate returns in `detail` — gate authors are
    not required to remember to sanitise their own output.
    """
    return " ".join(text.split())


def run_gates(ctx, gates: List[Callable]) -> int:
    # Materialise before anything reads the sequence twice. The guard below
    # and the summary line further down both need a length, and a lazy
    # sequence gives neither: `not gates` is False for ANY generator, empty
    # or not, so an empty one walks straight past the one guard whose whole
    # job is to refuse "zero gates registered" — the silence-as-pass shape
    # this runner exists to eliminate — and `len(gates)` is then a
    # TypeError. The non-empty case is the worse of the two in practice:
    # every gate runs and prints, and the summary line and the exit code
    # are what go missing, so the caller gets a full-looking transcript and
    # a traceback instead of a verdict. Callers assemble gate lists with
    # comprehensions and filters, so being handed a generator expression is
    # an ordinary slip.
    gates = list(gates)
    if not gates:
        print("FAIL — no gates were run: an empty gate list is refused, never reported as a pass")
        return 1

    failures = skips = warns = 0
    for gate in gates:
        try:
            result = gate(ctx)
        except Exception as exc:  # a crashing gate is a failing gate, never a silent one
            name = getattr(gate, "__name__", "gate")
            result = fail("?", name, f"gate raised {type(exc).__name__}: {exc}")
        line = f"{result.status} {result.number} — {result.name}"
        if result.detail:
            line += f": {_one_line(result.detail)}"
        print(line)
        if result.status == FAIL:
            failures += 1
        elif result.status == SKIP:
            skips += 1
        elif result.status == WARN:
            warns += 1

    print()
    summary = f"{len(gates)} gates run, {failures} failed, {skips} skipped, {warns} warned"
    if failures == 0 and skips == 0:
        summary += " — no hard failures"
    elif failures == 0 and skips * 2 > len(gates):
        # Final review, F4: a majority of gates skipping is not itself a
        # failure in non-strict mode (that is the whole point of SKIP
        # discipline — an honest "we did not check this" rather than a
        # silent pass), but "0 failed" leading the line reads as a clean
        # room to a skimming newcomer even when most of what would have
        # caught a real defect never ran at all (e.g. 15 of 17 gates
        # skipped on a directory that is not a built room). Made
        # unmistakable here, in the one place every caller sees it,
        # rather than only in --strict, which does not run by default.
        summary += (
            f" — MOST GATES SKIPPED ({skips}/{len(gates)}): this run does not verify "
            "the room; re-run with --strict before trusting it"
        )
    print(summary)
    if skips and getattr(ctx, "strict", False):
        print("strict mode: skipped gates count as failures")
        return 1
    return 1 if failures else 0
