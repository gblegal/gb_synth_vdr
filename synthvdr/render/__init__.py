"""Optional render layer: DOCX (this package, `docx.py`) and PDF (`pdf.mjs`,
a separate Node process).

Renders are extras, not the room itself — the room is markdown at heart, and
a tool can be evaluated against clean markdown alone. This package must
never be imported at core-build time (room generation, gates 1-15) so that a
missing `python-docx` or missing Node/Chrome never blocks building or
checking a room. Only `synthvdr.render.docx` imports `docx`, and only inside
`render_tree_docx`, so importing this package — or `synthvdr.render.docx`
itself — never requires the dependency to be installed; only calling
`render_tree_docx` does.
"""
