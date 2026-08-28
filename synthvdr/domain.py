"""Domain packs: the section taxonomy, document archetypes and finding seeds."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List

import yaml

DEFAULT_DOMAIN_ROOT = Path(__file__).resolve().parent.parent / "domain" / "ma"


class DomainError(Exception):
    """A domain pack is missing or internally inconsistent."""


@dataclass(frozen=True)
class Section:
    number: int
    dir_name: str
    title: str
    workstream: str
    weight: float
    subsections: List[str]
    # A section no room may drop. `DomainPack.subset` refuses a subset that
    # omits one. Defaulted so a sections.yaml row without the key still splats
    # into Section(**row), which is how load_domain builds these.
    core: bool = False


@dataclass(frozen=True)
class Archetype:
    name: str
    floor: int
    filename_patterns: List[str]


@dataclass(frozen=True)
class DomainPack:
    sections: List[Section]
    archetypes: Dict[str, Archetype]
    default_archetype: str
    tier_f_floor: int
    finding_archetypes: Dict[str, List[str]]
    # True on a pack `subset()` produced. Read by `workstreams()`, which refuses
    # to answer on one — see that method for why.
    is_subset: bool = False

    def __post_init__(self) -> None:
        """Two invariants a DomainPack must satisfy however it was built.

        FIRST: `default_archetype` must name an archetype this pack actually
        has. `qa.depth.classify_archetype` falls back to it for any filename
        no pattern matches, and `floor_for` then subscripts
        `pack.archetypes[...]` — so a pack whose default names nothing loads
        perfectly, passes every check here, and detonates on the first
        unclassifiable document as a bare `KeyError`. The runner turns that
        into `FAIL ? — gate_10_depth: gate raised KeyError: 'standrd'`, which
        names the typo but not the file it is in, and reads like a defect in
        the gate rather than in `archetypes.yaml`. One typo, arbitrarily far
        from its cause.

        SECOND: a tier-A anchor must never be held to a lower depth standard
        than tier-F filler. A tier-A anchor carries planted evidence; tier-F
        filler carries none. If any archetype's floor sits below the flat
        tier-F floor, a correctly tagged anchor is held to a LOWER floor than
        generic filler — backwards for the property this domain pack exists
        to encode.

        Both are enforced on the TYPE rather than in `load_domain`, because
        they belong to what a DomainPack is, not to one way of building one.
        Checked only at load time they would bind YAML on disk and nothing
        else, while DomainPack is also constructed directly — by the tests
        covering archetype classification, and by anything later that
        assembles a pack in memory. That is precisely where a pack encoding a
        broken rule would be built by hand and quietly believed.
        `load_domain` re-raises with the pack root, so a failure loading from
        disk still says which pack.
        """
        if self.default_archetype not in self.archetypes:
            raise DomainError(
                f"default_archetype {self.default_archetype!r} is not one of this pack's "
                f"archetypes ({sorted(self.archetypes)}) — every filename no pattern "
                "matches falls back to it, so a pack that ships this way fails gate 10 "
                "with a bare KeyError on the first unclassifiable document"
            )

        under_floor = sorted(
            name for name, a in self.archetypes.items() if a.floor < self.tier_f_floor
        )
        if under_floor:
            raise DomainError(
                f"archetype floor(s) {under_floor} are below tier_f_floor "
                f"({self.tier_f_floor}) — a tier-A anchor must never be held to a "
                "lower depth standard than tier-F filler"
            )

    def section_dirs(self) -> List[str]:
        return [s.dir_name for s in self.sections]

    def workstreams(self) -> List[str]:
        """The domain pack's canonical workstream order — sections.yaml's own
        `number`-sorted order, which `load_domain` has already confirmed
        `finding-archetypes.yaml` agrees with (see the check there).

        This is the one list any positional pairing with room.conf's
        FINDING_PREFIXES must be built from (`synthvdr.schema.
        derive_prefix_for_workstream`'s `workstreams` argument) — never
        `dict(finding_archetypes)`'s key order directly, even though the two
        are required to match, because this is the name for "the domain
        pack's canonical order" that stays correct even if a future domain
        pack adds a third file that also has an opinion about workstream
        order.

        Raises on a subset — see the guard below.
        """
        if self.is_subset:
            raise DomainError(
                "workstreams() is meaningless on a subset: it exists to be zipped "
                "positionally against room.conf's FINDING_PREFIXES, and on a subset "
                "every prefix after the first dropped section pairs with the wrong "
                "workstream. Pass the FULL pack — load_domain(DEFAULT_DOMAIN_ROOT) — "
                "to derive_prefix_for_workstream. FINDING_PREFIXES covers all twenty "
                "workstreams even in a room that builds a subset of them."
            )
        return [s.workstream for s in self.sections]

    def subset(self, dir_names: Iterable[str]) -> "DomainPack":
        """This pack narrowed to `dir_names`, with weights renormalised.

        A room may build fewer than every workstream the pack declares — at XS
        the alternative is a fiction stretched to cover a pension scheme and a
        bank facility the invented deal has no reason to have, and one document
        per subsection, which leaves ordinary sibling cross-references with no
        sibling to resolve to.

        THE RENORMALISATION IS THE CORRECTNESS CONDITION, not tidiness.
        `slots._allocate` spreads a room's document budget across sections by
        weight. The shipped pack sums to 1.0 across twenty; any subset sums to
        less, and the allocator's largest-remainder pass then has a shortfall
        larger than the number of sections left to give slots to. Filtering
        without renormalising builds 35 documents for a room whose `room.conf`
        declares 40 — silently, with gate 2 failing for the rest of the build
        and nothing naming the cause.

        Sections come back in the PACK's order, never the caller's: slot ids
        derive from `Section.number`, so honouring the caller's order would
        renumber the room.

        `finding_archetypes` is narrowed to the surviving workstreams, which keeps
        the returned pack internally consistent — nothing it exposes describes a
        workstream it no longer has. The actual guard against a finding stranded in
        a dropped section is `schema.evidence_outside_sections`, run at Gate B.
        """
        wanted = list(dict.fromkeys(dir_names))
        by_dir = {s.dir_name: s for s in self.sections}

        unknown = [d for d in wanted if d not in by_dir]
        if unknown:
            raise DomainError(
                f"no such section(s) in this domain pack: {sorted(unknown)} — "
                f"available: {sorted(by_dir)}"
            )

        keep = set(wanted)
        dropped_core = [s.dir_name for s in self.sections if s.core and s.dir_name not in keep]
        if dropped_core:
            raise DomainError(
                f"cannot drop core section(s) {sorted(dropped_core)} — every deal has "
                "them, and 18_transaction is the only natural home for the fact "
                "sheet's headline figures, which gate 13 greps the room for"
            )

        kept = [s for s in self.sections if s.dir_name in keep]
        total = sum(s.weight for s in kept)
        if total <= 0:
            raise DomainError(f"sections {sorted(keep)} carry no weight between them")

        return replace(
            self,
            sections=[replace(s, weight=s.weight / total) for s in kept],
            finding_archetypes={
                w: a for w, a in self.finding_archetypes.items()
                if w in {s.workstream for s in kept}
            },
            is_subset=True,
        )

    def section_by_dir(self, dir_name: str) -> Section:
        for section in self.sections:
            if section.dir_name == dir_name:
                return section
        raise DomainError(f"unknown section directory: {dir_name}")


def _read(path: Path) -> dict:
    if not path.is_file():
        raise DomainError(f"domain pack file missing: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_domain(root: Path) -> DomainPack:
    sections = [Section(**row) for row in _read(root / "sections.yaml")["sections"]]
    sections.sort(key=lambda s: s.number)

    arch_doc = _read(root / "archetypes.yaml")
    archetypes = {
        name: Archetype(name=name, floor=body["floor"], filename_patterns=body["filename_patterns"])
        for name, body in arch_doc["archetypes"].items()
    }
    finding_archetypes = _read(root / "finding-archetypes.yaml")["finding_archetypes"]

    total_weight = sum(s.weight for s in sections)
    if abs(total_weight - 1.0) > 1e-6:
        raise DomainError(f"{root}: section weights sum to {total_weight}, expected 1.0")

    # Final review, F2: sections.yaml and finding-archetypes.yaml each declare
    # the domain's workstream list independently, and
    # synthvdr.schema.derive_prefix_for_workstream zips a caller-supplied
    # workstream order positionally against room.conf's FINDING_PREFIXES. That
    # zip trusts its caller to pass workstreams in "the domain pack's
    # canonical order" — a convention that, before this check, spanned two
    # files with nothing enforcing they agreed. Swapping two rows in
    # finding-archetypes.yaml (same set of workstreams, same length, wrong
    # order) is a one-line edit that would silently re-pair every finding-ID
    # prefix with the wrong workstream — on wave 1 of a fresh room,
    # derive_prefix_for_workstream's own cross-check covers nothing, because
    # no workstream has an existing finding yet to check the pairing against.
    # Checked here, once, at load time, so that blind spot is closed at the
    # source instead of depending on findings existing later to catch it.
    section_workstreams = [s.workstream for s in sections]
    archetype_workstreams = list(finding_archetypes)
    if section_workstreams != archetype_workstreams:
        missing = sorted(set(section_workstreams) - set(archetype_workstreams))
        extra = sorted(set(archetype_workstreams) - set(section_workstreams))
        if missing or extra:
            raise DomainError(
                f"{root}: finding-archetypes.yaml's workstreams do not match "
                f"sections.yaml's — missing {missing}, unexpected {extra}"
            )
        raise DomainError(
            f"{root}: finding-archetypes.yaml declares the same workstreams as "
            "sections.yaml but in a DIFFERENT ORDER. sections.yaml (canonical): "
            f"{section_workstreams}. finding-archetypes.yaml: {archetype_workstreams}. "
            "Every positional pairing with room.conf's FINDING_PREFIXES trusts this "
            "order — a reordering here would silently mint new finding IDs under the "
            "wrong workstream's prefix."
        )

    # Every invariant DomainPack enforces on itself is raised without the
    # pack root, which the type cannot know. Re-raise with it, so loading a
    # bad pack from disk still names which pack — and so this stays true for
    # invariants added to __post_init__ later, not just today's.
    try:
        return DomainPack(
            sections=sections,
            archetypes=archetypes,
            default_archetype=arch_doc["default_archetype"],
            tier_f_floor=arch_doc["tier_f_floor"],
            finding_archetypes=finding_archetypes,
        )
    except DomainError as exc:
        raise DomainError(f"{root}: {exc}") from exc
