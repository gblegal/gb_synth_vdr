from synthvdr.qa.runner import GateResult, fail, ok, run_gates, skip, warn


class Ctx:
    strict = False


def results(*gates):
    ctx = Ctx()
    return run_gates(ctx, list(gates))


def test_all_passing_returns_zero(capsys):
    code = results(lambda c: ok("1", "counts"), lambda c: ok("2", "canon"))
    assert code == 0
    assert "no hard failures" in capsys.readouterr().out.lower()


def test_a_failure_returns_one(capsys):
    code = results(lambda c: ok("1", "counts"), lambda c: fail("2", "canon", "wrong dir"))
    assert code == 1
    assert "FAIL" in capsys.readouterr().out


def test_skip_is_printed_and_counted_not_silent(capsys):
    code = results(lambda c: skip("6", "render parity", "no render tree"))
    out = capsys.readouterr().out
    assert code == 0
    assert "SKIP 6" in out
    assert "1 skipped" in out
    assert "no hard failures" not in out.lower()


def test_summary_is_unmistakable_when_most_gates_skip(capsys):
    """Final review, F4: `--room` on a directory that is not a built room
    shows '0 failed, 15 skipped' and reads as a pass to a skimming
    newcomer — README's own first example runs exactly this, non-strict.
    The summary must say so plainly without changing the (correct, by
    design) non-strict exit code."""
    code = results(
        *[lambda c: skip(str(n), f"gate {n}", "input absent") for n in range(15)],
        lambda c: ok("15", "counts"),
        lambda c: ok("16", "canon"),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "MOST GATES SKIPPED" in out
    assert "15/17" in out


def test_summary_is_not_flagged_when_skips_are_a_minority(capsys):
    code = results(
        lambda c: skip("1", "gate one", "input absent"),
        lambda c: ok("2", "gate two"),
        lambda c: ok("3", "gate three"),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "MOST GATES SKIPPED" not in out


def test_strict_turns_a_skip_into_a_failure(capsys):
    ctx = Ctx()
    ctx.strict = True
    code = run_gates(ctx, [lambda c: skip("6", "render parity", "no render tree")])
    assert code == 1
    assert "strict" in capsys.readouterr().out.lower()


def test_a_gate_that_raises_is_reported_as_a_failure_not_a_crash(capsys):
    def boom(ctx):
        raise RuntimeError("kaboom")

    code = results(boom)
    assert code == 1
    assert "kaboom" in capsys.readouterr().out


def test_empty_gate_list_is_refused_not_reported_as_a_pass(capsys):
    """The same silence-as-pass failure this task exists to eliminate, one
    level up: not a gate skipping quietly, but zero gates ever registered."""
    code = run_gates(Ctx(), [])
    out = capsys.readouterr().out.lower()
    assert code == 1
    assert "no gates were run" in out
    assert "no hard failures" not in out


def test_a_warning_is_counted_in_the_summary_but_does_not_fail(capsys):
    code = results(lambda c: warn("3", "vocabulary", "unexpected token"))
    out = capsys.readouterr().out
    assert code == 0
    assert "WARN 3" in out
    assert "1 warned" in out


def test_a_multiline_detail_still_prints_as_one_line_per_gate(capsys):
    """A leak sweep naming several offending paths at once must not be able
    to break the one-line-per-gate transcript just by joining them with
    newlines."""
    detail = "a.md -> 9.9.9\nb.md -> 8.8.8\nc.md -> 7.7.7"
    code = results(lambda c: fail("9", "xrefs", detail))
    out = capsys.readouterr().out
    lines = out.rstrip("\n").split("\n")
    assert code == 1
    # exactly one line for the gate, one blank line, one summary line
    assert len(lines) == 3
    assert lines[0] == "FAIL 9 — xrefs: a.md -> 9.9.9 b.md -> 8.8.8 c.md -> 7.7.7"


# --- run_gates must not be fooled by a lazy sequence. -----------------------
#
# The empty-list guard exists to refuse the silence-as-pass shape: zero
# gates registered must never be reported as a clean room. A generator
# defeats it twice over — `not gates` is False for ANY generator, empty or
# not, so the guard waves it through, and `len(gates)` in the summary is a
# TypeError on something with no length. Callers build gate lists by
# comprehension and filter, so handing this a generator expression is an
# ordinary slip, not an exotic one.


def test_an_empty_generator_of_gates_is_refused_like_an_empty_list(capsys):
    code = run_gates(Ctx(), (g for g in []))
    out = capsys.readouterr().out.lower()
    assert code == 1
    assert "no gates were run" in out
    assert "no hard failures" not in out


def test_a_generator_of_gates_runs_and_summarises_like_a_list(capsys):
    code = run_gates(Ctx(), (g for g in [lambda c: ok("1", "counts"), lambda c: ok("2", "canon")]))
    out = capsys.readouterr().out
    assert code == 0
    assert "2 gates run, 0 failed" in out


# --- Truncated detail lists must say how much they hid. --------------------
#
# `truncated` was already written and already tested through gate 5, but it
# lived in qa/leakage.py and only that module's gates used it. Thirteen
# other sites across four gate modules hand-rolled "; ".join(xs[:5]), so a
# FAIL naming five problems read identically whether there were five or
# fifty. The count is the information a human triaging the failure needs
# most: it decides whether they are looking at a typo or a broken wave.


def test_truncated_names_how_many_it_hid():
    from synthvdr.qa.runner import truncated

    assert truncated([str(n) for n in range(8)]).endswith("(+3 more)")


def test_truncated_is_silent_when_everything_fits():
    from synthvdr.qa.runner import truncated

    assert "more" not in truncated(["a", "b", "c"])


def test_truncated_honours_a_caller_supplied_separator():
    from synthvdr.qa.runner import truncated

    assert truncated(["a", "b"], sep=", ") == "a, b"


def test_no_gate_hand_rolls_a_truncated_list():
    """The sweep's own guard, and the reason it is one test rather than
    thirteen: it fails for the fourteenth site too, whenever someone adds
    it. `truncated` is part of the gate-authoring vocabulary alongside
    fail/ok/skip, and every gate module already imports from this module.
    """
    from pathlib import Path

    qa_dir = Path(__file__).resolve().parent.parent / "synthvdr" / "qa"
    offenders = []
    for path in sorted(qa_dir.glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "[:5]" in line:
                offenders.append(f"{path.name}:{number}")
    assert not offenders, (
        "these truncate a detail list without naming what they hid — "
        "use runner.truncated() instead: " + ", ".join(offenders)
    )
