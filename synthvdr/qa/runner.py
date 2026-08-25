"""The gate runner.

A gate whose inputs are absent must SKIP loudly. Silence is indistinguishable
from a pass, and that silence has already hidden real defects for two phases of
a previous build. --strict turns every skip into a hard failure.
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
        if not self.blind_root.is_dir():
            return []
        return sorted(p for p in self.blind_root.rglob("*") if p.is_file())


def run_gates(ctx, gates: List[Callable]) -> int:
    failures = skips = 0
    for gate in gates:
        try:
            result = gate(ctx)
        except Exception as exc:  # a crashing gate is a failing gate, never a silent one
            name = getattr(gate, "__name__", "gate")
            result = fail("?", name, f"gate raised {type(exc).__name__}: {exc}")
        line = f"{result.status} {result.number} — {result.name}"
        if result.detail:
            line += f": {result.detail}"
        print(line)
        if result.status == FAIL:
            failures += 1
        elif result.status == SKIP:
            skips += 1

    print()
    summary = f"{len(gates)} gates run, {failures} failed, {skips} skipped"
    if failures == 0 and skips == 0:
        summary += " — no hard failures"
    print(summary)
    if skips and getattr(ctx, "strict", False):
        print("strict mode: skipped gates count as failures")
        return 1
    return 1 if failures else 0
