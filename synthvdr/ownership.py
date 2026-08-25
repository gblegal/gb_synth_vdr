"""Shared "is this directory ours to delete?" guard.

Any tool in this project that deletes-and-rebuilds a directory on every run
must prove it created that directory before calling shutil.rmtree on it.
The target is not always a room.conf-validated path — it may be a directory
a caller supplied directly as a parameter, and room.conf's own path checks
(synthvdr.roomconf.PATH_KEYS, enforced by load_room_conf) never run over a
bare parameter at all. Without this guard, pointing such a tool at an
existing, unrelated directory silently destroys its contents.

synthvdr.twin (Task 7, the blind->flagged tree derivation) found four
separate ways an ad hoc version of this check could be bypassed, across
five review rounds, before this shape held: write a marker file at the
root of the directory before any real content goes down, and refuse to
delete a non-empty target that does not carry it. Extracted here so every
writer with this problem — synthvdr.twin's flagged tree, synthvdr.subset's
subset directory, and any writer added later — reuses this one
implementation rather than re-deriving it. Two independently-written
copies of "prove ownership before deleting" is exactly how the original
guard picked up its four bypasses in the first place: divergence between
them, not any one flaw in either.

Each writer supplies its OWN marker filename and marker text (subset.py's
subset directory is not a "flagged tree", and its marker should not claim
to be one) — what is shared, and must not drift, is the ALGORITHM: how
ownership is proven and how deletion is gated on it. A distinct marker
name per writer is also a safety property in its own right: it stops a
directory built by one writer from being silently treated as "ours" by a
different one that happens to reuse the same name.

Presence-only trust, deliberately. The marker's CONTENT is never checked —
only that a real file with the exact name exists. Verifying content was
considered and rejected in Task 7: a user who edits the marker text (even
by accident — a stray save in an editor) would then be permanently locked
out of their own directory, unable to rebuild it and unable to prove to
the tool that they still own it. That risk outweighs anything content
verification would catch.

Two things presence-only trust must still get right, both found only
after Task 7's five review rounds had already closed every bypass the
question "can the guard be reasoned around" turns up: the check must not
follow a symlink at the marker's name (a symlink named `marker_name`
pointing at ANY unrelated regular file elsewhere would otherwise read as
"marker present"), and it must match the name by exact, un-casefolded
string equality against the real directory listing, not via a path
lookup — `(target / marker_name).is_file()` resolves through the OS's own
path-lookup semantics, which on a case-insensitive filesystem (APFS by
default) treats `.SYNTHVDR-SUBSET` as satisfying a check for
`.synthvdr-subset`. Both routes let an unrelated, attacker- or
accident-placed entry be treated as proof of ownership over a directory
this tool never built. Neither is a theoretical concern: both were
reproduced end-to-end against a real delete-and-rebuild call, with a
planted victim file destroyed as a result, before this fix.
"""

from __future__ import annotations

from pathlib import Path


class NotOwnedError(Exception):
    """A delete-and-rebuild target is not safe to delete: it is not a
    directory, could not be inspected, or is non-empty and carries no
    marker proving the caller created it.

    Callers are expected to catch this and re-raise it as their own
    module's exception type (e.g. synthvdr.twin.TwinError,
    synthvdr.subset.SubsetError) so a caller of THAT module sees one
    consistent exception type for everything the module can raise, rather
    than needing to know this shared module exists at all.
    """


def assert_target_is_ours(target: Path, marker_name: str) -> None:
    """The positive rule: only delete a directory the caller created.

    A target that does not exist, is empty, or carries a REAL, exact-name
    match for `marker_name` at its root may be deleted. Anything else is
    someone else's data — refuse and say so, naming the path. A target
    that names an existing file (not a directory) is refused too, with its
    own message, rather than let a later shutil.rmtree raise a bare
    NotADirectoryError out of a caller that otherwise raises its own
    exception type throughout.

    "Real, exact-name match" is load-bearing, not decorative — see the
    module docstring for the two ways a looser check was found to be
    bypassable:

      - the marker entry must not be a symlink (checked via
        entry.is_symlink() on the directory-listing entry ITSELF, never by
        stat'ing `target / marker_name` and letting a symlink there
        resolve to whatever it points at — that path is what let an
        unrelated file elsewhere read as "marker present");
      - the name is compared with plain `==` against the literal names
        `target.iterdir()` returns, never by asking the filesystem to look
        up `marker_name` itself (`(target / marker_name).is_file()`),
        which on a case-insensitive filesystem is satisfied by an entry
        differing only in case.

    A marker-named entry that IS a directory (not a file) still does not
    count, exactly as before — `entry.is_file()` is False for it.
    """
    try:
        if not target.exists():
            return
        is_dir = target.is_dir()
        entries = list(target.iterdir()) if is_dir else []
        has_marker = any(
            entry.name == marker_name and not entry.is_symlink() and entry.is_file()
            for entry in entries
        )
    except OSError as exc:
        raise NotOwnedError(
            f"refusing to delete {target}: it could not be inspected "
            f"({exc.__class__.__name__}: {exc})"
        ) from exc

    if not is_dir:
        raise NotOwnedError(
            f"refusing to delete {target}: it names an existing file, not "
            "a directory"
        )
    if has_marker or not entries:
        return
    raise NotOwnedError(
        f"refusing to delete {target}: it is not empty and carries no "
        f"{marker_name} marker, so it was not created by this tool. This "
        "directory is deleted and rebuilt in full on every run — point it "
        "at a directory synthvdr owns, or empty this one yourself first."
    )


def write_marker(target: Path, marker_name: str, marker_text: str) -> None:
    """Write the ownership marker at the root of `target`.

    Callers must do this BEFORE writing any real content into `target`, so
    a run that dies part-way through still leaves a directory the next run
    is allowed to clear, rather than a permanent lockout. `target` must
    already exist as a directory; callers create it first.
    """
    (target / marker_name).write_text(marker_text, encoding="utf-8")
