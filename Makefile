# Thin wrapper, in the spirit of tools/check.sh: nothing lives here that you
# cannot also run by hand, and no logic that is not tested elsewhere.
#
# It exists because pytest is in an optional extra and this repo ships no
# environment, so the first thing a newcomer meets is a test suite they
# cannot run. The pip commands in TECHNICAL-NOTES.md §1 still work and are
# still correct; they just assume you have somewhere to install into, which
# on a system Python that marks itself externally managed you do not.

VENV  := .venv
PY    := $(VENV)/bin/python
STAMP := $(VENV)/.deps-installed

.DEFAULT_GOAL := test
.PHONY: test venv clean

# Run the full suite. ARGS passes through, so `make test ARGS="-k twin -x"`
# reaches pytest unchanged rather than needing a target per invocation.
test: $(STAMP)
	$(PY) -m pytest $(ARGS)

# The environment on its own, for driving the CLIs by hand afterwards.
venv: $(STAMP)

# Stamped rather than phony so a second `make test` does not reinstall, and
# keyed on pyproject.toml so changing a dependency does.
$(STAMP): pyproject.toml
	@test -x $(PY) || python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --editable '.[dev,docx]'
	@touch $@

clean:
	rm -rf $(VENV)
