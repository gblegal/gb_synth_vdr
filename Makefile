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
.PHONY: test venv clean tag

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

# Tag the release on HEAD and push it, so a bump ends where it should rather
# than in a number nobody can point at a commit for.
#
# In the spirit of the header, the work is delegated: `claude plugin tag` reads
# the version from plugin.json, refuses if the marketplace entry disagrees,
# refuses on a dirty tree or a tag that already exists, and pushes with --push.
# Parsing the version here would be a second implementation of exactly what
# tools/version-check.sh exists to police, and the two would drift.
#
# The ancestry guard is the one piece of logic that earns its keep. This repo is
# its own marketplace, so master is the published artefact and a tag is a claim
# about what installs cached. `claude plugin tag` tags HEAD wherever HEAD is;
# run it from a feature branch and you publish a release marker pointing at a
# commit nobody was ever served. The fetch is not spare either — an ancestry
# check against a stale origin/master passes for the wrong reason.
#
# ARGS passes through as it does for test: `make tag ARGS=--dry-run` shows what
# would happen, `make tag ARGS='-m "..."'` sets the annotation.
tag:
	@command -v claude >/dev/null || { echo "make tag: needs the claude CLI on PATH" >&2; exit 1; }
	@git fetch --quiet origin master
	@git merge-base --is-ancestor HEAD origin/master || \
	    { echo "make tag: HEAD is not on origin/master — merge first, then tag the published commit" >&2; exit 1; }
	claude plugin tag --push $(ARGS)
