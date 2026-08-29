"""Static guard against the sinks that made the review UI vulnerable to a
stored XSS: `data/vendors/` is repo-controlled (a pull request can set any
field), the review UI renders those fields into a maintainer's browser, and
that server can `git push` and open GitHub PRs -- so any `innerHTML`
assignment or inline event-handler attribute built from interpolated data is
a privilege-escalation path from "anyone can open a PR" to "code runs
against a tool that can push".

There is no browser in this test environment, so this can't render the page
and check the DOM. Instead it asserts the *source* of index.html never
reintroduces either sink shape:

  1. `something.innerHTML = ...` where the right-hand side contains a
     template-literal interpolation `${...}` -- i.e. HTML built by string
     interpolation. The original vulnerable line was a single statement
     spanning several lines (main.innerHTML = visible.map(g => { ... return
     a template literal containing ${vendorRow(g.vendor)} ... }).join("");),
     so this doesn't just check the text immediately after `=` -- it scans
     from the assignment to the next top-level function boundary for `${`.
     A `.innerHTML = ""` / `.innerHTML = "<literal, no placeholders>"`
     assignment is fine and is not flagged.
  2. An inline `on<event>="..."` attribute in the markup whose value contains
     `${...}` -- i.e. a handler with data spliced into it, which is exactly
     how a `path` containing a single quote used to break out of
     `onclick="doApprove('${v.path}')"`. A static handler with no
     interpolation, like `onclick="openCommitModal()"`, is fine.

If this test starts failing, someone reintroduced string-built HTML for
repo-controlled data -- go back to building the element with
document.createElement/.textContent/property-assignment/addEventListener
instead of adding an escaping function or a narrower regex exception.
"""

from __future__ import annotations

import re
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "tools" / "review_ui" / "index.html"

_INTERPOLATION = "${"

# `.innerHTML` (optionally through a chained property access) followed by
# `=` and not `==`/`===` (hence the negative lookahead).
_INNER_HTML_ASSIGNMENT_RE = re.compile(r"\.innerHTML\s*=(?!=)")

# A blank line followed by a top-level function declaration -- used as the
# end-of-statement boundary below. An innerHTML assignment in this codebase
# is one expression (however many lines it spans via nested arrow-function
# callbacks) and never crosses into the next top-level function.
_FUNCTION_BOUNDARY_RE = re.compile(r"\n[ \t]*\n(?=(?:async\s+)?function\b)")

# Inline event-handler attributes in the literal HTML markup, e.g.
# onclick="...", onchange="...".
_INLINE_HANDLER_RE = re.compile(r'\bon[a-z]+\s*=\s*"([^"]*)"')


def _read_index_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _innerhtml_statement_windows(source: str) -> list[str]:
    """For every `.innerHTML = ` assignment in `source`, return the text
    from right after the `=` up to the next top-level function boundary (or
    EOF). See the module docstring for why a single-line "next backtick"
    match isn't enough: the original sink's right-hand side was a multi-line
    expression with an earlier semicolon-terminated statement inside its
    arrow-function body, before ever reaching the template literal."""
    boundaries = [m.start() for m in _FUNCTION_BOUNDARY_RE.finditer(source)]
    windows = []
    for m in _INNER_HTML_ASSIGNMENT_RE.finditer(source):
        end = next((b for b in boundaries if b > m.end()), len(source))
        windows.append(source[m.end() : end])
    return windows


def test_index_html_exists() -> None:
    assert INDEX_HTML.is_file(), f"expected {INDEX_HTML} to exist"


def test_no_innerhtml_assignment_interpolates_a_value() -> None:
    html = _read_index_html()
    violations = [w for w in _innerhtml_statement_windows(html) if _INTERPOLATION in w]
    assert not violations, (
        "Found an .innerHTML assignment whose statement contains ${...} "
        "(string-interpolated HTML). data/vendors/ is repo-controlled, so any value "
        "spliced into innerHTML this way is a stored-XSS sink against the "
        "maintainer's browser -- and this server can git push / open PRs. Build the "
        "element with document.createElement + .textContent / property assignment "
        "instead. Offending statement text:\n" + "\n---\n".join(violations)
    )


def test_no_inline_handler_attribute_interpolates_a_value() -> None:
    html = _read_index_html()
    violations = [
        m.group(0) for m in _INLINE_HANDLER_RE.finditer(html) if _INTERPOLATION in m.group(1)
    ]
    assert not violations, (
        "Found an inline on<event>=\"...\" handler whose value contains ${...} -- a "
        "record field (e.g. `path`) spliced directly into markup. This is exactly how "
        "a single quote in a repo-controlled field broke out of "
        "onclick=\"doApprove('${v.path}')\" and executed attacker script in the "
        "maintainer's browser. Use addEventListener with the record captured in a "
        "closure instead. Violations:\n" + "\n".join(violations)
    )


def test_the_innerhtml_guard_catches_the_original_multiline_vulnerable_pattern() -> None:
    # Proves the window-scan isn't vacuous and specifically handles the
    # tricky real-world shape: a `const ...;`-terminated statement *inside*
    # the arrow-function body, before the template literal that actually
    # carries the interpolation.
    vulnerable = """
function render() {
  main.innerHTML = visible.map(g => {
    const anyPending = g.vendor.pending || g.products.some(p => p.pending);
    return `<div>${vendorRow(g.vendor)}</div>`;
  }).join("");
}

function next() {}
"""
    windows = _innerhtml_statement_windows(vulnerable)
    assert any(_INTERPOLATION in w for w in windows)


def test_the_innerhtml_guard_does_not_flag_a_literal_assignment() -> None:
    safe = """
function render() {
  main.innerHTML = "";
}

function next() {}
"""
    windows = _innerhtml_statement_windows(safe)
    assert not any(_INTERPOLATION in w for w in windows)


def test_the_inline_handler_guard_catches_the_original_vulnerable_pattern() -> None:
    vulnerable = "<button onclick=\"doApprove('${v.path}')\">Approve</button>"
    matches = list(_INLINE_HANDLER_RE.finditer(vulnerable))
    assert matches and any(_INTERPOLATION in m.group(1) for m in matches)


def test_the_inline_handler_guard_does_not_flag_a_static_handler() -> None:
    safe = '<button onclick="openCommitModal()">Commit / PR…</button>'
    matches = list(_INLINE_HANDLER_RE.finditer(safe))
    assert not any(_INTERPOLATION in m.group(1) for m in matches)


def test_escapeHtml_is_gone() -> None:
    # escapeHtml() was never a real defence for the attribute/handler-context
    # sinks (it only helps text nodes), and the fix removes the sinks
    # entirely rather than trying to escape harder. Its reappearance is a
    # signal someone is patching over an innerHTML sink instead of removing
    # it -- not a hard failure on its own (the two guards above are what
    # actually gate this), but worth keeping visible here.
    html = _read_index_html()
    assert "function escapeHtml" not in html
