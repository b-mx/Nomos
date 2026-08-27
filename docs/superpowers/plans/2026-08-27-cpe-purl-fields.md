# CPE/PURL Fields + NVD Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the `nvd_cpe` alias source to `nvd`, add optional `cpe` (vendor+product) and `purl` (product) fields sourced only from real NVD/OSV data, and ship `tmp/scripts/pull_nvd_cpe.py` — an importer that backfills `cpe` onto existing entries and creates the top-N (by distinct product count) not-yet-covered vendors from the local NVD CPE match dump.

**Architecture:** Two small, mechanical repo-wide changes (rename, schema additions) land first since everything else depends on them. Then pure, independently-testable helper functions (CPE criteria parsing, PURL derivation) go into `tmp/scripts/_lib.py` alongside the existing CISA/endoflife-importer helpers. The importer itself (`tmp/scripts/pull_nvd_cpe.py`) composes those helpers the same way `pull_cisa_kev.py` does: dry-run by default, `--apply` to write, never silently overwrites an existing file's other fields.

**Tech Stack:** Python 3.12, stdlib `tarfile`/`json`/`re`/`argparse`, PyYAML, jsonschema, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-27-cpe-purl-fields-design.md`

## Global Constraints

- `cpe`/`purl` are always version-wildcarded prefixes — never a specific version.
- `cpe` is only ever set from data retrieved from NVD's CPE match dump; `purl` is only ever derived from an existing `osv` alias's `ecosystem`+`value`. Neither is ever guessed from a display name.
- The importer reads the local tarball at `tmp/cache/nvdcpematch-2.0.tar.gz` — it never attempts to download it (NVD blocks scripted fetches; confirmed during design).
- `tmp/scripts/` is gitignored (not part of the committed `tools` package) — its tests are still real pytest files, just invoked directly by path, not part of `uv run pytest`'s default CI run.
- Every `--apply` run must leave `uv run tools/validate.py` passing.
- CPE 2.3 criteria strings can contain escaped colons inside a component (confirmed: ~0.13% of the real dump, mostly Perl module names like `Data::FormValidator` → `data\:\:formvalidator`). The parser must split on *unescaped* colons only.

---

## Task 1: Rename `nvd_cpe` alias source to `nvd`

This must land as one atomic change — a schema-only rename without updating every fixture/data file that uses `source: nvd_cpe` would make the existing test suite fail immediately (the schema's `enum` would reject the old value).

**Files:**
- Modify: `data/schema/vendor.schema.json`, `data/schema/product.schema.json`
- Modify: every `data/vendors/**/*.yaml` with `source: nvd_cpe` (36 occurrences, confirmed via `grep -rc "source: nvd_cpe" data/vendors --include="*.yaml"`)
- Modify: every `tests/fixtures/**/*.yaml` with `source: nvd_cpe` (confirmed present in `valid_tree`, `invalid_unknown_tag`, `invalid_services_on_library`, `invalid_stray_file`, `invalid_orphan_product`, `invalid_duplicate_alias/vendors/acme-one`, `invalid_duplicate_alias/vendors/acme-two`)
- Modify: `tests/fixtures/expected_aliases.json` (2 occurrences)
- Modify: `tests/test_build_index.py` (4 assertions reference the literal string `"nvd_cpe"`)
- Modify: `tests/test_suggest_match.py` (1 mock reference)
- Modify: `tmp/scripts/pull_endoflife.py` (3 hardcoded `"nvd_cpe"` string literals)
- Modify: `tools/_common.py`, `tools/validate.py`, `tools/build_index.py`, `tools/suggest_match.py` (doc-comment prose only — no source-string logic to change, `by-source/<source>.json` grouping is data-driven)
- Modify: `README.md`, `CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `site/search.js` (prose references)

**Interfaces:** None — this task changes data/config only, no new functions.

- [ ] **Step 1: Confirm current test suite passes before touching anything**

Run: `cd /Users/teuf/Code/plopsec-core-graph/Nomos && uv run pytest -q`
Expected: `27 passed`

- [ ] **Step 2: Rename the schema enum value**

In `data/schema/vendor.schema.json` and `data/schema/product.schema.json`, in the `$defs.alias.properties.source.enum` array, change:
```json
"source": { "enum": ["nvd_cpe", "cisa_kev", "osv", "endoflife"] }
```
to:
```json
"source": { "enum": ["nvd", "cisa_kev", "osv", "endoflife"] }
```
(both files have this identical block)

- [ ] **Step 3: Run validate.py to confirm it now fails everywhere `nvd_cpe` is still used**

Run: `uv run tools/validate.py`
Expected: FAIL with many `schema error` messages citing `'nvd_cpe' is not one of ['nvd', ...]`

- [ ] **Step 4: Rename every `source: nvd_cpe` occurrence across data and test fixtures**

```bash
grep -rl "source: nvd_cpe" data/vendors tests/fixtures --include="*.yaml" --include="*.yml" \
  | xargs sed -i '' 's/source: nvd_cpe/source: nvd/g'
```
(macOS `sed -i ''`; on Linux drop the empty string argument)

- [ ] **Step 5: Fix the JSON fixture**

In `tests/fixtures/expected_aliases.json`, replace both occurrences of `"source": "nvd_cpe"` with `"source": "nvd"`.

- [ ] **Step 6: Fix the hardcoded test assertions**

In `tests/test_build_index.py`, replace every literal `"nvd_cpe"` with `"nvd"` (4 occurrences: the `by_source.keys()` assertion, the `by_source["nvd_cpe"]` length check, the `by-source/nvd_cpe.json` path assertion, and both `sources` list assertions).

In `tests/test_suggest_match.py`, replace the literal `"nvd_cpe"` in the mock alias dict with `"nvd"`.

- [ ] **Step 7: Fix the importer script's hardcoded source strings**

In `tmp/scripts/pull_endoflife.py`, replace all 3 occurrences of the literal string `"nvd_cpe"` with `"nvd"` (the two `product_aliases`/`vendor_aliases` append calls, and the `probe_source` assignment). Leave the docstring comment mentioning "nvd_cpe convention" as prose — update it too, to say "nvd convention" for accuracy.

- [ ] **Step 8: Run the full verification suite**

```bash
uv run ruff check .
uv run mypy --strict tools
uv run pytest -q
uv run tools/validate.py
grep -rn "nvd_cpe" data/ tests/ tools/ tmp/scripts/*.py || echo "clean"
```
Expected: ruff/mypy clean, `27 passed`, `All vendor and product entries are valid.`, and the final grep prints `clean` (no remaining code/data references — doc prose in README/CONTRIBUTING is handled in Step 9, not covered by this grep scope).

- [ ] **Step 9: Update prose references in docs and site**

```bash
grep -rln "nvd_cpe" README.md CONTRIBUTING.md .github/PULL_REQUEST_TEMPLATE.md site/search.js
```
For each file found, update the prose mention of `nvd_cpe` to `nvd` (these are narrative references to the alias source name, e.g. "aliases cite their source (`nvd_cpe`, `cisa_kev`, ...)" → "aliases cite their source (`nvd`, `cisa_kev`, ...)").

- [ ] **Step 10: Final full-repo grep and commit**

```bash
grep -rn "nvd_cpe" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=tmp || echo "clean"
```
Expected: `clean` (tmp/ is excluded here since it's gitignored and Task 1 already handled the one file in it that mattered — `pull_endoflife.py` — in Step 7; nothing else under tmp/ references nvd_cpe yet at this point in the plan).

```bash
git add data/schema data/vendors tests README.md CONTRIBUTING.md .github/PULL_REQUEST_TEMPLATE.md site/search.js tools tmp/scripts/pull_endoflife.py
git commit -m "rename nvd_cpe alias source to nvd"
```

---

## Task 2: Add `cpe` and `purl` schema fields

**Files:**
- Modify: `data/schema/vendor.schema.json`
- Modify: `data/schema/product.schema.json`
- Modify: `tests/fixtures/valid_tree/vendors/acme/vendor.yaml`, `tests/fixtures/valid_tree/vendors/acme/products/widget.yaml` (add example `cpe`/`purl` values so the "valid tree has no schema errors" test exercises the new fields)
- Test: `tests/test_validate.py`

**Interfaces:** None — schema-only change.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_validate.py`:
```python
def test_cpe_field_accepts_well_formed_prefix():
    from tools.validate import validate_schema_conformance

    vendors_dir = FIXTURES / "valid_tree" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    assert validate_schema_conformance(vendors, products) == []


def test_cpe_field_rejects_malformed_string():
    from tools.validate import load_schema
    import jsonschema

    schema = load_schema("product.schema.json")
    data = {
        "id": "widget",
        "vendor_id": "acme",
        "name": "Acme Widget",
        "type": "software",
        "tags": [],
        "cpe": "not-a-cpe-string",
        "aliases": [{"source": "nvd", "value": "widget", "confidence": "curated"}],
    }
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(data))
    assert len(errors) == 1


