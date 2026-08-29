"""Static guard against the sinks that made the review UI vulnerable to a
stored XSS: `data/vendors/` is repo-controlled (a pull request can set any
field), the review UI renders those fields into a maintainer's browser, and
that server can `git push` and open GitHub PRs -- so any HTML-injection sink
built from interpolated data is a privilege-escalation path from "anyone can
open a PR" to "code runs against a tool that can push".

There is no browser in this test environment, so this can't render the page
and check the DOM. Instead it asserts the *source* of index.html never
reintroduces any of these sink shapes:

  1. `.innerHTML = ...` or `.innerHTML += ...` whose right-hand side is not a
     bare string literal (a plain "...", '...', or `...` token with no `${`
     interpolation and nothing else glued onto it -- no concatenation, no
     variable, no function call). `.innerHTML = ""` is fine.
     `.innerHTML = "<b>" + name` and `.innerHTML += x` are both flagged, even
     though neither contains a template-literal `${}`.
  2. `.insertAdjacentHTML(...)`, `.outerHTML` (read or write), `document.write(
     ...)` / `document.writeln(...)`, and `.createContextualFragment(...)` --
     any use of these is flagged unconditionally. None of them are needed
     for DOM-building from trusted structure + textContent/property
     assignment, so there's no legitimate call site to allow-list.
  3. An inline `on<event>="..."` attribute in the markup whose value contains
     `${...}` -- i.e. a handler with data spliced into it, which is exactly
     how a `path` containing a single quote used to break out of
     `onclick="doApprove('${v.path}')"`. A static handler with no
     interpolation, like `onclick="openCommitModal()"`, is fine.
  4. `.setAttribute(name, value)` where `name` is not itself a string
     literal -- a dynamic attribute name is how a repo-controlled field could
     set `onclick`/`onerror`/etc. as an *attribute* rather than a DOM
     property, sidestepping the property-assignment discipline described in
     index.html's own comment. The one legitimate use in this file --
     `e.setAttribute(k, v)` inside the generic `h()` helper, where `k` is
     always a literal object-literal key from the call site, never a
     record field -- is allow-listed via an explicit
     `// static-guard-allow: ...` comment on that line; the check skips any
     line carrying that marker. This is the only exemption mechanism, and
     it's opt-in per line, not a blanket suppression.

WHAT THIS DOES NOT PROVE: this is a regression tripwire for the specific
sink *shapes* listed above, matched with regexes over the file's text -- it
is not a JavaScript parser and it is not a proof that index.html contains no
DOM-injection sink. It cannot see through indirection (e.g. an attacker
value reaching one of these sinks via a renamed variable, a helper function
defined elsewhere, string concatenation split across statements, or
`window["inner" + "HTML"]`-style obfuscation), and a sufficiently different
sink shape (e.g. a future templating library, `eval`, or assigning into
`Range.prototype` methods) is entirely outside what it looks for. Treat a
clean run of this file as "no known vulnerable shape was reintroduced", not
as "this file has no XSS" -- overstating that would invite exactly the false
confidence a narrow tripwire like this should not create.

If a test here starts failing, someone reintroduced string-built HTML for
repo-controlled data -- go back to building the element with
document.createElement/.textContent/property-assignment/addEventListener
instead of adding an escaping function or a narrower regex exception.
"""

from __future__ import annotations

import re
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "tools" / "review_ui" / "index.html"

_INTERPOLATION = "${"

# A single explicit, per-line opt-out for the one reviewed, safe use of a
# dynamic setAttribute name in this file (see module docstring, point 4).
_ALLOW_MARKER = "static-guard-allow"

# `.innerHTML` assignment via `=` (not `==`/`===`) or `+=`.
_INNER_HTML_ASSIGNMENT_RE = re.compile(r"\.innerHTML\s*(?:\+=|=(?!=))")

# A single JS string/template-literal token: "...", '...', or `...`. This
# intentionally permits any character (including `$` and `{`) *inside* a
# backtick literal at the regex level -- interpolation is ruled out
# separately by checking the matched text for the literal substring `${`,
# rather than trying to hand-roll that exclusion into the character class.
_STRING_LITERAL_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"' r"|'(?:[^'\\]|\\.)*'" r"|`(?:[^`\\]|\\.)*`"
)

# The entire (trimmed) right-hand side of an assignment must be exactly one
# such literal, optionally followed by a semicolon -- nothing concatenated,
# no trailing expression.
_BARE_LITERAL_LINE_RE = re.compile(rf"^\s*({_STRING_LITERAL_RE.pattern})\s*;?\s*$")

