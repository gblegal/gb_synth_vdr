from synthvdr.qa.runner import GateResult, fail, ok, run_gates, skip


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