def test_purl_field_rejects_malformed_string():
    from tools.validate import load_schema
    import jsonschema

    schema = load_schema("product.schema.json")
    data = {
        "id": "widget",
        "vendor_id": "acme",
        "name": "Acme Widget",
        "type": "software",
        "tags": [],
        "purl": "not-a-purl",
        "aliases": [{"source": "nvd", "value": "widget", "confidence": "curated"}],
    }
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(data))
    assert len(errors) == 1
```

- [ ] **Step 2: Run tests to verify the malformed-value tests fail (no pattern to reject them yet) and the valid-tree test passes (no cpe field added to fixtures yet, so nothing to conflict)**

Run: `uv run pytest tests/test_validate.py -k "cpe_field or purl_field" -v`
Expected: `test_cpe_field_accepts_well_formed_prefix` PASSES (nothing to validate yet); `test_cpe_field_rejects_malformed_string` and `test_purl_field_rejects_malformed_string` FAIL with `AssertionError: assert 0 == 1` (no `cpe`/`purl` properties exist in the schema yet, so `additionalProperties: false` rejects them for a *different* reason — 1 error either way, so re-run and confirm: if this already shows 1 error, adjust step 2's expectation to note both pass already via the additionalProperties rejection, and step 3 must add the properties AND keep the pattern rejecting bad values)

- [ ] **Step 3: Add the fields to the schemas**

In `data/schema/vendor.schema.json`, add to `properties` (alongside `icon`):
```json
"cpe": {
  "type": "string",
  "pattern": "^cpe:2\\.3:[*aoh]:[^:]+:\\*(:\\*){8}$"
},
```

In `data/schema/product.schema.json`, add to `properties`:
```json
"cpe": {
  "type": "string",
  "pattern": "^cpe:2\\.3:[aoh]:[^:]+:[^:]+:\\*(:\\*){7}$"
},
"purl": {
  "type": "string",
  "pattern": "^pkg:[a-z][a-z0-9.+-]*\\/.+$"
},
```
(vendor `cpe`: part wildcarded (`*`), vendor fixed, then 9 wildcard fields = product,version,update,edition,language,sw_edition,target_sw,target_hw,other. Product `cpe`: part fixed to a/o/h, vendor+product fixed, then 8 wildcard fields.)

- [ ] **Step 4: Run tests again to verify all three pass**

Run: `uv run pytest tests/test_validate.py -k "cpe_field or purl_field" -v`
Expected: all 3 PASS

- [ ] **Step 5: Add real example values to the valid_tree fixture so the general schema-conformance test exercises the new fields**

In `tests/fixtures/valid_tree/vendors/acme/vendor.yaml`, add `cpe: "cpe:2.3:*:acme:*:*:*:*:*:*:*:*:*"` after `name: Acme Corp`.

In `tests/fixtures/valid_tree/vendors/acme/products/widget.yaml`, add after `tags: [database]`:
```yaml
cpe: "cpe:2.3:a:acme:widget:*:*:*:*:*:*:*:*"
purl: "pkg:generic/acme/widget"
```

- [ ] **Step 6: Run the full suite**

```bash
uv run ruff check .
uv run mypy --strict tools
uv run pytest -q
uv run tools/validate.py
```
Expected: all clean, `30 passed` (27 + 3 new), validate passes.

- [ ] **Step 7: Commit**

```bash
git add data/schema tests/fixtures/valid_tree tests/test_validate.py
git commit -m "add optional cpe (vendor+product) and purl (product) schema fields"
```

---

## Task 3: CPE criteria parser + prefix formatter

**Files:**
- Modify: `tmp/scripts/_lib.py`
- Test: `tmp/scripts/test_cpe.py` (new)

**Interfaces:**
- Consumes: nothing new (stdlib `re` only)
- Produces:
  - `split_cpe_criteria(criteria: str) -> list[str]`
  - `parse_cpe_criteria(criteria: str) -> tuple[str, str, str] | None` (returns `(part, vendor_raw, product_raw)`, still CPE-escaped)
  - `unescape_cpe_component(s: str) -> str`
  - `format_cpe_prefix(part: str, vendor: str, product: str | None = None) -> str`
  - `CPE_PART_TO_TYPE: dict[str, str]`

- [ ] **Step 1: Write the failing tests**

Create `tmp/scripts/test_cpe.py`:
```python
"""Tests for the CPE parsing/formatting helpers in _lib.py.

Not part of the committed pytest suite (tmp/ is gitignored) — run directly:
    uv run pytest tmp/scripts/test_cpe.py -v
"""

from _lib import (
    CPE_PART_TO_TYPE,
    format_cpe_prefix,
    parse_cpe_criteria,
    split_cpe_criteria,
    unescape_cpe_component,
)


def test_split_respects_escaped_colons():
    criteria = r"cpe:2.3:a:mark_stosberg:data\:\:formvalidator:4.49_01:*:*:*:*:*:*:*"
    tokens = split_cpe_criteria(criteria)
    assert len(tokens) == 13
    assert tokens[4] == r"data\:\:formvalidator"


def test_split_plain_criteria():
    tokens = split_cpe_criteria("cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*")
    assert tokens == ["cpe", "2.3", "a", "nmap", "nmap", "3.27", "*", "*", "*", "*", "*", "*", "*"]


def test_parse_returns_part_vendor_product():
    result = parse_cpe_criteria("cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*")
    assert result == ("a", "nmap", "nmap")


def test_parse_unescapes_nothing_itself():
    # parse_cpe_criteria returns raw (still-escaped) components; unescaping
    # is a separate, explicit step so callers choose when they need it.
    result = parse_cpe_criteria(r"cpe:2.3:a:mark_stosberg:data\:\:formvalidator:4.49_01:*:*:*:*:*:*:*")
    assert result == ("a", "mark_stosberg", r"data\:\:formvalidator")


def test_parse_rejects_wrong_token_count():
    assert parse_cpe_criteria("cpe:2.3:a:nmap:nmap") is None


def test_parse_rejects_non_cpe_prefix():
    assert parse_cpe_criteria("not:a:cpe:string:at:all:*:*:*:*:*:*:*") is None


def test_parse_rejects_wildcard_vendor_or_product():
    assert parse_cpe_criteria("cpe:2.3:a:*:nmap:3.27:*:*:*:*:*:*:*") is None
    assert parse_cpe_criteria("cpe:2.3:a:nmap:*:3.27:*:*:*:*:*:*:*") is None


def test_unescape_cpe_component():
    assert unescape_cpe_component(r"data\:\:formvalidator") == "data::formvalidator"
    assert unescape_cpe_component("nmap") == "nmap"


def test_format_cpe_prefix_vendor_only():
    assert format_cpe_prefix("*", "cisco") == "cpe:2.3:*:cisco:*:*:*:*:*:*:*:*:*"


def test_format_cpe_prefix_vendor_and_product():
    assert (
        format_cpe_prefix("a", "nmap", "nmap")
        == "cpe:2.3:a:nmap:nmap:*:*:*:*:*:*:*:*"
    )