# Sinks that are flagged on any use, unconditionally -- none of them are
# needed anywhere in this file.
_UNCONDITIONAL_SINK_RES: dict[str, re.Pattern[str]] = {
    "insertAdjacentHTML": re.compile(r"\.insertAdjacentHTML\s*\("),
    "outerHTML": re.compile(r"\.outerHTML\b"),
    "document.write/writeln": re.compile(r"\bdocument\.write(?:ln)?\s*\("),
    "createContextualFragment": re.compile(r"\.createContextualFragment\s*\("),
}

# Inline event-handler attributes in the literal HTML markup, e.g.
# onclick="...", onchange="...".
_INLINE_HANDLER_RE = re.compile(r'\bon[a-z]+\s*=\s*"([^"]*)"')

# `.setAttribute(<name-arg>, ...)` -- deliberately simple (no nested commas or
# parens in <name-arg>), matching this file's actual call shapes rather than
# trying to be a general JS-argument parser.
_SET_ATTRIBUTE_RE = re.compile(r"\.setAttribute\(\s*([^,()]+?)\s*,")

_LITERAL_ARG_RE = re.compile(rf"^(?:{_STRING_LITERAL_RE.pattern})$")


def _read_index_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _is_bare_literal_rhs(rhs_first_line: str) -> bool:
    m = _BARE_LITERAL_LINE_RE.match(rhs_first_line)
    if not m:
        return False
    return _INTERPOLATION not in m.group(1)


def innerhtml_violations(source: str) -> list[str]:
    """Every `.innerHTML`/`.innerHTML +=` assignment whose right-hand side
    (taken up to the end of that source line -- see module docstring's
    "what this does not prove") is not a bare string literal."""
    violations = []
    for m in _INNER_HTML_ASSIGNMENT_RE.finditer(source):
        rest_of_line = source[m.end() :].split("\n", 1)[0]
        if not _is_bare_literal_rhs(rest_of_line):
            violations.append(source[m.start() : m.end()] + rest_of_line)
    return violations


