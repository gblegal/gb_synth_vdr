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