def test_cpe_part_to_type_mapping():
    assert CPE_PART_TO_TYPE == {"a": "software", "o": "os", "h": "hardware"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/teuf/Code/plopsec-core-graph/Nomos && uv run pytest tmp/scripts/test_cpe.py -v`
Expected: FAIL with `ImportError: cannot import name 'split_cpe_criteria' from '_lib'` (functions don't exist yet)

- [ ] **Step 3: Implement the helpers**

In `tmp/scripts/_lib.py`, add near the top (after the existing imports, before `CACHE_DIR`):
```python
CPE_PART_TO_TYPE = {"a": "software", "o": "os", "h": "hardware"}
```

Add after `slugify()`:
```python
def split_cpe_criteria(criteria: str) -> list[str]:
    """Split a CPE 2.3 formatted string on unescaped colons only.

    CPE 2.3 escapes literal colons inside a component as `\\:` (seen in
    e.g. Perl module names like `Data::FormValidator`) — a naive
    `.split(":")` would corrupt those.
    """
    return re.split(r"(?<!\\):", criteria)


def parse_cpe_criteria(criteria: str) -> tuple[str, str, str] | None:
    """Parse a CPE 2.3 criteria string into (part, vendor, product).

    Returns the vendor/product components RAW (still CPE-escaped) — use
    unescape_cpe_component() separately when you need the natural string
    form (e.g. for matching against alias values). Returns None if the
    string isn't a well-formed 13-token CPE 2.3 URI, or if vendor/product
    is itself wildcarded (not useful for identity mapping).
    """
    tokens = split_cpe_criteria(criteria)
    if len(tokens) != 13 or tokens[0] != "cpe" or tokens[1] != "2.3":
        return None
    part, vendor, product = tokens[2], tokens[3], tokens[4]
    if part not in ("a", "o", "h") or vendor in ("*", "-") or product in ("*", "-"):
        return None
    return part, vendor, product


def unescape_cpe_component(s: str) -> str:
    """Undo CPE 2.3's `\\:` escaping. Only handles colons — the only
    escape sequence confirmed present in the real NVD CPE match dump."""
    return s.replace("\\:", ":")


def format_cpe_prefix(part: str, vendor: str, product: str | None = None) -> str:
    """Build a version-wildcarded CPE 2.3 prefix string.

    format_cpe_prefix("*", "cisco") -> vendor-only form (part wildcarded too,
    since one vendor can span multiple parts).
    format_cpe_prefix("a", "nmap", "nmap") -> vendor+product form.
    """
    if product is None:
        return ":".join(["cpe", "2.3", "*", vendor] + ["*"] * 9)
    return ":".join(["cpe", "2.3", part, vendor, product] + ["*"] * 8)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tmp/scripts/test_cpe.py -v`
Expected: all 11 PASS

- [ ] **Step 5: Type-check and lint**

```bash
uv run mypy --strict tools  # _lib.py isn't in the tools package, so this only confirms tools/ is unaffected
uv run ruff check tmp/scripts/_lib.py tmp/scripts/test_cpe.py
```
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add tmp/scripts/_lib.py tmp/scripts/test_cpe.py
git commit -m "add CPE criteria parser and prefix formatter"
```
(Note: `tmp/` is gitignored, so this commit is local-only bookkeeping for your own history if you've excluded tmp/ from .gitignore locally, or simply a no-op reminder — confirm with `git status tmp/scripts/_lib.py` first; if it reports "ignored", skip the commit and just note the change is complete.)

---

## Task 4: Ecosystem → PURL mapper

**Files:**
- Modify: `tmp/scripts/_lib.py`
- Test: `tmp/scripts/test_purl.py` (new)

**Interfaces:**
- Consumes: nothing new
- Produces: `ecosystem_to_purl(ecosystem: str, value: str) -> str | None`, `ECOSYSTEM_TO_PURL_TYPE: dict[str, str]`

- [ ] **Step 1: Write the failing tests**

Create `tmp/scripts/test_purl.py`:
```python
"""Run directly: uv run pytest tmp/scripts/test_purl.py -v"""

from _lib import ecosystem_to_purl


def test_pypi_maps_directly():
    assert ecosystem_to_purl("PyPI", "pytorch") == "pkg:pypi/pytorch"


def test_npm_maps_directly():
    assert ecosystem_to_purl("npm", "left-pad") == "pkg:npm/left-pad"


def test_maven_splits_group_and_artifact():
    assert ecosystem_to_purl("Maven", "org.apache.logging.log4j:log4j-core") == (
        "pkg:maven/org.apache.logging.log4j/log4j-core"
    )


def test_maven_without_colon_returns_none():
    assert ecosystem_to_purl("Maven", "log4j-core") is None


def test_unknown_ecosystem_returns_none():
    assert ecosystem_to_purl("SomeNewEcosystem", "whatever") is None


def test_cargo_and_golang_and_others():
    assert ecosystem_to_purl("crates.io", "serde") == "pkg:cargo/serde"
    assert ecosystem_to_purl("Go", "github.com/gorilla/mux") == "pkg:golang/github.com/gorilla/mux"
    assert ecosystem_to_purl("NuGet", "Newtonsoft.Json") == "pkg:nuget/Newtonsoft.Json"
    assert ecosystem_to_purl("RubyGems", "rails") == "pkg:gem/rails"
    assert ecosystem_to_purl("Packagist", "monolog/monolog") == "pkg:composer/monolog/monolog"
    assert ecosystem_to_purl("Hex", "phoenix") == "pkg:hex/phoenix"
    assert ecosystem_to_purl("Pub", "http") == "pkg:pub/http"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tmp/scripts/test_purl.py -v`
Expected: FAIL with `ImportError: cannot import name 'ecosystem_to_purl'`

- [ ] **Step 3: Implement the mapper**

In `tmp/scripts/_lib.py`, add near `CPE_PART_TO_TYPE`:
```python
ECOSYSTEM_TO_PURL_TYPE = {
    "PyPI": "pypi",
    "npm": "npm",
    "Maven": "maven",
    "Go": "golang",
    "crates.io": "cargo",
    "NuGet": "nuget",
    "RubyGems": "gem",
    "Packagist": "composer",
    "Hex": "hex",
    "Pub": "pub",
}
```

Add after `format_cpe_prefix()`:
```python
def ecosystem_to_purl(ecosystem: str, value: str) -> str | None:
    """Derive a PURL from an existing osv alias's (ecosystem, value) —
    never a guess, just a reformat of data we already trust. Returns None
    when the ecosystem isn't in the known table, or (for Maven) when
    `value` isn't in the expected groupId:artifactId shape — never
    fabricates a namespace."""
    purl_type = ECOSYSTEM_TO_PURL_TYPE.get(ecosystem)
    if purl_type is None:
        return None
    if purl_type == "maven":
        group_id, sep, artifact_id = value.partition(":")
        if not sep or not group_id or not artifact_id:
            return None
        return f"pkg:maven/{group_id}/{artifact_id}"
    return f"pkg:{purl_type}/{value}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tmp/scripts/test_purl.py -v`
Expected: all 7 PASS

- [ ] **Step 5: Lint**

Run: `uv run ruff check tmp/scripts/_lib.py tmp/scripts/test_purl.py`
Expected: clean

- [ ] **Step 6: Commit** (or confirm gitignored, per Task 3 Step 6's note)

---

## Task 5: Extend `NewVendor`/`NewProduct` and the write functions for `cpe`/`purl`

**Files:**
- Modify: `tmp/scripts/_lib.py`
- Test: `tmp/scripts/test_write.py` (new)

**Interfaces:**
- Consumes: `NewVendor`, `NewProduct`, `write_new_vendor`, `write_new_product` (existing, being extended — no signature removed, only optional fields added)
- Produces: `NewVendor.cpe: str | None = None`, `NewProduct.cpe: str | None = None`, `NewProduct.purl: str | None = None`, `VENDOR_KEY_ORDER: list[str]`, `PRODUCT_KEY_ORDER: list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tmp/scripts/test_write.py`:
```python
"""Run directly: uv run pytest tmp/scripts/test_write.py -v"""

import shutil
from pathlib import Path

import pytest
import yaml

import _lib


@pytest.fixture
def isolated_vendors_dir(tmp_path, monkeypatch):
    vendors_dir = tmp_path / "vendors"
    vendors_dir.mkdir()
    monkeypatch.setattr(_lib, "VENDORS_DIR", vendors_dir)
    yield vendors_dir
    shutil.rmtree(vendors_dir, ignore_errors=True)


def test_write_new_vendor_includes_cpe_when_set(isolated_vendors_dir):
    v = _lib.NewVendor(
        id="acme",
        name="Acme Corp",
        aliases=[{"source": "nvd", "value": "acme", "confidence": "auto"}],
        cpe="cpe:2.3:*:acme:*:*:*:*:*:*:*:*:*",
    )
    path = _lib.write_new_vendor(v)
    data = yaml.safe_load(path.read_text())
    assert data["cpe"] == "cpe:2.3:*:acme:*:*:*:*:*:*:*:*:*"
    assert list(data.keys()) == ["id", "name", "cpe", "aliases"]


def test_write_new_vendor_omits_cpe_when_none(isolated_vendors_dir):
    v = _lib.NewVendor(
        id="acme", name="Acme Corp", aliases=[{"source": "nvd", "value": "acme", "confidence": "auto"}]
    )
    path = _lib.write_new_vendor(v)
    data = yaml.safe_load(path.read_text())
    assert "cpe" not in data
    assert list(data.keys()) == ["id", "name", "aliases"]


def test_write_new_product_includes_cpe_and_purl(isolated_vendors_dir):
    p = _lib.NewProduct(
        id="widget",
        vendor_id="acme",
        name="Widget",
        type="software",
        tags=[],
        aliases=[{"source": "nvd", "value": "widget", "confidence": "auto"}],
        cpe="cpe:2.3:a:acme:widget:*:*:*:*:*:*:*:*",
        purl="pkg:generic/acme/widget",
    )
    path = _lib.write_new_product(p)
    data = yaml.safe_load(path.read_text())
    assert data["cpe"] == "cpe:2.3:a:acme:widget:*:*:*:*:*:*:*:*"
    assert data["purl"] == "pkg:generic/acme/widget"
    assert list(data.keys()) == ["id", "vendor_id", "name", "type", "tags", "cpe", "purl", "aliases"]


def test_write_new_product_omits_cpe_and_purl_when_none(isolated_vendors_dir):
    p = _lib.NewProduct(
        id="widget",
        vendor_id="acme",
        name="Widget",
        type="software",
        tags=[],
        aliases=[{"source": "nvd", "value": "widget", "confidence": "auto"}],
    )
    path = _lib.write_new_product(p)
    data = yaml.safe_load(path.read_text())
    assert "cpe" not in data
    assert "purl" not in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tmp/scripts/test_write.py -v`
Expected: FAIL — `TypeError: NewVendor.__init__() got an unexpected keyword argument 'cpe'`

- [ ] **Step 3: Extend the dataclasses and write functions**

In `tmp/scripts/_lib.py`, change:
```python
@dataclass
class NewVendor:
    id: str
    name: str
    aliases: list[dict[str, str]]
```
to:
```python
@dataclass
class NewVendor:
    id: str
    name: str
    aliases: list[dict[str, str]]
    cpe: str | None = None
```

Change:
```python
@dataclass
class NewProduct:
    id: str
    vendor_id: str
    name: str
    type: str
    tags: list[str]
    aliases: list[dict[str, str]]
```
to:
```python
@dataclass
class NewProduct:
    id: str
    vendor_id: str
    name: str
    type: str
    tags: list[str]
    aliases: list[dict[str, str]]
    cpe: str | None = None
    purl: str | None = None
```

Add near the top (after `CPE_PART_TO_TYPE`):
```python
VENDOR_KEY_ORDER = ["id", "name", "cpe", "icon", "aliases"]
PRODUCT_KEY_ORDER = ["id", "vendor_id", "name", "type", "tags", "cpe", "purl", "icon", "aliases", "services"]
```

Change `write_new_vendor`'s body from:
```python
    vendor_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"id": v.id, "name": v.name, "aliases": v.aliases}
    path.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True))
    return path
```
to:
```python
    vendor_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"id": v.id, "name": v.name}
    if v.cpe:
        data["cpe"] = v.cpe
    data["aliases"] = v.aliases
    path.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True))
    return path
```

Change `write_new_product`'s body from:
```python
    products_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "id": p.id,
        "vendor_id": p.vendor_id,
        "name": p.name,
        "type": p.type,
        "tags": p.tags,
        "aliases": p.aliases,
    }
    path.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True))
    return path
```
to:
```python
    products_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "id": p.id,
        "vendor_id": p.vendor_id,
        "name": p.name,
        "type": p.type,
        "tags": p.tags,
    }
    if p.cpe:
        data["cpe"] = p.cpe
    if p.purl:
        data["purl"] = p.purl
    data["aliases"] = p.aliases
    path.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True))
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tmp/scripts/test_write.py -v`
Expected: all 4 PASS

- [ ] **Step 5: Regression-check the existing importers still work**

```bash
uv run pytest tmp/scripts/ -v
uv run ruff check tmp/scripts/
```
Expected: all pass (existing `NewVendor(id=..., name=..., aliases=...)` call sites in `pull_cisa_kev.py`/`pull_endoflife.py` still work unchanged since `cpe`/`purl` default to `None`)

- [ ] **Step 6: Commit** (or confirm gitignored)

---

## Task 6: Reduced CPE map builder

**Files:**
- Create: `tmp/scripts/pull_nvd_cpe.py`
- Test: `tmp/scripts/test_pull_nvd_cpe.py` (new)

**Interfaces:**
- Consumes: `parse_cpe_criteria`, `unescape_cpe_component` from `_lib.py` (Task 3); `CACHE_DIR` from `_lib.py` (existing)
- Produces: `build_reduced_cpe_map(tarball_path: Path, cache_path: Path, *, refresh: bool = False) -> dict[str, dict[str, str]]` (`{vendor: {product: part}}`)

- [ ] **Step 1: Write the failing test**

Create `tmp/scripts/test_pull_nvd_cpe.py`:
```python
"""Run directly: uv run pytest tmp/scripts/test_pull_nvd_cpe.py -v

Uses a small synthetic tarball, never the real ~765MB NVD dump.
"""

import json
import tarfile
from pathlib import Path

from pull_nvd_cpe import build_reduced_cpe_map


def _make_chunk(match_strings):
    return {
        "resultsPerPage": len(match_strings),
        "startIndex": 0,
        "totalResults": len(match_strings),
        "format": "NVD_CPEMatchString",
        "version": "2.0",
        "timestamp": "2026-08-26T00:00:00.000",
        "matchStrings": [{"matchString": {"criteria": c}} for c in match_strings],
    }


def _make_tarball(tmp_path: Path, chunks: list[dict]) -> Path:
    tarball = tmp_path / "nvdcpematch-2.0.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        for i, chunk in enumerate(chunks, start=1):
            chunk_path = tmp_path / f"chunk-{i:05d}.json"
            chunk_path.write_text(json.dumps(chunk))
            tar.add(chunk_path, arcname=f"nvdcpematch-2.0-chunks/nvdcpematch-2.0-chunk-{i:05d}.json")
    return tarball


def test_build_reduced_map_dedupes_versions_across_chunks(tmp_path):
    chunk1 = _make_chunk(
        [
            "cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*",
            "cpe:2.3:a:nmap:nmap:3.30:*:*:*:*:*:*:*",
            "cpe:2.3:h:cisco:asa:9.1:*:*:*:*:*:*:*",
        ]
    )
    chunk2 = _make_chunk(
        [
            "cpe:2.3:a:nmap:nmap:3.31:*:*:*:*:*:*:*",  # same product, another version
            "cpe:2.3:a:nmap:ncat:7.9:*:*:*:*:*:*:*",  # same vendor, different product
        ]
    )
    tarball = _make_tarball(tmp_path, [chunk1, chunk2])
    cache_path = tmp_path / "reduced.json"

    result = build_reduced_cpe_map(tarball, cache_path)

    assert result == {
        "nmap": {"nmap": "a", "ncat": "a"},
        "cisco": {"asa": "h"},
    }


def test_build_reduced_map_handles_escaped_colons(tmp_path):
    chunk = _make_chunk([r"cpe:2.3:a:mark_stosberg:data\:\:formvalidator:4.49_01:*:*:*:*:*:*:*"])
    tarball = _make_tarball(tmp_path, [chunk])
    cache_path = tmp_path / "reduced.json"

    result = build_reduced_cpe_map(tarball, cache_path)

    assert result == {"mark_stosberg": {"data::formvalidator": "a"}}


def test_build_reduced_map_skips_malformed_criteria(tmp_path):
    chunk = _make_chunk(["not-a-real-cpe-string", "cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*"])
    tarball = _make_tarball(tmp_path, [chunk])
    cache_path = tmp_path / "reduced.json"

    result = build_reduced_cpe_map(tarball, cache_path)

    assert result == {"nmap": {"nmap": "a"}}


def test_build_reduced_map_uses_cache_when_fresh(tmp_path):
    chunk = _make_chunk(["cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*"])
    tarball = _make_tarball(tmp_path, [chunk])
    cache_path = tmp_path / "reduced.json"

    first = build_reduced_cpe_map(tarball, cache_path)
    assert cache_path.exists()

    # Overwrite the tarball with different content but an OLDER mtime than
    # the cache — the cache should win.
    import time

    time.sleep(0.01)
    cache_path.write_text(json.dumps({"stale": {"marker": "a"}}))
    cache_path.touch()  # ensure cache mtime > tarball mtime

    second = build_reduced_cpe_map(tarball, cache_path)
    assert second == {"stale": {"marker": "a"}}
    assert second != first


def test_build_reduced_map_raises_clear_error_when_tarball_missing(tmp_path):
    import pytest

    missing = tmp_path / "does-not-exist.tar.gz"
    with pytest.raises(FileNotFoundError, match="nvd.nist.gov"):
        build_reduced_cpe_map(missing, tmp_path / "reduced.json")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tmp/scripts/test_pull_nvd_cpe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pull_nvd_cpe'`

- [ ] **Step 3: Create `tmp/scripts/pull_nvd_cpe.py` with the reduced-map builder**

```python
#!/usr/bin/env python3
"""Backfill cpe onto existing entries and import top-N vendors from NVD's
CPE match dump into data/vendors/.

Dry-run by default — prints a report of what it would do. Pass --apply to
write. Never edits an existing file's other fields — the backfill pass
only ever adds a missing `cpe`/`purl` field, and new-file creation refuses
to overwrite a path that already exists (see write_new_vendor/product).

This does NOT download the NVD CPE match feed itself — NVD blocks
scripted fetches. Download it manually from:
    https://nvd.nist.gov/feeds/json/cpematch/2.0/nvdcpematch-2.0.tar.gz
and place it at tmp/cache/nvdcpematch-2.0.tar.gz.

Usage:
    uv run tmp/scripts/pull_nvd_cpe.py                  # dry run
    uv run tmp/scripts/pull_nvd_cpe.py --apply           # write new files + backfill
    uv run tmp/scripts/pull_nvd_cpe.py --top-n 200        # only top 200 vendors (default 1000)
    uv run tmp/scripts/pull_nvd_cpe.py --refresh          # re-parse the tarball, bypass the reduced-map cache
"""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

from _lib import (
    CACHE_DIR,
    parse_cpe_criteria,
    unescape_cpe_component,
)

NVD_CPE_TARBALL = CACHE_DIR / "nvdcpematch-2.0.tar.gz"
NVD_CPE_REDUCED_CACHE = CACHE_DIR / "nvd-cpe-reduced.json"


def build_reduced_cpe_map(
    tarball_path: Path = NVD_CPE_TARBALL,
    cache_path: Path = NVD_CPE_REDUCED_CACHE,
    *,
    refresh: bool = False,
) -> dict[str, dict[str, str]]:
    """Stream the NVD CPE match tarball into {vendor: {product: part}}.

    Cached at cache_path; reused whenever the cache is newer than the
    source tarball (parsing the full ~3.5GB uncompressed feed is the
    expensive part, and it doesn't change between reruns of this script).
    """
    if not tarball_path.exists():
        raise FileNotFoundError(
            f"{tarball_path} not found. Download it manually from "
            "https://nvd.nist.gov/feeds/json/cpematch/2.0/nvdcpematch-2.0.tar.gz "
            "(nvd.nist.gov blocks scripted downloads, so this can't be automated) "
            "and place it there."
        )

    if (
        not refresh
        and cache_path.exists()
        and cache_path.stat().st_mtime > tarball_path.stat().st_mtime
    ):
        return dict(json.loads(cache_path.read_text()))

    result: dict[str, dict[str, str]] = {}
    malformed = 0
    with tarfile.open(tarball_path, "r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            fileobj = tar.extractfile(member)
            if fileobj is None:
                continue
            chunk = json.load(fileobj)
            for entry in chunk.get("matchStrings", []):
                criteria = entry.get("matchString", {}).get("criteria", "")
                parsed = parse_cpe_criteria(criteria)
                if parsed is None:
                    malformed += 1
                    continue
                part, vendor_raw, product_raw = parsed
                vendor = unescape_cpe_component(vendor_raw)
                product = unescape_cpe_component(product_raw)
                result.setdefault(vendor, {})[product] = part

    if malformed:
        print(f"  {malformed} malformed/unparseable criteria strings skipped", file=sys.stderr)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result))
    return result


if __name__ == "__main__":
    print("This script isn't runnable standalone yet — Task 10 adds main().")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tmp/scripts/test_pull_nvd_cpe.py -v`
Expected: all 5 PASS

- [ ] **Step 5: Lint**

Run: `uv run ruff check tmp/scripts/pull_nvd_cpe.py tmp/scripts/test_pull_nvd_cpe.py`
Expected: clean

- [ ] **Step 6: Commit** (or confirm gitignored)

---

## Task 7: Backfill `cpe` onto existing entries

**Files:**
- Modify: `tmp/scripts/pull_nvd_cpe.py`
- Modify: `tmp/scripts/test_pull_nvd_cpe.py`

**Interfaces:**
- Consumes: `ExistingData`, `load_existing()` from `_lib.py` (existing); `format_cpe_prefix` from `_lib.py` (Task 3); `build_reduced_cpe_map` (Task 6)
- Produces: `backfill_cpe_fields(existing: ExistingData, reduced_map: dict[str, dict[str, str]], *, apply: bool) -> tuple[int, int]` (returns `(vendors_matched, products_matched)`, regardless of `apply` — only writes when `apply=True`)

- [ ] **Step 1: Write the failing tests**

Add to `tmp/scripts/test_pull_nvd_cpe.py`:
```python
import shutil

import yaml

from _lib import ExistingData, ProductEntry, VendorEntry, load_existing
from pull_nvd_cpe import backfill_cpe_fields


def _write_vendor(vendors_dir: Path, vendor_id: str, nvd_value: str) -> Path:
    vendor_dir = vendors_dir / vendor_id
    vendor_dir.mkdir(parents=True)
    path = vendor_dir / "vendor.yaml"
    path.write_text(
        yaml.dump(
            {
                "id": vendor_id,
                "name": vendor_id.title(),
                "aliases": [{"source": "nvd", "value": nvd_value, "confidence": "curated"}],
            }
        )
    )
    return path


def _write_product(vendors_dir: Path, vendor_id: str, product_id: str, nvd_value: str) -> Path:
    products_dir = vendors_dir / vendor_id / "products"
    products_dir.mkdir(parents=True, exist_ok=True)
    path = products_dir / f"{product_id}.yaml"
    path.write_text(
        yaml.dump(
            {
                "id": product_id,
                "vendor_id": vendor_id,
                "name": product_id.title(),
                "type": "software",
                "tags": [],
                "aliases": [{"source": "nvd", "value": nvd_value, "confidence": "curated"}],
            }
        )
    )
    return path


def _load_existing_data(vendors_dir: Path) -> ExistingData:
    vendors = [
        VendorEntry(path=p, data=yaml.safe_load(p.read_text()))
        for p in sorted(vendors_dir.glob("*/vendor.yaml"))
    ]
    products = []
    for p in sorted(vendors_dir.glob("*/products/*.yaml")):
        data = yaml.safe_load(p.read_text())
        products.append(ProductEntry(path=p, data=data, vendor_id=data["vendor_id"]))
    return ExistingData(
        vendors=vendors, products=products, vendor_alias_index=[], product_alias_index=[], known_tags=set()
    )


def test_backfill_sets_cpe_on_matching_vendor_and_product(tmp_path):
    vendors_dir = tmp_path / "vendors"
    _write_vendor(vendors_dir, "cisco", "cisco")
    _write_product(vendors_dir, "cisco", "asa", "asa")
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"cisco": {"asa": "h"}}

    v_count, p_count = backfill_cpe_fields(existing, reduced_map, apply=True)

    assert (v_count, p_count) == (1, 1)
    vendor_data = yaml.safe_load((vendors_dir / "cisco" / "vendor.yaml").read_text())
    assert vendor_data["cpe"] == "cpe:2.3:*:cisco:*:*:*:*:*:*:*:*:*"
    product_data = yaml.safe_load((vendors_dir / "cisco" / "products" / "asa.yaml").read_text())
    assert product_data["cpe"] == "cpe:2.3:h:cisco:asa:*:*:*:*:*:*:*:*"


def test_backfill_skips_entries_with_no_nvd_match(tmp_path):
    vendors_dir = tmp_path / "vendors"
    _write_vendor(vendors_dir, "acme", "acme")
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"cisco": {"asa": "h"}}  # no "acme" in here

    v_count, p_count = backfill_cpe_fields(existing, reduced_map, apply=True)

    assert (v_count, p_count) == (0, 0)
    vendor_data = yaml.safe_load((vendors_dir / "acme" / "vendor.yaml").read_text())
    assert "cpe" not in vendor_data


def test_backfill_does_not_write_when_apply_false(tmp_path):
    vendors_dir = tmp_path / "vendors"
    _write_vendor(vendors_dir, "cisco", "cisco")
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"cisco": {}}

    v_count, p_count = backfill_cpe_fields(existing, reduced_map, apply=False)

    assert v_count == 1
    vendor_data = yaml.safe_load((vendors_dir / "cisco" / "vendor.yaml").read_text())
    assert "cpe" not in vendor_data  # counted the match, but did not write


def test_backfill_never_overwrites_an_existing_cpe(tmp_path):
    vendors_dir = tmp_path / "vendors"
    path = _write_vendor(vendors_dir, "cisco", "cisco")
    data = yaml.safe_load(path.read_text())
    data["cpe"] = "cpe:2.3:*:cisco:*:*:*:*:*:*:*:*:*"
    path.write_text(yaml.dump(data))
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"cisco": {}}

    v_count, _ = backfill_cpe_fields(existing, reduced_map, apply=True)

    assert v_count == 0  # already had cpe, not counted as a new match
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tmp/scripts/test_pull_nvd_cpe.py -k backfill -v`
Expected: FAIL — `ImportError: cannot import name 'backfill_cpe_fields'`

- [ ] **Step 3: Implement `backfill_cpe_fields`**

In `tmp/scripts/pull_nvd_cpe.py`, update the import line to add the new dependencies:
```python
from _lib import (
    CACHE_DIR,
    VENDOR_KEY_ORDER,
    PRODUCT_KEY_ORDER,
    ExistingData,
    format_cpe_prefix,
    parse_cpe_criteria,
    unescape_cpe_component,
)
```

Add after `build_reduced_cpe_map`:
```python
def _reorder(data: dict, order: list[str]) -> dict:
    return {k: data[k] for k in order if k in data}


def _nvd_alias_value(data: dict) -> str | None:
    return next((a["value"] for a in data.get("aliases", []) if a.get("source") == "nvd"), None)


def backfill_cpe_fields(
    existing: ExistingData, reduced_map: dict[str, dict[str, str]], *, apply: bool
) -> tuple[int, int]:
    """Set `cpe` on any existing vendor/product whose `nvd` alias exactly
    matches an entry in reduced_map and doesn't already have `cpe` set.
    Returns (vendors_matched, products_matched) — counted whether or not
    `apply` is True; files are only written when `apply=True`."""
    import yaml

    vendors_matched = 0
    products_matched = 0

    for vendor in existing.vendors:
        if vendor.data.get("cpe"):
            continue
        nvd_value = _nvd_alias_value(vendor.data)
        if nvd_value is None or nvd_value not in reduced_map:
            continue
        vendors_matched += 1
        if apply:
            data = dict(vendor.data)
            data["cpe"] = format_cpe_prefix("*", nvd_value)
            vendor.path.write_text(
                yaml.dump(_reorder(data, VENDOR_KEY_ORDER), sort_keys=False, allow_unicode=True)
            )

    for product in existing.products:
        if product.data.get("cpe"):
            continue
        nvd_value = _nvd_alias_value(product.data)
        if nvd_value is None:
            continue
        vendor_entry = existing.vendor_by_id(product.vendor_id)
        if vendor_entry is None:
            continue
        vendor_nvd = _nvd_alias_value(vendor_entry.data)
        if vendor_nvd is None:
            continue
        part = reduced_map.get(vendor_nvd, {}).get(nvd_value)
        if part is None:
            continue
        products_matched += 1
        if apply:
            data = dict(product.data)
            data["cpe"] = format_cpe_prefix(part, vendor_nvd, nvd_value)
            product.path.write_text(
                yaml.dump(_reorder(data, PRODUCT_KEY_ORDER), sort_keys=False, allow_unicode=True)
            )

    return vendors_matched, products_matched
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tmp/scripts/test_pull_nvd_cpe.py -k backfill -v`
Expected: all 4 PASS

- [ ] **Step 5: Run the full tmp/scripts/ test suite and lint**

```bash
uv run pytest tmp/scripts/ -v
uv run ruff check tmp/scripts/
```
Expected: all pass, clean

- [ ] **Step 6: Commit** (or confirm gitignored)

---

## Task 8: New-coverage pass (top-N vendors)

**Files:**
- Modify: `tmp/scripts/pull_nvd_cpe.py`
- Modify: `tmp/scripts/test_pull_nvd_cpe.py`

**Interfaces:**
- Consumes: `resolve_against_index`, `slugify`, `NewVendor`, `NewProduct`, `CPE_PART_TO_TYPE` from `_lib.py`; `ExistingData` (Task 6/7)
- Produces: `select_top_vendors(reduced_map: dict[str, dict[str, str]], top_n: int) -> list[str]`, `create_new_coverage(existing: ExistingData, reduced_map: dict[str, dict[str, str]], top_n: int, threshold: int) -> tuple[list[NewVendor], list[NewProduct]]`

- [ ] **Step 1: Write the failing tests**

Add to `tmp/scripts/test_pull_nvd_cpe.py`:
```python
from pull_nvd_cpe import create_new_coverage, select_top_vendors


def test_select_top_vendors_ranks_by_distinct_product_count():
    reduced_map = {
        "cisco": {"asa": "h", "ios": "o", "ftd": "h"},
        "nmap": {"nmap": "a"},
        "microsoft": {"windows": "o", "office": "a"},
    }
    assert select_top_vendors(reduced_map, top_n=2) == ["cisco", "microsoft"]


def test_create_new_coverage_creates_vendor_and_products_for_unmapped_vendor(tmp_path):
    vendors_dir = tmp_path / "vendors"
    vendors_dir.mkdir()
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"newvendor": {"newproduct": "a"}}

    new_vendors, new_products = create_new_coverage(existing, reduced_map, top_n=10, threshold=85)

    assert len(new_vendors) == 1
    assert new_vendors[0].id == "newvendor"
    assert new_vendors[0].cpe == "cpe:2.3:*:newvendor:*:*:*:*:*:*:*:*:*"
    assert len(new_products) == 1
    assert new_products[0].id == "newproduct"
    assert new_products[0].vendor_id == "newvendor"
    assert new_products[0].type == "software"
    assert new_products[0].cpe == "cpe:2.3:a:newvendor:newproduct:*:*:*:*:*:*:*:*"


def test_create_new_coverage_only_creates_missing_products_for_known_vendor(tmp_path):
    vendors_dir = tmp_path / "vendors"
    _write_vendor(vendors_dir, "cisco", "cisco")
    _write_product(vendors_dir, "cisco", "asa", "asa")
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"cisco": {"asa": "h", "ios": "o"}}  # asa already exists, ios doesn't

    new_vendors, new_products = create_new_coverage(existing, reduced_map, top_n=10, threshold=85)

    assert new_vendors == []
    assert len(new_products) == 1
    assert new_products[0].id == "ios"
    assert new_products[0].type == "os"


def test_create_new_coverage_respects_top_n(tmp_path):
    vendors_dir = tmp_path / "vendors"
    vendors_dir.mkdir()
    existing = _load_existing_data(vendors_dir)
    reduced_map = {
        "big-vendor": {"p1": "a", "p2": "a"},
        "small-vendor": {"p1": "a"},
    }

    new_vendors, _ = create_new_coverage(existing, reduced_map, top_n=1, threshold=85)

    assert [v.id for v in new_vendors] == ["big-vendor"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tmp/scripts/test_pull_nvd_cpe.py -k "top_vendors or new_coverage" -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement**

In `tmp/scripts/pull_nvd_cpe.py`, update the `_lib` import to add:
```python
from _lib import (
    CACHE_DIR,
    VENDOR_KEY_ORDER,
    PRODUCT_KEY_ORDER,
    CPE_PART_TO_TYPE,
    ExistingData,
    NewProduct,
    NewVendor,
    format_cpe_prefix,
    parse_cpe_criteria,
    resolve_against_index,
    slugify,
    unescape_cpe_component,
)

SOURCE = "nvd"
```

Add after `backfill_cpe_fields`:
```python
def select_top_vendors(reduced_map: dict[str, dict[str, str]], top_n: int) -> list[str]:
    return sorted(reduced_map, key=lambda v: len(reduced_map[v]), reverse=True)[:top_n]


def create_new_coverage(
    existing: ExistingData,
    reduced_map: dict[str, dict[str, str]],
    top_n: int,
    threshold: int,
) -> tuple[list[NewVendor], list[NewProduct]]:
    vendor_entries = existing.vendor_entries_with_aliases()
    new_vendors: dict[str, NewVendor] = {}
    new_products: list[NewProduct] = []

    for cpe_vendor in select_top_vendors(reduced_map, top_n):
        vendor_res = resolve_against_index(
            cpe_vendor, SOURCE, vendor_entries, existing.vendor_alias_index, threshold, cpe_vendor
        )
        if vendor_res.status == "review":
            continue
        if vendor_res.status == "mapped":
            vendor_id = vendor_res.canonical_id or ""
            vendor_known = True
        else:
            vendor_id = slugify(cpe_vendor)
            if not vendor_id:
                continue
            vendor_known = False

        product_entries = existing.product_entries_with_aliases(vendor_id) if vendor_known else []
        product_alias_index = existing.product_alias_index_for_vendor(vendor_id) if vendor_known else []

        vendor_new_products = []
        for cpe_product, part in reduced_map[cpe_vendor].items():
            product_res = resolve_against_index(
                cpe_product, SOURCE, product_entries, product_alias_index, threshold, cpe_product
            )
            if product_res.status != "new":
                continue
            product_slug = slugify(cpe_product)
            if not product_slug:
                continue
            vendor_new_products.append(
                NewProduct(
                    id=product_slug,
                    vendor_id=vendor_id,
                    name=cpe_product.replace("_", " ").title(),
                    type=CPE_PART_TO_TYPE.get(part, "software"),
                    tags=[],
                    aliases=[{"source": SOURCE, "value": cpe_product, "confidence": "auto"}],
                    cpe=format_cpe_prefix(part, cpe_vendor, cpe_product),
                )
            )

        if not vendor_known and vendor_id not in new_vendors and vendor_new_products:
            new_vendors[vendor_id] = NewVendor(
                id=vendor_id,
                name=cpe_vendor.replace("_", " ").title(),
                aliases=[{"source": SOURCE, "value": cpe_vendor, "confidence": "auto"}],
                cpe=format_cpe_prefix("*", cpe_vendor),
            )

        new_products.extend(vendor_new_products)

    return list(new_vendors.values()), new_products
```

Note: `vendor_new_products` is only added to `new_products` for a genuinely-new vendor if it has at least one product (`if ... and vendor_new_products`) — no point creating an empty vendor shell if every one of its products already resolved as "mapped" or "review".

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tmp/scripts/test_pull_nvd_cpe.py -k "top_vendors or new_coverage" -v`
Expected: all 4 PASS

- [ ] **Step 5: Run the full tmp/scripts/ test suite and lint**

```bash
uv run pytest tmp/scripts/ -v
uv run ruff check tmp/scripts/
```
Expected: all pass, clean

- [ ] **Step 6: Commit** (or confirm gitignored)

---

## Task 9: `purl` backfill pass

**Files:**
- Modify: `tmp/scripts/pull_nvd_cpe.py`
- Modify: `tmp/scripts/test_pull_nvd_cpe.py`

**Interfaces:**
- Consumes: `ecosystem_to_purl` from `_lib.py` (Task 4); `ExistingData`
- Produces: `backfill_purl_fields(existing: ExistingData, *, apply: bool) -> int`

- [ ] **Step 1: Write the failing tests**

Add to `tmp/scripts/test_pull_nvd_cpe.py`:
```python
from pull_nvd_cpe import backfill_purl_fields


def _write_product_with_osv(vendors_dir: Path, vendor_id: str, product_id: str, ecosystem: str, value: str) -> Path:
    products_dir = vendors_dir / vendor_id / "products"
    products_dir.mkdir(parents=True, exist_ok=True)
    path = products_dir / f"{product_id}.yaml"
    path.write_text(
        yaml.dump(
            {
                "id": product_id,
                "vendor_id": vendor_id,
                "name": product_id.title(),
                "type": "library",
                "tags": [],
                "aliases": [
                    {"source": "osv", "value": value, "ecosystem": ecosystem, "confidence": "curated"}
                ],
            }
        )
    )
    return path


def test_backfill_purl_sets_purl_from_osv_alias(tmp_path):
    vendors_dir = tmp_path / "vendors"
    _write_product_with_osv(vendors_dir, "pytorch", "pytorch", "PyPI", "torch")
    existing = _load_existing_data(vendors_dir)

    count = backfill_purl_fields(existing, apply=True)

    assert count == 1
    data = yaml.safe_load((vendors_dir / "pytorch" / "products" / "pytorch.yaml").read_text())
    assert data["purl"] == "pkg:pypi/torch"


def test_backfill_purl_skips_products_without_osv_alias(tmp_path):
    vendors_dir = tmp_path / "vendors"
    _write_product(vendors_dir, "cisco", "asa", "asa")  # nvd alias only, no osv
    existing = _load_existing_data(vendors_dir)

    count = backfill_purl_fields(existing, apply=True)

    assert count == 0


def test_backfill_purl_skips_unknown_ecosystem(tmp_path):
    vendors_dir = tmp_path / "vendors"
    _write_product_with_osv(vendors_dir, "acme", "widget", "SomeNewEcosystem", "widget")
    existing = _load_existing_data(vendors_dir)

    count = backfill_purl_fields(existing, apply=True)

    assert count == 0
    data = yaml.safe_load((vendors_dir / "acme" / "products" / "widget.yaml").read_text())
    assert "purl" not in data


def test_backfill_purl_does_not_write_when_apply_false(tmp_path):
    vendors_dir = tmp_path / "vendors"
    _write_product_with_osv(vendors_dir, "pytorch", "pytorch", "PyPI", "torch")
    existing = _load_existing_data(vendors_dir)

    count = backfill_purl_fields(existing, apply=False)

    assert count == 1
    data = yaml.safe_load((vendors_dir / "pytorch" / "products" / "pytorch.yaml").read_text())
    assert "purl" not in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tmp/scripts/test_pull_nvd_cpe.py -k backfill_purl -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement**

In `tmp/scripts/pull_nvd_cpe.py`, add `ecosystem_to_purl` to the `_lib` import list, then add after `create_new_coverage`:
```python
def backfill_purl_fields(existing: ExistingData, *, apply: bool) -> int:
    """Set `purl` on any product with an osv alias whose ecosystem maps to
    a known PURL type and doesn't already have `purl` set. Returns the
    match count regardless of `apply`; only writes when `apply=True`."""
    import yaml

    matched = 0
    for product in existing.products:
        if product.data.get("purl"):
            continue
        osv_alias = next((a for a in product.data.get("aliases", []) if a.get("source") == "osv"), None)
        if osv_alias is None or "ecosystem" not in osv_alias:
            continue
        purl = ecosystem_to_purl(osv_alias["ecosystem"], osv_alias["value"])
        if purl is None:
            continue
        matched += 1
        if apply:
            data = dict(product.data)
            data["purl"] = purl
            product.path.write_text(
                yaml.dump(_reorder(data, PRODUCT_KEY_ORDER), sort_keys=False, allow_unicode=True)
            )
    return matched
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tmp/scripts/test_pull_nvd_cpe.py -k backfill_purl -v`
Expected: all 4 PASS

- [ ] **Step 5: Run the full tmp/scripts/ test suite and lint**

```bash
uv run pytest tmp/scripts/ -v
uv run ruff check tmp/scripts/
```
Expected: all pass (by now: 11 + 7 + 5 + 4 + 4 + 4 = 35 tests across `test_cpe.py`, `test_purl.py`, `test_pull_nvd_cpe.py`, `test_write.py`), clean

- [ ] **Step 6: Commit** (or confirm gitignored)

---

## Task 10: CLI wiring + manual smoke test against the real dump

**Files:**
- Modify: `tmp/scripts/pull_nvd_cpe.py`

**Interfaces:**
- Consumes: everything from Tasks 6-9
- Produces: `main() -> int`, runnable as `uv run tmp/scripts/pull_nvd_cpe.py`

- [ ] **Step 1: Replace the placeholder `if __name__` block with a real CLI**

In `tmp/scripts/pull_nvd_cpe.py`, add near the top:
```python
import argparse

from _lib import REPO_ROOT, load_existing, write_new_product, write_new_vendor
```

Replace the file's trailing:
```python
if __name__ == "__main__":
    print("This script isn't runnable standalone yet — Task 10 adds main().")
```
with:
```python
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write files (default: dry run)")
    parser.add_argument("--top-n", type=int, default=1000, help="how many not-yet-covered vendors to create, ranked by distinct product count")
    parser.add_argument("--refresh", action="store_true", help="bypass the reduced-map cache and re-parse the tarball")
    parser.add_argument("--threshold", type=int, default=85, help="fuzzy match threshold (0-100)")
    args = parser.parse_args()

    print("Building reduced CPE vendor/product map (first run parses ~3.5GB, can take a few minutes; cached after)...")
    reduced_map = build_reduced_cpe_map(refresh=args.refresh)
    pair_count = sum(len(products) for products in reduced_map.values())
    print(f"  {len(reduced_map)} unique vendors, {pair_count} unique vendor/product pairs")

    existing = load_existing()

    v_backfilled, p_backfilled = backfill_cpe_fields(existing, reduced_map, apply=args.apply)
    print(f"\ncpe backfill: {v_backfilled} vendor(s), {p_backfilled} product(s) matched"
          + ("" if args.apply else " (dry run — nothing written)"))

    new_vendors, new_products = create_new_coverage(existing, reduced_map, args.top_n, args.threshold)
    print(f"\nnew coverage (top {args.top_n} vendors by product count): "
          f"{len(new_vendors)} new vendor(s), {len(new_products)} new product(s)")

    purl_matched = backfill_purl_fields(existing, apply=args.apply)
    print(f"\npurl backfill: {purl_matched} product(s) matched"
          + ("" if args.apply else " (dry run — nothing written)"))

    if not args.apply:
        print("\nDry run — no files written. Re-run with --apply.")
        return 0

    print("\nWriting new files...")
    skipped = 0
    for v in new_vendors:
        try:
            path = write_new_vendor(v)
            print(f"  wrote {path.relative_to(REPO_ROOT)}")
        except FileExistsError as exc:
            print(f"  SKIPPED: {exc}")
            skipped += 1
    for p in new_products:
        try:
            path = write_new_product(p)
            print(f"  wrote {path.relative_to(REPO_ROOT)}")
        except FileExistsError as exc:
            print(f"  SKIPPED: {exc}")
            skipped += 1
    if skipped:
        print(f"\n{skipped} file(s) skipped due to path collisions — see above, review manually.")

    print("\nDone. Now run `uv run tools/validate.py` and review the new (confidence: auto) entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the full tmp/scripts/ test suite one more time (main() itself isn't unit-tested — argparse wiring is exercised by the manual smoke test in Step 4)**

```bash
uv run pytest tmp/scripts/ -v
uv run ruff check tmp/scripts/
```
Expected: all 35 tests pass, clean

- [ ] **Step 3: Dry-run smoke test against the real local tarball (small `--top-n` to keep it fast)**

```bash
uv run tmp/scripts/pull_nvd_cpe.py --top-n 10
```
Expected: completes (first run takes a few minutes parsing the real ~3.5GB dump; writes `tmp/cache/nvd-cpe-reduced.json` for subsequent runs), prints vendor/pair counts, backfill match counts, and up to 10 new vendors with their products. No files written (dry run).

- [ ] **Step 4: `--apply` smoke test, then full verification**

```bash
uv run tmp/scripts/pull_nvd_cpe.py --top-n 10 --apply
uv run tools/validate.py
uv run pytest -q
```
Expected: `tools/validate.py` reports `All vendor and product entries are valid.`; the full suite still shows `30 passed` (from Task 2's additions — this run only touches `data/vendors/`, not the committed test suite).

- [ ] **Step 5: Spot-check the output**

```bash
git status --porcelain=v1 --untracked-files=all -- data/vendors | head -20
```
Manually open 2-3 of the newly-created or backfilled files and confirm: `cpe` is well-formed and matches the vendor/product name, `type` matches what you'd expect from the CPE part, `confidence: auto` on every new alias. This data now flows into the same local review UI (`tmp/review-ui/server.py`) built earlier — it will show up there for approval/rejection like the CISA/EOL batches did.

- [ ] **Step 6: Final commit**

```bash
git add tmp/scripts/pull_nvd_cpe.py tmp/scripts/test_pull_nvd_cpe.py
git commit -m "add pull_nvd_cpe.py: cpe backfill + top-N vendor import from NVD CPE match dump"
```
(or confirm gitignored, per earlier tasks' note — either way, this step marks the plan complete)

---

## Self-Review Notes

- **Spec coverage:** rename (Task 1) ✓, `cpe`/`purl` schema fields (Task 2) ✓, CPE parsing incl. escaped-colon handling (Task 3) ✓, PURL ecosystem mapping incl. Maven split (Task 4) ✓, write-function support for the new fields incl. key ordering (Task 5) ✓, streaming/cached tarball parse (Task 6) ✓, backfill pass (Task 7) ✓, top-N new-coverage pass with type-from-part (Task 8) ✓, purl backfill (Task 9) ✓, CLI + manual smoke test against the real dump (Task 10) ✓. No spec section without a task.
- **Type consistency:** `NewVendor`/`NewProduct` gain `cpe`/`purl` as `str | None = None` in Task 5 and are used with those exact names in Tasks 8-9; `ExistingData`, `resolve_against_index`, `slugify`, `format_cpe_prefix`, `parse_cpe_criteria`, `unescape_cpe_component`, `ecosystem_to_purl` are each defined once (Tasks 3/4, reused from existing `_lib.py` for `ExistingData`/`resolve_against_index`/`slugify`) and referenced with matching signatures everywhere they're used afterward.
- **No placeholders:** every step has runnable code or an exact shell command; no "add appropriate error handling" or "similar to Task N" — Task 7/9's write logic is spelled out in full each time despite structural similarity to Task 6, since a task's implementer may not have Task 6 in context.