def unconditional_sink_violations(source: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name, pattern in _UNCONDITIONAL_SINK_RES.items():
        matches = [m.group(0) for m in pattern.finditer(source)]
        if matches:
            found[name] = matches
    return found


def set_attribute_violations(source: str) -> list[str]:
    violations = []
    for line in source.split("\n"):
        if _ALLOW_MARKER in line:
            continue
        for m in _SET_ATTRIBUTE_RE.finditer(line):
            name_arg = m.group(1).strip()
            if not _LITERAL_ARG_RE.match(name_arg):
                violations.append(line.strip())
    return violations


def test_index_html_exists() -> None:
    assert INDEX_HTML.is_file(), f"expected {INDEX_HTML} to exist"


def test_no_innerhtml_assignment_has_a_non_literal_right_hand_side() -> None:
    html = _read_index_html()
    violations = innerhtml_violations(html)
    assert not violations, (
        "Found an .innerHTML (or .innerHTML +=) assignment whose right-hand side is "
        "not a bare string literal. data/vendors/ is repo-controlled, so any value "
        "reaching innerHTML this way -- whether via ${...} interpolation, string "
        "concatenation, or a bare variable -- is a stored-XSS sink against the "
        "maintainer's browser, and this server can git push / open PRs. Build the "
        "element with document.createElement + .textContent / property assignment "
        "instead. Offending statement text:\n" + "\n---\n".join(violations)
    )


def test_no_unconditional_html_injection_sink_is_used() -> None:
    html = _read_index_html()
    found = unconditional_sink_violations(html)
    assert not found, (
        "Found a use of a sink that is always flagged regardless of its argument "
        "(insertAdjacentHTML / outerHTML / document.write(ln) / "
        "createContextualFragment). None of these are needed to build DOM from "
        "trusted structure + textContent/property assignment, and each is another "
        "way repo-controlled data could end up parsed as markup. Found:\n"
        + "\n".join(f"{name}: {ms}" for name, ms in found.items())
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


def test_no_setattribute_call_has_a_non_literal_attribute_name() -> None:
    html = _read_index_html()
    violations = set_attribute_violations(html)
    assert not violations, (
        "Found a .setAttribute(name, ...) call whose attribute-name argument is not "
        "itself a string literal. A dynamic attribute name is a route for "
        "repo-controlled data to set an event-handler attribute (onclick, onerror, "
        "...) as markup rather than through addEventListener, sidestepping this "
        "file's property-assignment discipline. If this is a genuinely reviewed, safe "
        "generic call site (like h()'s own e.setAttribute(k, v), where the key always "
        "comes from a literal object passed at the call site), mark that exact line "
        "with a `// static-guard-allow: <why>` comment explaining why the name can't "
        "be attacker-controlled -- don't loosen this check instead. Offending lines:\n"
        + "\n".join(violations)
    )


def test_the_innerhtml_guard_catches_the_original_vulnerable_pattern() -> None:
    # Proves the check isn't vacuous against the original real-world shape:
    # a multi-line arrow-function body whose template literal (several lines
    # after the `=`) is what actually carries the interpolation. The RHS on
    # the assignment's own line (`visible.map(g => {`) is already not a bare
    # literal, so this is caught without needing to see the `${` at all.
    vulnerable = """
function render() {
  main.innerHTML = visible.map(g => {
    const anyPending = g.vendor.pending || g.products.some(p => p.pending);
    return `<div>${vendorRow(g.vendor)}</div>`;
  }).join("");
}

function next() {}
"""
    assert innerhtml_violations(vulnerable)


def test_the_innerhtml_guard_does_not_flag_a_literal_assignment() -> None:
    safe = """
function render() {
  main.innerHTML = "";
}

function next() {}
"""
    assert not innerhtml_violations(safe)


def test_the_innerhtml_guard_catches_string_concatenation() -> None:
    # No ${} at all here -- the old (pre-widening) guard only looked for
    # template-literal interpolation and would have missed this entirely.
    assert innerhtml_violations('main.innerHTML = "<b>" + name;\n')


def test_the_innerhtml_guard_catches_plus_equals() -> None:
    assert innerhtml_violations("main.innerHTML += userControlled;\n")


def test_the_innerhtml_guard_does_not_flag_plus_equals_with_a_literal() -> None:
    assert not innerhtml_violations('main.innerHTML += "<hr>";\n')


def test_insert_adjacent_html_is_flagged_even_with_a_literal_argument() -> None:
    # Unconditional sink: flagged regardless of whether the argument looks
    # like a literal, unlike innerHTML which gets the bare-literal carve-out.
    found = unconditional_sink_violations('main.insertAdjacentHTML("beforeend", "<hr>");\n')
    assert "insertAdjacentHTML" in found


def test_outer_html_assignment_is_flagged() -> None:
    found = unconditional_sink_violations("el.outerHTML = userControlled;\n")
    assert "outerHTML" in found


def test_document_write_is_flagged() -> None:
    found = unconditional_sink_violations('document.write("<p>hi</p>");\n')
    assert "document.write/writeln" in found


def test_document_writeln_is_flagged() -> None:
    found = unconditional_sink_violations('document.writeln("<p>hi</p>");\n')
    assert "document.write/writeln" in found


def test_create_contextual_fragment_is_flagged() -> None:
    found = unconditional_sink_violations("range.createContextualFragment(userControlled);\n")
    assert "createContextualFragment" in found


def test_the_inline_handler_guard_catches_the_original_vulnerable_pattern() -> None:
    vulnerable = "<button onclick=\"doApprove('${v.path}')\">Approve</button>"
    matches = list(_INLINE_HANDLER_RE.finditer(vulnerable))
    assert matches and any(_INTERPOLATION in m.group(1) for m in matches)


def test_the_inline_handler_guard_does_not_flag_a_static_handler() -> None:
    safe = '<button onclick="openCommitModal()">Commit / PR…</button>'
    matches = list(_INLINE_HANDLER_RE.finditer(safe))
    assert not any(_INTERPOLATION in m.group(1) for m in matches)


def test_setattribute_with_a_variable_name_is_flagged() -> None:
    assert set_attribute_violations("e.setAttribute(k, v);\n")


def test_setattribute_with_a_literal_name_is_not_flagged() -> None:
    assert not set_attribute_violations('e.setAttribute("spellcheck", "false");\n')


def test_setattribute_with_a_variable_name_is_not_flagged_when_allow_marked() -> None:
    line = "e.setAttribute(k, v); // static-guard-allow: k is a literal call-site key\n"
    assert not set_attribute_violations(line)


def test_escapeHtml_is_gone() -> None:
    # escapeHtml() was never a real defence for the attribute/handler-context
    # sinks (it only helps text nodes), and the fix removes the sinks
    # entirely rather than trying to escape harder. Its reappearance is a
    # signal someone is patching over an innerHTML sink instead of removing
    # it -- not a hard failure on its own (the checks above are what
    # actually gate this), but worth keeping visible here.
    html = _read_index_html()
    assert "function escapeHtml" not in html
