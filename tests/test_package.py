import ast
import pathlib

import synthvdr

PACKAGE_ROOT = pathlib.Path(synthvdr.__file__).parent


def test_package_exposes_version():
    assert isinstance(synthvdr.__version__, str)
    assert synthvdr.__version__.count(".") == 2


def test_no_module_imports_below_its_first_definition():
    """E402 as one rule over the package, rather than one file at a time.

    qa/structural.py carried five module-level imports at line 44, between
    gate_02 and the constants gate_08 needs — the visible seam where a later
    batch of gates was appended, and the reason the file read as two files
    joined end to end. They were the only E402s in the package.

    There is no linter in CI (see .github/workflows/ci.yml: pytest and
    version-check.sh, nothing else), so the guard against the seam reopening
    has to be a test. Asserted over every module rather than over that one
    file: the pattern is what recurs, and it recurs wherever the next batch
    of anything gets appended.

    If a genuinely deferred import is ever needed, put it inside the function
    that needs it — render/docx.py already imports `docx` that way, and a
    function-level import is invisible to this walk.
    """
    offenders = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        body = ast.parse(path.read_text(encoding="utf-8")).body
        first_definition = None
        for node in body:
            is_import = isinstance(node, (ast.Import, ast.ImportFrom))
            if first_definition is None and not is_import:
                # A leading docstring is not a definition.
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    continue
                first_definition = node.lineno
            elif is_import and first_definition is not None:
                offenders.setdefault(path.name, []).append(node.lineno)
    assert not offenders, (
        f"module-level imports below the first definition: {offenders} — move "
        "them to the top, or into the function that needs them"
    )
