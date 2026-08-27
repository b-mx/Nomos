# Nomos Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap the Nomos repo — a community-maintained mapping from vendor/product names across security data sources (NVD CPE, CISA KEV, OSV, endoflife.date) to one canonical id per vendor/product — with schema, validation tooling, seed data, a published index + search site, and a hardened `main` branch.

**Architecture:** Pure-data repo (`vendors/**` YAML) validated by three independent Python CLI tools (`validate.py`, `suggest_match.py`, `build_index.py`) sharing one tree-walking helper module (`tools/_common.py`). CI runs validation on every PR; a separate merge-to-main workflow builds and publishes the flattened index + a static Fuse.js search page to `gh-pages`.

**Tech Stack:** Python 3.12, `uv`, `pyyaml`, `jsonschema` (draft 2020-12), `rapidfuzz`, `pytest`, `ruff`, `mypy --strict`. Static site: plain HTML/CSS/vanilla JS + vendored Fuse.js (no CDN, no build step).

**Spec:** the full spec is in this conversation's initial user message (no separate file — carry it forward for any executor who wasn't present for that turn).

## Global Constraints

- Python 3.12, `uv` for dependency management, `pyproject.toml`.
- `ruff` + `mypy --strict` clean on `tools/`.
- All CI scripts runnable locally via `uv run tools/<script>.py`.
- `pytest`, no network access in tests.
- License: **MIT**.
- `id` fields: lowercase kebab-case (`^[a-z0-9]+(-[a-z0-9]+)*$`), immutable once merged.
- Self-vendored products (no real company) reuse the same slug for vendor dir and product, e.g. `vendors/redis/vendor.yaml` + `vendors/redis/products/redis.yaml`.
- **Convention (derived from the self-vendored decision):** when a source's alias value would be identical at both vendor and product level (true for every single-product vendor, self-vendored or not — e.g. `nginx`/`nginx`, `docker`/`docker`), the alias goes on the **product only**. The `(source, value)` uniqueness check is global across vendor *and* product entries, so duplicating it at both levels is a validation error, not just redundant. Vendor-level aliases are kept only where the vendor's alias string genuinely differs from its product's (e.g. Cisco vendor `cisco` vs. product `adaptive_security_appliance`).
- Multi-vendor CVEs use sentinel vendor `_multiple` — **no `vendors/_multiple/vendor.yaml` file is created**; it's a reserved id documented in CONTRIBUTING.md only, never a real canonical entry.
- `taxonomy/tags.yaml` and `schema/**` require CODEOWNERS review; a new tag can never land in the same PR as the product using it.
- Third-party GitHub Actions are pinned by commit SHA (with a version comment), not floating tags — resolve the SHA at implementation time via `gh api repos/<owner>/<repo>/git/refs/tags/<tag> --jq .object.sha` since this plan cannot fabricate real commit hashes.
- `tools/suggest_match.py`'s diff mode reads PR content via `yaml.safe_load` only (never executes anything from the PR) and its CI job checks out **base**-branch tooling only, fetching the PR head solely as a data source — see Task 15 for the full rationale (this is decision #3 from the plan-approval turn).

---

## Repo config review (read before Task 18)

Checked live via `gh api` against `b-mx/Nomos` (public repo, already exists, no commits pushed yet):

| Setting | Current | Fix |
|---|---|---|
| Branch protection on `main` | none (branch doesn't exist yet) | Task 18: create after first push |
| `security_and_analysis.dependabot_security_updates` | disabled | Task 18: enable via API |
| `security_and_analysis.secret_scanning` / `secret_scanning_push_protection` | already enabled | no action |
| `delete_branch_on_merge` | false | Task 18: enable |
| `actions/permissions.allowed_actions` | `all`, `sha_pinning_required: false` | Mitigate at the workflow-file level (SHA-pin every third-party action — see Global Constraints) rather than restricting `allowed_actions`, which would also block contributors' own forked-repo CI runs |
| `actions/permissions/workflow.default_workflow_permissions` | `read` | already correct (least-privilege default) — workflows opt into `write` explicitly per job |

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `LICENSE`, `README.md` (stub, filled in Task 16)
- Create dirs (via placeholder files where needed): `vendors/.gitkeep` (removed once seed data lands in Task 10)

**Interfaces:**
- Produces: `pyproject.toml` dependency set (`pyyaml`, `jsonschema`, `rapidfuzz` runtime; `pytest`, `ruff`, `mypy`, `types-pyyaml`, `types-jsonschema` dev) that every later task's `uv run` commands depend on.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "nomos-tools"
version = "0.1.0"
description = "Validation and index-generation tooling for the Nomos vendor/product identity mapping"
requires-python = ">=3.12"
dependencies = [
    "pyyaml>=6.0",
    "jsonschema>=4.23",
    "rapidfuzz>=3.10",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "ruff>=0.8",
    "mypy>=1.13",
    "types-pyyaml>=6.0",
    "types-jsonschema>=4.23",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
strict = true
python_version = "3.12"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 2: Create `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.mypy_cache/
.ruff_cache/
.pytest_cache/
dist/
/index/
```

(`/index/` is the locally-generated output of `build_index.py` — the real index is published to `gh-pages`, never committed to `main`. This does not affect `examples/aliases.json`, which lives outside `/index/`.)

- [ ] **Step 3: Create `LICENSE`**

Standard MIT license text, copyright holder `Nomos contributors`, year 2026.

- [ ] **Step 4: Install and lock dependencies**

Run: `uv sync --all-extras`
Expected: creates `.venv/` and `uv.lock`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore LICENSE uv.lock
git commit -m "chore: scaffold project with uv, ruff, mypy config"
```

---

### Task 2: JSON Schemas

**Files:**
- Create: `schema/vendor.schema.json`, `schema/product.schema.json`, `schema/taxonomy.schema.json`

**Interfaces:**
- Produces: the two schema files `tools/validate.py` (Task 5) loads by filename via `SCHEMA_DIR / "vendor.schema.json"` / `"product.schema.json"`.

- [ ] **Step 1: Create `schema/vendor.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://nomos.dev/schema/vendor.schema.json",
  "title": "Vendor",
  "type": "object",
  "required": ["id", "name", "aliases"],
  "additionalProperties": false,
  "properties": {
    "id": { "type": "string", "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$" },
    "name": { "type": "string", "minLength": 1 },
    "icon": { "type": "string", "pattern": "^i-[a-z0-9]+(-[a-z0-9]+)+$" },
    "aliases": {
      "type": "array",
      "items": { "$ref": "#/$defs/alias" }
    }
  },
  "$defs": {
    "alias": {
      "type": "object",
      "required": ["source", "value", "confidence"],
      "additionalProperties": false,
      "properties": {
        "source": { "type": "string", "minLength": 1 },
        "value": { "type": "string", "minLength": 1 },
        "ecosystem": { "type": "string", "minLength": 1 },
        "confidence": { "enum": ["curated", "auto"] }
      }
    }
  }
}
```

`aliases` has no `minItems` — self-vendored vendors legitimately have `aliases: []` (see Global Constraints).

- [ ] **Step 2: Create `schema/product.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://nomos.dev/schema/product.schema.json",
  "title": "Product",
  "type": "object",
  "required": ["id", "vendor_id", "name", "type", "tags", "aliases"],
  "additionalProperties": false,
  "properties": {
    "id": { "type": "string", "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$" },
    "vendor_id": { "type": "string", "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$" },
    "name": { "type": "string", "minLength": 1 },
    "type": { "enum": ["hardware", "appliance", "firmware", "os", "software", "library"] },
    "tags": {
      "type": "array",
      "items": { "type": "string" },
      "uniqueItems": true
    },
    "icon": { "type": "string", "pattern": "^i-[a-z0-9]+(-[a-z0-9]+)+$" },
    "aliases": {
      "type": "array",
      "items": { "$ref": "#/$defs/alias" }
    },
    "services": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["protocol", "port", "name"],
        "additionalProperties": false,
        "properties": {
          "protocol": { "enum": ["tcp", "udp"] },
          "port": { "type": "integer", "minimum": 1, "maximum": 65535 },
          "name": { "type": "string", "minLength": 1 },
          "default": { "type": "boolean" }
        }
      }
    }
  },
  "$defs": {
    "alias": {
      "type": "object",
      "required": ["source", "value", "confidence"],
      "additionalProperties": false,
      "properties": {
        "source": { "type": "string", "minLength": 1 },
        "value": { "type": "string", "minLength": 1 },
        "ecosystem": { "type": "string", "minLength": 1 },
        "confidence": { "enum": ["curated", "auto"] }
      }
    }
  }
}
```

The `alias` def is duplicated rather than `$ref`-ed across files to avoid cross-file reference resolution in `jsonschema` — both schemas are small enough that the duplication is cheaper than a resolver/registry setup. `services`-on-`type` restriction (CI rule 5) is deliberately **not** encoded here — it's a code-level check in Task 7, so the two failure modes stay independently testable.

- [ ] **Step 3: Create `schema/taxonomy.schema.json`**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://nomos.dev/schema/taxonomy.schema.json",
  "title": "Taxonomy",
  "type": "object",
  "required": ["tags"],
  "additionalProperties": false,
  "properties": {
    "tags": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["id", "name"],
        "additionalProperties": false,
        "properties": {
          "id": { "type": "string", "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$" },
          "name": { "type": "string", "minLength": 1 }
        }
      }
    }
  }
}
```

(Uniqueness of tag `id`s is enforced in code, not schema — JSON Schema `uniqueItems` on an array of objects requires whole-object equality, which wouldn't catch two tags sharing an `id` with different `name`s.)

- [ ] **Step 4: Commit**

```bash
git add schema/
git commit -m "feat: add JSON Schema definitions for vendor, product, taxonomy"
```

---

### Task 3: Taxonomy seed data

**Files:**
- Create: `taxonomy/tags.yaml`

**Interfaces:**
- Produces: the 29 tag ids every seed product (Tasks 10–12) and `tools/validate.py`'s tag-existence check (Task 7) reference.

- [ ] **Step 1: Create `taxonomy/tags.yaml`**

```yaml
tags:
  - id: operating-system
    name: Operating System
  - id: firmware
    name: Firmware
  - id: hardware-appliance
    name: Hardware Appliance
  - id: virtualization
    name: Virtualization
  - id: container-runtime
    name: Container Runtime
  - id: orchestration
    name: Orchestration
  - id: webserver
    name: Web Server
  - id: database
    name: Database
  - id: cache
    name: Cache
  - id: message-queue
    name: Message Queue
  - id: network-device
    name: Network Device
  - id: firewall
    name: Firewall
  - id: vpn
    name: VPN
  - id: load-balancer
    name: Load Balancer
  - id: dns
    name: DNS
  - id: cms
    name: Content Management System
  - id: ecommerce
    name: E-Commerce
  - id: programming-language
    name: Programming Language
  - id: runtime
    name: Runtime
  - id: framework
    name: Framework
  - id: library
    name: Library
  - id: build-tool
    name: Build Tool
  - id: ci-cd
    name: CI/CD
  - id: monitoring
    name: Monitoring
  - id: logging
    name: Logging
  - id: identity-access-management
    name: Identity & Access Management
  - id: ai-ml
    name: AI / Machine Learning
  - id: collaboration-tool
    name: Collaboration Tool
  - id: cryptography
    name: Cryptography
```

- [ ] **Step 2: Validate it parses and matches the schema shape**

Run: `uv run python -c "import yaml; d = yaml.safe_load(open('taxonomy/tags.yaml')); assert len({t['id'] for t in d['tags']}) == len(d['tags']); print(len(d['tags']), 'tags, all unique')"`
Expected: `29 tags, all unique`

- [ ] **Step 3: Commit**

```bash
git add taxonomy/
git commit -m "feat: seed taxonomy with 29 starter tags"
```

---

### Task 4: Shared tooling helpers

**Files:**
- Create: `tools/__init__.py` (empty), `tools/_common.py`
- Create: `tests/__init__.py` (empty)

**Interfaces:**
- Consumes: nothing (foundation module).
- Produces: `REPO_ROOT: Path`, `KEBAB_CASE_RE: re.Pattern[str]`, `VendorEntry(path: Path, data: dict[str, Any])`, `ProductEntry(path: Path, data: dict[str, Any], vendor_id: str)`, `load_yaml(path: Path) -> dict[str, Any]`, `iter_vendors(vendors_dir: Path) -> list[VendorEntry]`, `iter_products(vendors_dir: Path) -> list[ProductEntry]`, `load_taxonomy_tags(taxonomy_file: Path) -> set[str]` — every later tool and test imports these exact names.

- [ ] **Step 1: Create `tools/__init__.py` and `tests/__init__.py`**

Both empty files (makes `tools` and `tests` importable packages so `from tools._common import ...` and `from tools.validate import ...` resolve under pytest).

- [ ] **Step 2: Create `tools/_common.py`**

```python
"""Shared helpers for Nomos validation and index-generation tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORS_DIR = REPO_ROOT / "vendors"
TAXONOMY_FILE = REPO_ROOT / "taxonomy" / "tags.yaml"

KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass(frozen=True)
class VendorEntry:
    path: Path
    data: dict[str, Any]


@dataclass(frozen=True)
class ProductEntry:
    path: Path
    data: dict[str, Any]
    vendor_id: str


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def iter_vendors(vendors_dir: Path = VENDORS_DIR) -> list[VendorEntry]:
    entries: list[VendorEntry] = []
    if not vendors_dir.is_dir():
        return entries
    for vendor_dir in sorted(vendors_dir.iterdir()):
        if not vendor_dir.is_dir():
            continue
        vendor_file = vendor_dir / "vendor.yaml"
        if vendor_file.exists():
            entries.append(VendorEntry(path=vendor_file, data=load_yaml(vendor_file)))
    return entries


def iter_products(vendors_dir: Path = VENDORS_DIR) -> list[ProductEntry]:
    entries: list[ProductEntry] = []
    if not vendors_dir.is_dir():
        return entries
    for vendor_dir in sorted(vendors_dir.iterdir()):
        if not vendor_dir.is_dir():
            continue
        products_dir = vendor_dir / "products"
        if not products_dir.is_dir():
            continue
        for product_file in sorted(products_dir.glob("*.yaml")):
            entries.append(
                ProductEntry(
                    path=product_file,
                    data=load_yaml(product_file),
                    vendor_id=vendor_dir.name,
                )
            )
    return entries


def load_taxonomy_tags(taxonomy_file: Path = TAXONOMY_FILE) -> set[str]:
    data = load_yaml(taxonomy_file)
    return {tag["id"] for tag in data.get("tags", [])}
```

Note the `vendors_dir` / `taxonomy_file` default-argument pattern: every function takes its data-source path as an explicit, overridable parameter (not a hidden global read at call time). This is what lets Task 5–9's tests point at `tests/fixtures/...` trees without monkeypatching module globals — a default arg is bound once, but an explicit override always wins.

- [ ] **Step 3: Sanity-check it imports**

Run: `uv run python -c "from tools._common import iter_vendors, iter_products; print(iter_vendors(), iter_products())"`
Expected: `[] []` (no `vendors/` content yet)

- [ ] **Step 4: Commit**

```bash
git add tools/__init__.py tools/_common.py tests/__init__.py
git commit -m "feat: add shared tree-walking helpers for tools"
```

---

### Task 5: `tools/validate.py` — schema conformance + id/path checks

**Files:**
- Create: `tools/validate.py`
- Create: `tests/test_validate.py`
- Create fixtures: `tests/fixtures/valid_tree/vendors/acme/vendor.yaml`, `tests/fixtures/valid_tree/vendors/acme/products/widget.yaml`

**Interfaces:**
- Consumes: `tools._common.{REPO_ROOT, KEBAB_CASE_RE, VendorEntry, ProductEntry, iter_vendors, iter_products, load_taxonomy_tags}` (Task 4).
- Produces: `validate_schema_conformance(vendors, products) -> list[str]`, `validate_ids_and_paths(vendors, products) -> list[str]`, `run_all_checks(vendors_dir: Path) -> list[str]`, `main() -> int` — Tasks 6–7 add more `validate_*` functions to this same file and extend `run_all_checks`.

- [ ] **Step 1: Create the valid-tree fixture**

`tests/fixtures/valid_tree/vendors/acme/vendor.yaml`:
```yaml
id: acme
name: Acme Corp
aliases:
  - source: nvd_cpe
    value: acme
    confidence: curated
```

`tests/fixtures/valid_tree/vendors/acme/products/widget.yaml`:
```yaml
id: widget
vendor_id: acme
name: Acme Widget
type: software
tags: [database]
aliases:
  - source: nvd_cpe
    value: widget
    confidence: curated
services:
  - protocol: tcp
    port: 1234
    name: widget-proto
    default: true
```

- [ ] **Step 2: Write the failing test**

`tests/test_validate.py`:
```python
from pathlib import Path

from tools._common import iter_products, iter_vendors

FIXTURES = Path(__file__).parent / "fixtures"


def test_valid_tree_has_no_schema_or_id_errors():
    from tools.validate import validate_ids_and_paths, validate_schema_conformance

    vendors_dir = FIXTURES / "valid_tree" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    assert validate_schema_conformance(vendors, products) == []
    assert validate_ids_and_paths(vendors, products) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.validate'`

- [ ] **Step 4: Write `tools/validate.py` (schema + id/path checks only)**

```python
"""Validate every vendor and product YAML file in the vendors/ tree."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

from tools._common import (
    KEBAB_CASE_RE,
    REPO_ROOT,
    ProductEntry,
    VendorEntry,
    iter_products,
    iter_vendors,
)

SCHEMA_DIR = REPO_ROOT / "schema"


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def load_schema(name: str) -> dict[str, Any]:
    with (SCHEMA_DIR / name).open("r", encoding="utf-8") as f:
        result: dict[str, Any] = json.load(f)
        return result


def validate_schema_conformance(
    vendors: list[VendorEntry], products: list[ProductEntry]
) -> list[str]:
    errors: list[str] = []
    vendor_schema = load_schema("vendor.schema.json")
    product_schema = load_schema("product.schema.json")
    vendor_validator = jsonschema.Draft202012Validator(vendor_schema)
    product_validator = jsonschema.Draft202012Validator(product_schema)
    for entry in vendors:
        for err in vendor_validator.iter_errors(entry.data):
            errors.append(f"{_rel(entry.path)}: schema error at {list(err.path)}: {err.message}")
    for entry in products:
        for err in product_validator.iter_errors(entry.data):
            errors.append(f"{_rel(entry.path)}: schema error at {list(err.path)}: {err.message}")
    return errors


def validate_ids_and_paths(
    vendors: list[VendorEntry], products: list[ProductEntry]
) -> list[str]:
    errors: list[str] = []
    for entry in vendors:
        vid = entry.data.get("id", "")
        if not KEBAB_CASE_RE.match(vid):
            errors.append(f"{_rel(entry.path)}: id '{vid}' is not lowercase kebab-case")
        dir_name = entry.path.parent.name
        if vid != dir_name:
            errors.append(
                f"{_rel(entry.path)}: id '{vid}' does not match directory name '{dir_name}'"
            )
    for entry in products:
        pid = entry.data.get("id", "")
        if not KEBAB_CASE_RE.match(pid):
            errors.append(f"{_rel(entry.path)}: id '{pid}' is not lowercase kebab-case")
        if entry.path.stem != pid:
            errors.append(
                f"{_rel(entry.path)}: id '{pid}' does not match filename '{entry.path.stem}'"
            )
        declared_vendor_id = entry.data.get("vendor_id", "")
        if declared_vendor_id != entry.vendor_id:
            errors.append(
                f"{_rel(entry.path)}: vendor_id '{declared_vendor_id}' does not match "
                f"parent vendor directory '{entry.vendor_id}'"
            )
    return errors


def run_all_checks(vendors_dir: Path = REPO_ROOT / "vendors") -> list[str]:
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    errors: list[str] = []
    errors += validate_schema_conformance(vendors, products)
    errors += validate_ids_and_paths(vendors, products)
    return errors


def main() -> int:
    errors = run_all_checks()
    if errors:
        print(f"Found {len(errors)} validation error(s):\n", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("All vendor and product entries are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_validate.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tools/validate.py tests/test_validate.py tests/fixtures/valid_tree/
git commit -m "feat: validate schema conformance and id/path consistency"
```

---

### Task 6: `tools/validate.py` — global alias uniqueness

**Files:**
- Modify: `tools/validate.py`
- Modify: `tests/test_validate.py`
- Create fixtures: `tests/fixtures/invalid_duplicate_alias/vendors/acme-one/vendor.yaml`, `tests/fixtures/invalid_duplicate_alias/vendors/acme-two/vendor.yaml`

**Interfaces:**
- Consumes: `VendorEntry`, `ProductEntry` from Task 4.
- Produces: `validate_alias_uniqueness(vendors, products) -> list[str]`, added into `run_all_checks`.

- [ ] **Step 1: Create the duplicate-alias fixture**

`tests/fixtures/invalid_duplicate_alias/vendors/acme-one/vendor.yaml`:
```yaml
id: acme-one
name: Acme One
aliases:
  - source: nvd_cpe
    value: shared_value
    confidence: curated
```

`tests/fixtures/invalid_duplicate_alias/vendors/acme-two/vendor.yaml`:
```yaml
id: acme-two
name: Acme Two
aliases:
  - source: nvd_cpe
    value: shared_value
    confidence: curated
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_validate.py`:
```python
def test_duplicate_alias_is_caught_with_both_paths_named():
    from tools.validate import validate_alias_uniqueness

    vendors_dir = FIXTURES / "invalid_duplicate_alias" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    errors = validate_alias_uniqueness(vendors, products)
    assert len(errors) == 1
    assert "acme-one" in errors[0]
    assert "acme-two" in errors[0]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_validate.py::test_duplicate_alias_is_caught_with_both_paths_named -v`
Expected: FAIL with `ImportError: cannot import name 'validate_alias_uniqueness'`

- [ ] **Step 4: Add `validate_alias_uniqueness` to `tools/validate.py`**

```python
def validate_alias_uniqueness(
    vendors: list[VendorEntry], products: list[ProductEntry]
) -> list[str]:
    errors: list[str] = []
    seen: dict[tuple[str, str], Path] = {}
    for entry in [*vendors, *products]:
        for alias in entry.data.get("aliases", []):
            key = (alias.get("source", ""), alias.get("value", ""))
            if key in seen:
                errors.append(
                    f"Duplicate alias {key} claimed by both "
                    f"{_rel(seen[key])} and {_rel(entry.path)}"
                )
            else:
                seen[key] = entry.path
    return errors
```

And add it to `run_all_checks`:
```python
    errors += validate_alias_uniqueness(vendors, products)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_validate.py -v`
Expected: PASS (both tests)

- [ ] **Step 6: Commit**

```bash
git add tools/validate.py tests/test_validate.py tests/fixtures/invalid_duplicate_alias/
git commit -m "feat: reject duplicate (source, value) aliases across the whole tree"
```

---

### Task 7: `tools/validate.py` — tag existence + services-on-type restriction

**Files:**
- Modify: `tools/validate.py`
- Modify: `tests/test_validate.py`
- Create fixtures: `tests/fixtures/invalid_unknown_tag/vendors/acme/vendor.yaml`, `tests/fixtures/invalid_unknown_tag/vendors/acme/products/widget.yaml`, `tests/fixtures/invalid_services_on_library/vendors/acme/vendor.yaml`, `tests/fixtures/invalid_services_on_library/vendors/acme/products/libwidget.yaml`, `tests/fixtures/self_vendored/vendors/widgetlib/vendor.yaml`, `tests/fixtures/self_vendored/vendors/widgetlib/products/widgetlib.yaml`

**Interfaces:**
- Consumes: `load_taxonomy_tags` from Task 4 (called with the real `taxonomy/tags.yaml`, since it's stable and these fixtures don't need their own copy).
- Produces: `validate_tags_exist(products) -> list[str]`, `validate_services_allowed(products) -> list[str]`, both added into `run_all_checks`. This completes all 7 CI validation rules from the spec (rule 6, the icon pattern, is already covered by the schema `pattern` in Task 2).

- [ ] **Step 1: Create the unknown-tag fixture**

`tests/fixtures/invalid_unknown_tag/vendors/acme/vendor.yaml`:
```yaml
id: acme
name: Acme Corp
aliases:
  - source: nvd_cpe
    value: acme
    confidence: curated
```

`tests/fixtures/invalid_unknown_tag/vendors/acme/products/widget.yaml`:
```yaml
id: widget
vendor_id: acme
name: Acme Widget
type: software
tags: [definitely-not-a-real-tag]
aliases:
  - source: nvd_cpe
    value: widget
    confidence: curated
```

- [ ] **Step 2: Create the services-on-library fixture**

`tests/fixtures/invalid_services_on_library/vendors/acme/vendor.yaml`: same content as Step 1's vendor file.

`tests/fixtures/invalid_services_on_library/vendors/acme/products/libwidget.yaml`:
```yaml
id: libwidget
vendor_id: acme
name: Acme LibWidget
type: library
tags: [library]
aliases:
  - source: nvd_cpe
    value: libwidget
    confidence: curated
services:
  - protocol: tcp
    port: 1234
    name: nope
    default: true
```

- [ ] **Step 3: Create the self-vendored fixture**

`tests/fixtures/self_vendored/vendors/widgetlib/vendor.yaml`:
```yaml
id: widgetlib
name: WidgetLib
aliases: []
```

`tests/fixtures/self_vendored/vendors/widgetlib/products/widgetlib.yaml`:
```yaml
id: widgetlib
vendor_id: widgetlib
name: WidgetLib
type: library
tags: [library]
aliases:
  - source: osv
    value: widgetlib
    ecosystem: PyPI
    confidence: curated
```

- [ ] **Step 4: Write the failing tests**

Append to `tests/test_validate.py`:
```python
def test_unknown_tag_is_rejected():
    from tools.validate import validate_tags_exist

    vendors_dir = FIXTURES / "invalid_unknown_tag" / "vendors"
    products = iter_products(vendors_dir)
    errors = validate_tags_exist(products)
    assert len(errors) == 1
    assert "definitely-not-a-real-tag" in errors[0]


def test_services_rejected_on_library():
    from tools.validate import validate_services_allowed

    vendors_dir = FIXTURES / "invalid_services_on_library" / "vendors"
    products = iter_products(vendors_dir)
    errors = validate_services_allowed(products)
    assert len(errors) == 1
    assert "library" in errors[0]


def test_self_vendored_validates_normally():
    from tools.validate import (
        validate_alias_uniqueness,
        validate_schema_conformance,
        validate_services_allowed,
        validate_tags_exist,
    )

    vendors_dir = FIXTURES / "self_vendored" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    assert vendors[0].data["id"] == products[0].vendor_id == products[0].data["id"]
    assert validate_schema_conformance(vendors, products) == []
    assert validate_alias_uniqueness(vendors, products) == []
    assert validate_tags_exist(products) == []
    assert validate_services_allowed(products) == []
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `uv run pytest tests/test_validate.py -v`
Expected: the three new tests FAIL with `ImportError` (functions don't exist yet)

- [ ] **Step 6: Add the two checks to `tools/validate.py`**

```python
from tools._common import load_taxonomy_tags  # add to existing import line

SERVICES_ALLOWED_TYPES = {"software", "appliance", "os"}


def validate_tags_exist(products: list[ProductEntry]) -> list[str]:
    errors: list[str] = []
    known_tags = load_taxonomy_tags()
    for entry in products:
        for tag in entry.data.get("tags", []):
            if tag not in known_tags:
                errors.append(
                    f"{_rel(entry.path)}: unknown tag '{tag}' not in taxonomy/tags.yaml"
                )
    return errors


def validate_services_allowed(products: list[ProductEntry]) -> list[str]:
    errors: list[str] = []
    for entry in products:
        if "services" in entry.data and entry.data.get("type") not in SERVICES_ALLOWED_TYPES:
            errors.append(
                f"{_rel(entry.path)}: 'services' is not allowed on type "
                f"'{entry.data.get('type')}' (only software, appliance, os)"
            )
    return errors
```

And add both to `run_all_checks`:
```python
    errors += validate_tags_exist(products)
    errors += validate_services_allowed(products)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_validate.py -v`
Expected: PASS (all tests)

- [ ] **Step 8: Run ruff and mypy**

Run: `uv run ruff check tools/ && uv run mypy --strict tools/`
Expected: no errors. Fix any typing issues (e.g. missing return type annotations) before proceeding.

- [ ] **Step 9: Commit**

```bash
git add tools/validate.py tests/test_validate.py tests/fixtures/invalid_unknown_tag/ tests/fixtures/invalid_services_on_library/ tests/fixtures/self_vendored/
git commit -m "feat: reject unknown tags and services on disallowed product types"
```

---

### Task 8: `tools/suggest_match.py`

**Files:**
- Create: `tools/suggest_match.py`
- Create: `tests/test_suggest_match.py`

**Interfaces:**
- Consumes: `tools._common.{iter_vendors, iter_products, load_yaml, REPO_ROOT}`.
- Produces: `AliasRecord(value: str, canonical_id: str)`, `flatten_alias_index(vendors_dir: Path) -> list[AliasRecord]`, `find_close_matches(candidate_value, candidate_canonical_id, index, threshold) -> list[tuple[AliasRecord, float]]`, `format_comment(candidate_value, matches) -> str`, `main() -> int` (CLI: `--value`/`--canonical-id` single-check mode, `--base-ref`/`--head-ref` diff mode for CI) — Task 15's workflow calls this CLI directly.

- [ ] **Step 1: Write the failing tests**

`tests/test_suggest_match.py`:
```python
from tools.suggest_match import AliasRecord, find_close_matches, format_comment


def test_close_match_fires_above_threshold():
    index = [AliasRecord(value="microsft", canonical_id="microsoft")]
    matches = find_close_matches("microsoft", "some-new-vendor", index, threshold=85)
    assert len(matches) == 1
    assert matches[0][0].canonical_id == "microsoft"


def test_distinct_names_do_not_fire():
    index = [AliasRecord(value="postgresql", canonical_id="postgresql")]
    matches = find_close_matches("mysql", "some-new-vendor", index, threshold=85)
    assert matches == []


def test_self_match_is_excluded():
    index = [AliasRecord(value="nginx", canonical_id="nginx")]
    matches = find_close_matches("nginx", "nginx", index, threshold=85)
    assert matches == []


def test_format_comment_includes_all_matches():
    index = [AliasRecord(value="microsft", canonical_id="microsoft")]
    matches = find_close_matches("microsoft", "new-vendor", index, threshold=85)
    comment = format_comment("microsoft", matches)
    assert "microsoft" in comment
    assert "distinct vendor/product" in comment
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_suggest_match.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.suggest_match'`

- [ ] **Step 3: Write `tools/suggest_match.py`**

```python
"""Fuzzy-match a new alias value against the existing alias index.

Used as a non-blocking PR comment check — never a merge gate, since
legitimately similar vendor/product names exist.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz

from tools._common import REPO_ROOT, iter_products, iter_vendors

DEFAULT_THRESHOLD = 85


@dataclass(frozen=True)
class AliasRecord:
    value: str
    canonical_id: str


def flatten_alias_index(vendors_dir: Path = REPO_ROOT / "vendors") -> list[AliasRecord]:
    records: list[AliasRecord] = []
    for vendor in iter_vendors(vendors_dir):
        for alias in vendor.data.get("aliases", []):
            records.append(AliasRecord(value=alias["value"], canonical_id=vendor.data["id"]))
    for product in iter_products(vendors_dir):
        canonical_id = f"{product.vendor_id}/{product.data['id']}"
        for alias in product.data.get("aliases", []):
            records.append(AliasRecord(value=alias["value"], canonical_id=canonical_id))
    return records


def find_close_matches(
    candidate_value: str,
    candidate_canonical_id: str,
    index: list[AliasRecord],
    threshold: int = DEFAULT_THRESHOLD,
) -> list[tuple[AliasRecord, float]]:
    matches: list[tuple[AliasRecord, float]] = []
    for record in index:
        if record.canonical_id == candidate_canonical_id:
            continue
        score = fuzz.ratio(candidate_value.lower(), record.value.lower())
        if score >= threshold:
            matches.append((record, score))
    return sorted(matches, key=lambda pair: pair[1], reverse=True)


def format_comment(candidate_value: str, matches: list[tuple[AliasRecord, float]]) -> str:
    lines = [f"Alias `{candidate_value}` looks similar to existing entries:"]
    for record, score in matches:
        lines.append(f"- `{record.canonical_id}` (alias `{record.value}`, {score:.0f}% match)")
    lines.append(
        "\nThis alias looks similar to an existing canonical id — please confirm this is a "
        "distinct vendor/product, not a duplicate."
    )
    return "\n".join(lines)


def changed_vendor_files(base_ref: str, head_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACM", f"{base_ref}...{head_ref}", "--", "vendors/"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return [line for line in result.stdout.splitlines() if line.endswith(".yaml")]


def load_yaml_at_ref(ref: str, path: str) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    data: dict[str, Any] = yaml.safe_load(result.stdout) or {}
    return data


def run_diff_mode(base_ref: str, head_ref: str, threshold: int) -> str:
    full_index = flatten_alias_index()
    comments: list[str] = []
    for path in changed_vendor_files(base_ref, head_ref):
        data = load_yaml_at_ref(head_ref, path)
        canonical_id = data.get("id", "")
        if "vendor_id" in data:
            canonical_id = f"{data['vendor_id']}/{canonical_id}"
        for alias in data.get("aliases", []):
            matches = find_close_matches(alias["value"], canonical_id, full_index, threshold)
            if matches:
                comments.append(format_comment(alias["value"], matches))
    return "\n\n---\n\n".join(comments) if comments else "NO_MATCH"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    parser.add_argument("--value")
    parser.add_argument("--canonical-id")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()

    if args.base_ref and args.head_ref:
        print(run_diff_mode(args.base_ref, args.head_ref, args.threshold))
        return 0

    if not args.value or not args.canonical_id:
        parser.error("either --base-ref and --head-ref, or both --value and --canonical-id, are required")

    index = flatten_alias_index()
    matches = find_close_matches(args.value, args.canonical_id, index, args.threshold)
    print(format_comment(args.value, matches) if matches else "NO_MATCH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`load_yaml_at_ref` uses `yaml.safe_load` on content read via `git show` (never `git checkout` + execute) — this is what makes it safe to run against untrusted PR content in Task 15's `pull_request_target` job: the PR's YAML is only ever parsed as data, never executed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_suggest_match.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check tools/ && uv run mypy --strict tools/`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add tools/suggest_match.py tests/test_suggest_match.py
git commit -m "feat: add fuzzy-match suggestion tool for near-duplicate aliases"
```

---

### Task 9: `tools/build_index.py`

**Files:**
- Create: `tools/build_index.py`
- Create: `tests/test_build_index.py`
- Create fixture: `tests/fixtures/expected_aliases.json`

**Interfaces:**
- Consumes: `tools._common.{iter_vendors, iter_products, REPO_ROOT}`.
- Produces: `build_entries(vendors_dir: Path) -> list[dict[str, Any]]`, `build_index(generated_at: str, vendors_dir: Path) -> dict[str, Any]`, `build_by_source(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]`, `write_index(output_dir: Path, generated_at: str, vendors_dir: Path) -> None`, `main() -> int` (CLI: `--output-dir`, `--generated-at`) — Task 13 (examples) and Task 15 (publish workflow) both invoke this CLI.

- [ ] **Step 1: Write the failing tests**

`tests/fixtures/expected_aliases.json` (matches `tests/fixtures/valid_tree` from Task 5 exactly):
```json
{
  "generated_at": "2026-01-01T00:00:00Z",
  "entries": [
    {
      "canonical_type": "vendor",
      "vendor_id": "acme",
      "name": "Acme Corp",
      "icon": null,
      "aliases": [
        { "source": "nvd_cpe", "value": "acme", "confidence": "curated" }
      ]
    },
    {
      "canonical_type": "product",
      "vendor_id": "acme",
      "product_id": "widget",
      "name": "Acme Widget",
      "type": "software",
      "tags": ["database"],
      "icon": null,
      "services": [
        { "protocol": "tcp", "port": 1234, "name": "widget-proto", "default": true }
      ],
      "aliases": [
        { "source": "nvd_cpe", "value": "widget", "confidence": "curated" }
      ]
    }
  ]
}
```

`tests/test_build_index.py`:
```python
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
VALID_TREE = FIXTURES / "valid_tree" / "vendors"


def test_build_index_matches_expected_snapshot():
    from tools.build_index import build_index

    index = build_index(generated_at="2026-01-01T00:00:00Z", vendors_dir=VALID_TREE)
    expected = json.loads((FIXTURES / "expected_aliases.json").read_text())
    assert index == expected


def test_build_by_source_splits_correctly():
    from tools.build_index import build_by_source, build_entries

    entries = build_entries(VALID_TREE)
    by_source = build_by_source(entries)
    assert set(by_source.keys()) == {"nvd_cpe"}
    assert len(by_source["nvd_cpe"]) == 2  # vendor acme + product widget
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_build_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.build_index'`

- [ ] **Step 3: Write `tools/build_index.py`**

```python
"""Flatten vendors/ into index/aliases.json and per-source split files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools._common import REPO_ROOT, iter_products, iter_vendors


def build_entries(vendors_dir: Path = REPO_ROOT / "vendors") -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for vendor in iter_vendors(vendors_dir):
        entries.append(
            {
                "canonical_type": "vendor",
                "vendor_id": vendor.data["id"],
                "name": vendor.data["name"],
                "icon": vendor.data.get("icon"),
                "aliases": vendor.data.get("aliases", []),
            }
        )
    for product in iter_products(vendors_dir):
        entries.append(
            {
                "canonical_type": "product",
                "vendor_id": product.vendor_id,
                "product_id": product.data["id"],
                "name": product.data["name"],
                "type": product.data["type"],
                "tags": product.data.get("tags", []),
                "icon": product.data.get("icon"),
                "services": product.data.get("services", []),
                "aliases": product.data.get("aliases", []),
            }
        )
    return entries


def build_index(
    generated_at: str, vendors_dir: Path = REPO_ROOT / "vendors"
) -> dict[str, Any]:
    return {"generated_at": generated_at, "entries": build_entries(vendors_dir)}


def build_by_source(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        for alias in entry.get("aliases", []):
            source = alias["source"]
            by_source.setdefault(source, []).append(
                {
                    "canonical_type": entry["canonical_type"],
                    "vendor_id": entry["vendor_id"],
                    "product_id": entry.get("product_id"),
                    "name": entry["name"],
                    "alias": alias,
                }
            )
    return by_source


def write_index(
    output_dir: Path, generated_at: str, vendors_dir: Path = REPO_ROOT / "vendors"
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    index = build_index(generated_at, vendors_dir)
    (output_dir / "aliases.json").write_text(json.dumps(index, indent=2) + "\n")
    by_source_dir = output_dir / "by-source"
    by_source_dir.mkdir(parents=True, exist_ok=True)
    for source, items in build_by_source(index["entries"]).items():
        (by_source_dir / f"{source}.json").write_text(json.dumps(items, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "index")
    parser.add_argument(
        "--generated-at",
        required=True,
        help="ISO-8601 timestamp, e.g. output of `date -u +%Y-%m-%dT%H:%M:%SZ`",
    )
    args = parser.parse_args()
    write_index(args.output_dir, args.generated_at)
    print(f"Wrote index to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`--generated-at` is a required CLI arg rather than computed internally with `datetime.now()` — keeps the tool pure and the snapshot test deterministic.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_build_index.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check tools/ && uv run mypy --strict tools/`
Expected: no errors.

- [ ] **Step 6: Run the full test suite and validate.py once more before moving to seed data**

Run: `uv run pytest && uv run tools/validate.py`
Expected: all tests pass; `validate.py` prints `All vendor and product entries are valid.` (the real `vendors/` tree is still empty at this point, which trivially validates).

- [ ] **Step 7: Commit**

```bash
git add tools/build_index.py tests/test_build_index.py tests/fixtures/expected_aliases.json
git commit -m "feat: add build_index.py to flatten vendors/ into aliases.json + by-source files"
```

---

### Task 10: Seed vendor data — batch 1 (self-vendored databases/runtimes + Microsoft)

**Files:**
- Create: `vendors/microsoft/vendor.yaml`, `vendors/microsoft/products/windows-server.yaml`, `vendors/microsoft/products/exchange-server.yaml`
- Create: `vendors/nginx/vendor.yaml`, `vendors/nginx/products/nginx.yaml`
- Create: `vendors/mysql/vendor.yaml`, `vendors/mysql/products/mysql.yaml`
- Create: `vendors/postgresql/vendor.yaml`, `vendors/postgresql/products/postgresql.yaml`
- Delete: `vendors/.gitkeep` (from Task 1, no longer needed once real content exists)

**Interfaces:**
- Consumes: tag ids from `taxonomy/tags.yaml` (Task 3); validated by `tools/validate.py` (Tasks 5–7).

- [ ] **Step 1: Create Microsoft vendor + products**

`vendors/microsoft/vendor.yaml`:
```yaml
id: microsoft
name: Microsoft
icon: i-logos-microsoft-icon
aliases:
  - source: nvd_cpe
    value: microsoft
    confidence: curated
  - source: cisa_kev
    value: "Microsoft Corporation"
    confidence: curated
```

`vendors/microsoft/products/windows-server.yaml`:
```yaml
id: windows-server
vendor_id: microsoft
name: Windows Server
type: os
tags: [operating-system]
icon: i-logos-microsoft-icon
aliases:
  - source: nvd_cpe
    value: windows_server
    confidence: curated
  - source: endoflife
    value: windows-server
    confidence: curated
services:
  - protocol: tcp
    port: 3389
    name: rdp
    default: true
  - protocol: tcp
    port: 445
    name: smb
    default: true
```

`vendors/microsoft/products/exchange-server.yaml`:
```yaml
id: exchange-server
vendor_id: microsoft
name: Microsoft Exchange Server
type: software
tags: [collaboration-tool]
icon: i-logos-microsoft-icon
aliases:
  - source: nvd_cpe
    value: exchange_server
    confidence: curated
  - source: cisa_kev
    value: "Microsoft Exchange Server"
    confidence: curated
services:
  - protocol: tcp
    port: 443
    name: https
    default: true
  - protocol: tcp
    port: 25
    name: smtp
    default: false
```

- [ ] **Step 2: Create NGINX (self-vendored)**

`vendors/nginx/vendor.yaml`:
```yaml
id: nginx
name: NGINX
icon: i-logos-nginx
aliases: []
```

`vendors/nginx/products/nginx.yaml`:
```yaml
id: nginx
vendor_id: nginx
name: NGINX
type: software
tags: [webserver]
icon: i-logos-nginx
aliases:
  - source: nvd_cpe
    value: nginx
    confidence: curated
  - source: endoflife
    value: nginx
    confidence: curated
services:
  - protocol: tcp
    port: 80
    name: http
    default: true
  - protocol: tcp
    port: 443
    name: https
    default: false
```

- [ ] **Step 3: Create MySQL (self-vendored — decision #1 from plan approval)**

`vendors/mysql/vendor.yaml`:
```yaml
id: mysql
name: MySQL
icon: i-logos-mysql-icon
aliases: []
```

`vendors/mysql/products/mysql.yaml`:
```yaml
id: mysql
vendor_id: mysql
name: MySQL
type: software
tags: [database]
icon: i-logos-mysql-icon
aliases:
  - source: nvd_cpe
    value: mysql
    confidence: curated
  - source: endoflife
    value: mysql
    confidence: curated
services:
  - protocol: tcp
    port: 3306
    name: mysql
    default: true
```

- [ ] **Step 4: Create PostgreSQL (self-vendored)**

`vendors/postgresql/vendor.yaml`:
```yaml
id: postgresql
name: PostgreSQL
icon: i-logos-postgresql
aliases: []
```

`vendors/postgresql/products/postgresql.yaml`:
```yaml
id: postgresql
vendor_id: postgresql
name: PostgreSQL
type: software
tags: [database]
icon: i-logos-postgresql
aliases:
  - source: nvd_cpe
    value: postgresql
    confidence: curated
  - source: endoflife
    value: postgresql
    confidence: curated
services:
  - protocol: tcp
    port: 5432
    name: postgresql
    default: true
```

- [ ] **Step 5: Remove the `.gitkeep` placeholder and validate**

Run: `rm -f vendors/.gitkeep && uv run tools/validate.py`
Expected: `All vendor and product entries are valid.`

- [ ] **Step 6: Commit**

```bash
git add vendors/
git commit -m "data: seed microsoft, nginx, mysql, postgresql vendors and products"
```

---

### Task 11: Seed vendor data — batch 2 (self-vendored language/library + Cisco/Canonical/Red Hat)

**Files:**
- Create: `vendors/pytorch/vendor.yaml`, `vendors/pytorch/products/pytorch.yaml`
- Create: `vendors/python/vendor.yaml`, `vendors/python/products/python.yaml`
- Create: `vendors/openssl/vendor.yaml`, `vendors/openssl/products/openssl.yaml`
- Create: `vendors/cisco/vendor.yaml`, `vendors/cisco/products/asa.yaml`, `vendors/cisco/products/ios.yaml`
- Create: `vendors/canonical/vendor.yaml`, `vendors/canonical/products/ubuntu.yaml`
- Create: `vendors/redhat/vendor.yaml`, `vendors/redhat/products/rhel.yaml`

- [ ] **Step 1: Create PyTorch (self-vendored, `type: library`)**

`vendors/pytorch/vendor.yaml`:
```yaml
id: pytorch
name: PyTorch
icon: i-logos-pytorch-icon
aliases: []
```

`vendors/pytorch/products/pytorch.yaml`:
```yaml
id: pytorch
vendor_id: pytorch
name: PyTorch
type: library
tags: [ai-ml]
icon: i-logos-pytorch-icon
aliases:
  - source: osv
    value: pytorch
    ecosystem: PyPI
    confidence: curated
  - source: nvd_cpe
    value: pytorch
    confidence: curated
```

- [ ] **Step 2: Create Python (self-vendored, `type: software` for the interpreter)**

`vendors/python/vendor.yaml`:
```yaml
id: python
name: Python
icon: i-logos-python
aliases: []
```

`vendors/python/products/python.yaml`:
```yaml
id: python
vendor_id: python
name: Python
type: software
tags: [programming-language, runtime]
icon: i-logos-python
aliases:
  - source: nvd_cpe
    value: python
    confidence: curated
  - source: endoflife
    value: python
    confidence: curated
```

- [ ] **Step 3: Create OpenSSL (self-vendored, `type: library`)**

`vendors/openssl/vendor.yaml`:
```yaml
id: openssl
name: OpenSSL
icon: i-logos-openssl
aliases: []
```

`vendors/openssl/products/openssl.yaml`:
```yaml
id: openssl
vendor_id: openssl
name: OpenSSL
type: library
tags: [cryptography]
icon: i-logos-openssl
aliases:
  - source: nvd_cpe
    value: openssl
    confidence: curated
  - source: endoflife
    value: openssl
    confidence: curated
```

- [ ] **Step 4: Create Cisco vendor + `appliance`/`firmware` products**

`vendors/cisco/vendor.yaml`:
```yaml
id: cisco
name: Cisco
icon: i-logos-cisco
aliases:
  - source: nvd_cpe
    value: cisco
    confidence: curated
  - source: cisa_kev
    value: Cisco
    confidence: curated
```

`vendors/cisco/products/asa.yaml`:
```yaml
id: asa
vendor_id: cisco
name: Cisco Adaptive Security Appliance (ASA)
type: appliance
tags: [firewall, vpn, network-device]
icon: i-logos-cisco
aliases:
  - source: nvd_cpe
    value: adaptive_security_appliance
    confidence: curated
  - source: cisa_kev
    value: "Cisco Adaptive Security Appliance (ASA)"
    confidence: curated
services:
  - protocol: tcp
    port: 443
    name: https-admin
    default: true
```

`vendors/cisco/products/ios.yaml`:
```yaml
id: ios
vendor_id: cisco
name: Cisco IOS
type: firmware
tags: [network-device]
icon: i-logos-cisco
aliases:
  - source: nvd_cpe
    value: ios
    confidence: curated
  - source: cisa_kev
    value: "Cisco IOS"
    confidence: curated
```

- [ ] **Step 5: Create Canonical/Ubuntu**

`vendors/canonical/vendor.yaml`:
```yaml
id: canonical
name: Canonical
icon: i-logos-ubuntu
aliases:
  - source: nvd_cpe
    value: canonical
    confidence: curated
```

`vendors/canonical/products/ubuntu.yaml`:
```yaml
id: ubuntu
vendor_id: canonical
name: Ubuntu
type: os
tags: [operating-system]
icon: i-logos-ubuntu
aliases:
  - source: nvd_cpe
    value: ubuntu_linux
    confidence: curated
  - source: endoflife
    value: ubuntu
    confidence: curated
```

- [ ] **Step 6: Create Red Hat/RHEL**

`vendors/redhat/vendor.yaml`:
```yaml
id: redhat
name: Red Hat
icon: i-logos-redhat-icon
aliases:
  - source: nvd_cpe
    value: redhat
    confidence: curated
  - source: cisa_kev
    value: "Red Hat"
    confidence: curated
```

`vendors/redhat/products/rhel.yaml`:
```yaml
id: rhel
vendor_id: redhat
name: Red Hat Enterprise Linux
type: os
tags: [operating-system]
icon: i-logos-redhat-icon
aliases:
  - source: nvd_cpe
    value: enterprise_linux
    confidence: curated
  - source: endoflife
    value: rhel
    confidence: curated
```

- [ ] **Step 7: Validate**

Run: `uv run tools/validate.py`
Expected: `All vendor and product entries are valid.`

- [ ] **Step 8: Commit**

```bash
git add vendors/
git commit -m "data: seed pytorch, python, openssl, cisco, canonical, redhat vendors and products"
```

---

### Task 12: Seed vendor data — batch 3 (Apache/Redis/Docker/Kubernetes/Fortinet/Atlassian/Dell — completes hardware coverage)

**Files:**
- Create: `vendors/apache/vendor.yaml`, `vendors/apache/products/log4j.yaml`
- Create: `vendors/redis/vendor.yaml`, `vendors/redis/products/redis.yaml`
- Create: `vendors/docker/vendor.yaml`, `vendors/docker/products/docker-engine.yaml`
- Create: `vendors/kubernetes/vendor.yaml`, `vendors/kubernetes/products/kubernetes.yaml`
- Create: `vendors/fortinet/vendor.yaml`, `vendors/fortinet/products/fortios.yaml`
- Create: `vendors/atlassian/vendor.yaml`, `vendors/atlassian/products/confluence.yaml`
- Create: `vendors/dell/vendor.yaml`, `vendors/dell/products/poweredge-r740.yaml`

- [ ] **Step 1: Create Apache/Log4j (real vendor, `type: library`)**

`vendors/apache/vendor.yaml`:
```yaml
id: apache
name: Apache Software Foundation
icon: i-logos-apache
aliases:
  - source: nvd_cpe
    value: apache
    confidence: curated
  - source: cisa_kev
    value: Apache
    confidence: curated
```

`vendors/apache/products/log4j.yaml`:
```yaml
id: log4j
vendor_id: apache
name: Apache Log4j
type: library
tags: [logging]
icon: i-logos-apache
aliases:
  - source: nvd_cpe
    value: log4j
    confidence: curated
  - source: osv
    value: "org.apache.logging.log4j:log4j-core"
    ecosystem: Maven
    confidence: curated
  - source: cisa_kev
    value: "Apache Log4j2"
    confidence: curated
```

- [ ] **Step 2: Create Redis (self-vendored)**

`vendors/redis/vendor.yaml`:
```yaml
id: redis
name: Redis
icon: i-logos-redis
aliases: []
```

`vendors/redis/products/redis.yaml`:
```yaml
id: redis
vendor_id: redis
name: Redis
type: software
tags: [database, cache]
icon: i-logos-redis
aliases:
  - source: nvd_cpe
    value: redis
    confidence: curated
  - source: endoflife
    value: redis
    confidence: curated
services:
  - protocol: tcp
    port: 6379
    name: redis
    default: true
```

- [ ] **Step 3: Create Docker**

`vendors/docker/vendor.yaml`:
```yaml
id: docker
name: Docker
icon: i-logos-docker-icon
aliases: []
```

`vendors/docker/products/docker-engine.yaml`:
```yaml
id: docker-engine
vendor_id: docker
name: Docker Engine
type: software
tags: [container-runtime]
icon: i-logos-docker-icon
aliases:
  - source: nvd_cpe
    value: docker
    confidence: curated
  - source: endoflife
    value: docker-engine
    confidence: curated
```

- [ ] **Step 4: Create Kubernetes (self-vendored)**

`vendors/kubernetes/vendor.yaml`:
```yaml
id: kubernetes
name: Kubernetes
icon: i-logos-kubernetes
aliases: []
```

`vendors/kubernetes/products/kubernetes.yaml`:
```yaml
id: kubernetes
vendor_id: kubernetes
name: Kubernetes
type: software
tags: [orchestration, container-runtime]
icon: i-logos-kubernetes
aliases:
  - source: nvd_cpe
    value: kubernetes
    confidence: curated
  - source: endoflife
    value: kubernetes
    confidence: curated
services:
  - protocol: tcp
    port: 6443
    name: kube-apiserver
    default: true
```

- [ ] **Step 5: Create Fortinet/FortiOS (`type: firmware`)**

`vendors/fortinet/vendor.yaml`:
```yaml
id: fortinet
name: Fortinet
icon: i-logos-fortinet
aliases:
  - source: nvd_cpe
    value: fortinet
    confidence: curated
  - source: cisa_kev
    value: Fortinet
    confidence: curated
```

`vendors/fortinet/products/fortios.yaml`:
```yaml
id: fortios
vendor_id: fortinet
name: FortiOS
type: firmware
tags: [firewall, vpn, network-device]
icon: i-logos-fortinet
aliases:
  - source: nvd_cpe
    value: fortios
    confidence: curated
  - source: cisa_kev
    value: FortiOS
    confidence: curated
```

- [ ] **Step 6: Create Atlassian/Confluence**

`vendors/atlassian/vendor.yaml`:
```yaml
id: atlassian
name: Atlassian
icon: i-logos-atlassian
aliases:
  - source: nvd_cpe
    value: atlassian
    confidence: curated
  - source: cisa_kev
    value: Atlassian
    confidence: curated
```

`vendors/atlassian/products/confluence.yaml`:
```yaml
id: confluence
vendor_id: atlassian
name: Confluence
type: software
tags: [collaboration-tool]
icon: i-logos-atlassian
aliases:
  - source: nvd_cpe
    value: confluence
    confidence: curated
  - source: cisa_kev
    value: "Atlassian Confluence"
    confidence: curated
services:
  - protocol: tcp
    port: 8090
    name: http
    default: true
```

- [ ] **Step 7: Create Dell/PowerEdge (`type: hardware` — the only entry of this type, completes coverage of all 6 types)**

`vendors/dell/vendor.yaml`:
```yaml
id: dell
name: Dell
aliases:
  - source: nvd_cpe
    value: dell
    confidence: curated
```

`vendors/dell/products/poweredge-r740.yaml`:
```yaml
id: poweredge-r740
vendor_id: dell
name: Dell PowerEdge R740
type: hardware
tags: [hardware-appliance]
aliases:
  - source: nvd_cpe
    value: poweredge_r740_firmware
    confidence: auto
```

(No `services` — `hardware` is not in `SERVICES_ALLOWED_TYPES`, and this fixes the real-world example the schema-only tests don't cover: an actual hardware entry with no services block.)

- [ ] **Step 8: Validate and run full test suite**

Run: `uv run tools/validate.py && uv run pytest`
Expected: `All vendor and product entries are valid.`, all tests pass. 17 vendors, 19 products total, all 6 `type` values represented.

- [ ] **Step 9: Commit**

```bash
git add vendors/
git commit -m "data: seed apache, redis, docker, kubernetes, fortinet, atlassian, dell vendors and products"
```

---

### Task 13: Generate `examples/aliases.json`

**Files:**
- Create: `examples/aliases.json`, `examples/by-source/nvd_cpe.json`, `examples/by-source/cisa_kev.json`, `examples/by-source/endoflife.json`, `examples/by-source/osv.json`

**Interfaces:**
- Consumes: `tools/build_index.py` (Task 9) run against the full seed `vendors/` tree (Tasks 10–12).

- [ ] **Step 1: Run build_index.py against examples/**

Run: `uv run tools/build_index.py --output-dir examples --generated-at 2026-08-27T00:00:00Z`
Expected: creates `examples/aliases.json` and `examples/by-source/*.json`.

- [ ] **Step 2: Spot-check the output**

Run: `python -c "import json; d = json.load(open('examples/aliases.json')); print(len(d['entries']), 'entries')"`
Expected: `36 entries` (17 vendors + 19 products).

- [ ] **Step 3: Commit**

```bash
git add examples/
git commit -m "docs: commit example generated index for the seed data"
```

---

### Task 14: Static search site

**Files:**
- Create: `site/index.html`, `site/style.css`, `site/search.js`
- Create: `site/vendor/fuse.min.js` (vendored third-party file — see Step 1)

**Interfaces:**
- Consumes: `index/aliases.json` at runtime (relative fetch — works against both the real published index at the Pages root and, locally, a copy of `examples/aliases.json`).
- Produces: nothing consumed by other tasks — this is the final user-facing artifact, wired into Task 15's publish workflow.

- [ ] **Step 1: Vendor Fuse.js**

Download the official Fuse.js UMD build (the `fuse.js` npm package's `dist/fuse.min.js`, v7.x) and save it to `site/vendor/fuse.min.js`. This plan cannot embed third-party binary/minified content directly — fetch it via `npm pack fuse.js@7 --pack-destination /tmp && tar xf /tmp/fuse.js-*.tgz -C /tmp && cp /tmp/package/dist/fuse.min.js site/vendor/fuse.min.js`, or copy it from an existing `node_modules/fuse.js/dist/fuse.min.js` if available locally. Verify it loads by checking the file starts with a UMD wrapper (`(function(global,factory){` or similar) and exposes `window.Fuse`.

- [ ] **Step 2: Create `site/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Nomos — Vendor/Product Identity Search</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <main>
    <h1>Nomos</h1>
    <p>Search canonical vendors and products, and every source alias for each.</p>
    <input id="search-box" type="search" placeholder="Search e.g. windows_server, Cisco, pytorch..." autofocus />
    <div id="status"></div>
    <ul id="results"></ul>
  </main>
  <script src="vendor/fuse.min.js"></script>
  <script src="search.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create `site/style.css`**

```css
:root {
  color-scheme: light dark;
  --fg: #1a1a1a;
  --bg: #ffffff;
  --muted: #666666;
  --border: #dddddd;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #eaeaea;
    --bg: #121212;
    --muted: #999999;
    --border: #333333;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: system-ui, sans-serif;
  color: var(--fg);
  background: var(--bg);
}
main {
  max-width: 720px;
  margin: 0 auto;
  padding: 2rem 1rem;
}
#search-box {
  width: 100%;
  padding: 0.75rem;
  font-size: 1rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg);
  color: var(--fg);
}
#results {
  list-style: none;
  padding: 0;
  margin-top: 1rem;
}
.result {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.75rem 1rem;
  margin-bottom: 0.75rem;
}
.result h2 {
  margin: 0 0 0.25rem;
  font-size: 1.1rem;
}
.result .meta {
  color: var(--muted);
  font-size: 0.85rem;
  margin-bottom: 0.5rem;
}
.result .tags span,
.result .services span {
  display: inline-block;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.1rem 0.4rem;
  margin: 0.1rem 0.25rem 0.1rem 0;
  font-size: 0.8rem;
}
.result .aliases dt {
  font-weight: 600;
  margin-top: 0.5rem;
}
.result .aliases dd {
  margin: 0.15rem 0 0.15rem 1rem;
  font-size: 0.9rem;
  color: var(--muted);
}
#status {
  color: var(--muted);
  font-size: 0.85rem;
  margin-top: 0.5rem;
}
```

- [ ] **Step 4: Create `site/search.js`**

```javascript
const INDEX_URL = "index/aliases.json";

async function loadIndex() {
  const statusEl = document.getElementById("status");
  try {
    const response = await fetch(INDEX_URL);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    statusEl.textContent = `Loaded ${data.entries.length} entries (generated ${data.generated_at}).`;
    return data.entries;
  } catch (err) {
    statusEl.textContent = `Failed to load index: ${err.message}`;
    return [];
  }
}

function buildSearchRecords(entries) {
  return entries.map((entry) => ({
    entry,
    name: entry.name,
    aliasValues: (entry.aliases || []).map((a) => a.value),
  }));
}

function renderResults(records) {
  const list = document.getElementById("results");
  list.innerHTML = "";
  for (const { entry } of records) {
    const li = document.createElement("li");
    li.className = "result";

    const title =
      entry.canonical_type === "product"
        ? `${entry.name} (${entry.vendor_id}/${entry.product_id})`
        : `${entry.name} (${entry.vendor_id})`;

    const tagsHtml = (entry.tags || []).map((t) => `<span>${t}</span>`).join("");
    const servicesHtml = (entry.services || [])
      .map((s) => `<span>${s.name} ${s.protocol}/${s.port}</span>`)
      .join("");

    const aliasesBySource = {};
    for (const alias of entry.aliases || []) {
      (aliasesBySource[alias.source] ||= []).push(alias);
    }
    const aliasesHtml = Object.entries(aliasesBySource)
      .map(([source, aliases]) => {
        const values = aliases
          .map((a) => `${a.value}${a.ecosystem ? ` (${a.ecosystem})` : ""} — ${a.confidence}`)
          .join(", ");
        return `<dt>${source}</dt><dd>${values}</dd>`;
      })
      .join("");

    li.innerHTML = `
      <h2>${title}</h2>
      <div class="meta">${entry.canonical_type}${entry.type ? ` · ${entry.type}` : ""}</div>
      <div class="tags">${tagsHtml}</div>
      <div class="services">${servicesHtml}</div>
      <dl class="aliases">${aliasesHtml}</dl>
    `;
    list.appendChild(li);
  }
}

async function main() {
  const entries = await loadIndex();
  const records = buildSearchRecords(entries);

  const fuse = new Fuse(records, {
    keys: ["name", "aliasValues"],
    threshold: 0.35,
    ignoreLocation: true,
  });

  renderResults(records.slice(0, 20));

  const searchBox = document.getElementById("search-box");
  searchBox.addEventListener("input", () => {
    const query = searchBox.value.trim();
    if (!query) {
      renderResults(records.slice(0, 20));
      return;
    }
    const results = fuse.search(query).map((r) => r.item);
    renderResults(results.slice(0, 30));
  });
}

main();
```

- [ ] **Step 5: Manually verify against the example index**

```bash
mkdir -p /tmp/nomos-site-check/index
cp -r site/. /tmp/nomos-site-check/
cp examples/aliases.json /tmp/nomos-site-check/index/aliases.json
cd /tmp/nomos-site-check && python3 -m http.server 8123
```

Open `http://localhost:8123` in a browser. Confirm: the page loads and shows "Loaded 36 entries"; typing `windows` filters to Windows Server/Exchange; typing `mysql` shows MySQL with its `database` tag and port `3306`; typing a source name like `cisa_kev` does not crash (no matches expected, since search only indexes names/alias values, not source names — this is expected behavior, not a bug). Stop the server with Ctrl+C when done.

- [ ] **Step 6: Commit**

```bash
git add site/
git commit -m "feat: add static Fuse.js search site for the published index"
```

---

### Task 15: GitHub Actions workflows

**Files:**
- Create: `.github/workflows/validate.yml`
- Create: `.github/workflows/suggest-match.yml`
- Create: `.github/workflows/publish-index.yml`

**Interfaces:**
- Consumes: `uv run tools/validate.py` (Task 7), `uv run tools/suggest_match.py --base-ref/--head-ref` (Task 8), `uv run tools/build_index.py` (Task 9), `site/` (Task 14).

**Before writing these files**, resolve the pinned commit SHA for each third-party action (this plan cannot fabricate real hashes):

```bash
for repo_tag in actions/checkout@v4.2.2 astral-sh/setup-uv@v5.4.1 actions/github-script@v7.0.1 peaceiris/actions-gh-pages@v4.0.0; do
  repo="${repo_tag%@*}"; tag="${repo_tag#*@}"
  sha=$(gh api "repos/${repo}/git/refs/tags/${tag}" --jq '.object.sha')
  echo "${repo}@${tag} -> ${sha}"
done
```

Substitute the printed SHAs into the `uses:` lines below in place of `<CHECKOUT_SHA>`, `<SETUP_UV_SHA>`, `<GITHUB_SCRIPT_SHA>`, `<GH_PAGES_SHA>`. Keep the `# vX.Y.Z` comment next to each pin so Dependabot (Task 17) can propose version bumps in human-readable terms.

- [ ] **Step 1: Create `.github/workflows/validate.yml`**

```yaml
name: Validate

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<CHECKOUT_SHA> # v4.2.2
      - uses: astral-sh/setup-uv@<SETUP_UV_SHA> # v5.4.1
        with:
          python-version: "3.12"
      - run: uv sync --all-extras
      - run: uv run ruff check .
      - run: uv run mypy --strict tools
      - run: uv run pytest
      - run: uv run tools/validate.py
```

- [ ] **Step 2: Create `.github/workflows/suggest-match.yml`**

This is a **separate workflow file** (not a second job in `validate.yml`) because it needs `pull_request_target` to get comment-write permissions on fork PRs — decision #3 from plan approval. Safety property: the checked-out ref is always `main` (trusted tooling), the PR's own content is fetched with `git fetch`/read via `git show` inside `tools/suggest_match.py` (Task 8) and only ever parsed with `yaml.safe_load`, never executed. No PR-supplied `pyproject.toml`, script, or workflow file is ever installed or run.

```yaml
name: Suggest Match

on:
  pull_request_target:
    branches: [main]
    paths:
      - "vendors/**"

permissions:
  contents: read

jobs:
  suggest-match:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - name: Checkout base branch (trusted tooling only)
        uses: actions/checkout@<CHECKOUT_SHA> # v4.2.2
        with:
          ref: main
          fetch-depth: 0
      - name: Fetch PR head as a data source only
        run: git fetch origin "pull/${{ github.event.pull_request.number }}/head:pr-head"
      - uses: astral-sh/setup-uv@<SETUP_UV_SHA> # v5.4.1
        with:
          python-version: "3.12"
      - run: uv sync --all-extras
      - name: Run fuzzy-match check
        id: match
        run: |
          {
            echo "comment<<NOMOS_EOF"
            uv run tools/suggest_match.py --base-ref main --head-ref pr-head
            echo "NOMOS_EOF"
          } >> "$GITHUB_OUTPUT"
      - name: Post comment
        if: steps.match.outputs.comment != 'NO_MATCH'
        uses: actions/github-script@<GITHUB_SCRIPT_SHA> # v7.0.1
        env:
          COMMENT_BODY: ${{ steps.match.outputs.comment }}
        with:
          script: |
            await github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: process.env.COMMENT_BODY,
            });
```

- [ ] **Step 3: Create `.github/workflows/publish-index.yml`**

```yaml
name: Publish Index

on:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@<CHECKOUT_SHA> # v4.2.2
      - uses: astral-sh/setup-uv@<SETUP_UV_SHA> # v5.4.1
        with:
          python-version: "3.12"
      - run: uv sync --all-extras
      - run: uv run tools/validate.py
      - name: Build index
        run: uv run tools/build_index.py --output-dir dist/index --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      - name: Assemble Pages site
        run: |
          mkdir -p dist
          cp -r site/. dist/
      - name: Deploy to gh-pages
        uses: peaceiris/actions-gh-pages@<GH_PAGES_SHA> # v4.0.0
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./dist
          publish_branch: gh-pages
```

Note the "Build index" step runs before "Assemble Pages site" — `site/` has no `index/` subdirectory of its own, so the `cp -r site/. dist/` step cannot clobber `dist/index/`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/
git commit -m "ci: add validate, suggest-match, and publish-index workflows"
```

(These workflows won't actually run until Task 18 pushes to a real `main` branch on GitHub — this repo has no remote `main` yet.)

---

### Task 16: Contributor documentation

**Files:**
- Create: `.github/CODEOWNERS`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Modify: `README.md` (replace Task 1's stub)
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`

- [ ] **Step 1: Create `.github/CODEOWNERS`**

```
# Schema and taxonomy changes require maintainer review — a new tag can
# never land in the same PR as the product/vendor that uses it.
/schema/ @b-mx
/taxonomy/tags.yaml @b-mx
```

- [ ] **Step 2: Create `.github/PULL_REQUEST_TEMPLATE.md`**

```markdown
## What does this PR add or change?

<!-- e.g. "Adds vendor `acme` and product `acme/widget`" -->

## Checklist

- [ ] `uv run tools/validate.py` passes locally
- [ ] `uv run pytest` passes locally
- [ ] No duplicate `(source, value)` alias pairs introduced
- [ ] Every tag referenced already exists in `taxonomy/tags.yaml` (if not, that's a separate PR — see CONTRIBUTING.md)
- [ ] Every alias cites its source (`nvd_cpe`, `cisa_kev`, `osv`, `endoflife`, etc.)
- [ ] This PR does not mix a new taxonomy tag with the product/vendor that uses it
```

- [ ] **Step 3: Write `README.md`**

```markdown
# Nomos

Nomos is a community-maintained mapping from vendor and product names, as
they appear across disparate security data sources, to a single canonical
identity per vendor and per product.

## Why

Every security data source names vendors and products differently: NVD
CPE uses `microsoft:windows_server`, CISA KEV writes "Microsoft
Corporation", endoflife.date uses `windows-server`. Nomos gives every
vendor and product a stable canonical id and records each source's alias
for it, so downstream tools (CVE ingestion, asset inventory matching,
vulnerability correlation) can resolve "these three strings are the same
thing" without re-deriving the mapping themselves.

Nomos is source-agnostic: it doesn't belong to or favor NVD, CISA, OSV, or
any other single source.

## Data model

- **Vendor** (`vendors/<id>/vendor.yaml`) — a canonical vendor, with a
  stable `id`, display `name`, and a list of source aliases.
- **Product** (`vendors/<id>/products/<id>.yaml`) — a canonical product
  nested under its vendor, with a `type` (`hardware`, `appliance`,
  `firmware`, `os`, `software`, `library`), tags, optional network
  `services`, and aliases.
- **Alias** — `{source, value, confidence}` (plus optional `ecosystem` for
  package-ecosystem sources like PyPI/npm). `confidence: curated` means a
  human verified it; `auto` means it came from an unverified match.
- **Tag** (`taxonomy/tags.yaml`) — the closed set of category labels a
  product can carry (e.g. `database`, `webserver`).

**Self-vendored products** — packages with no real company behind them —
use the same slug for both the vendor directory and the product, e.g.
`vendors/redis/vendor.yaml` and `vendors/redis/products/redis.yaml`. By
convention their `vendor.yaml` carries an empty `aliases: []` — see
`CONTRIBUTING.md` for why.

## Using the published index

On every merge to `main`, the full mapping is published to GitHub Pages:

- `index/aliases.json` — every vendor and product, flattened, with all
  aliases.
- `index/by-source/<source>.json` — just the aliases relevant to one
  source, for consumers that only care about one feed.

A worked example of the shape (built from this repo's seed data) is
committed at `examples/aliases.json` so you can see the format without a
Pages deploy.

The search site at the published Pages URL lets you check whether a
vendor/product is already mapped before opening a PR.

## Quickstart: adding an entry

See `CONTRIBUTING.md` for the full walkthrough. Short version:

1. `uv run tools/validate.py` to confirm the repo is clean before you start.
2. Add `vendors/<id>/vendor.yaml` (or reuse an existing vendor) and
   `vendors/<id>/products/<id>.yaml`.
3. Only use tags already in `taxonomy/tags.yaml` — a new tag needs its own
   PR first.
4. `uv run tools/validate.py` again, then open a PR.

## License

MIT — see `LICENSE`.
```

- [ ] **Step 4: Write `CONTRIBUTING.md`**

```markdown
# Contributing to Nomos

## Adding a new vendor + product

1. Pick a stable, lowercase, kebab-case `id` for the vendor. This is
   immutable once merged — downstream consumers key off it.
2. Create `vendors/<vendor-id>/vendor.yaml`:

   ```yaml
   id: acme
   name: Acme Corp
   aliases:
     - source: nvd_cpe
       value: acme
       confidence: curated
   ```

3. Create `vendors/<vendor-id>/products/<product-id>.yaml`:

   ```yaml
   id: widget
   vendor_id: acme
   name: Acme Widget
   type: software
   tags: [database]
   aliases:
     - source: nvd_cpe
       value: widget
       confidence: curated
   ```

4. If the vendor's alias value for a given source would be *identical* to
   the product's value for that same source (common for single-product
   vendors, e.g. `nginx`/`nginx`), don't add it at both levels — the
   `(source, value)` pair must be globally unique across the whole repo,
   vendor and product entries included. Put it on the product only, and
   leave the vendor's `aliases` as `[]` if nothing else distinguishes it.
   This is why self-vendored entries (see README) usually have an empty
   vendor-level alias list.

## Self-vendored products

If there's no real company behind the product (most npm/PyPI/crates
packages), the vendor directory reuses the product's own slug, e.g.
`vendors/pytorch/vendor.yaml` + `vendors/pytorch/products/pytorch.yaml`,
both `id: pytorch`. Add an `ecosystem` field to ecosystem-sourced aliases:

```yaml
aliases:
  - source: osv
    value: pytorch
    ecosystem: PyPI
    confidence: curated
```

## Multi-vendor CVEs

CISA KEV entries whose `vendorProject` is literally "Multiple Vendors" use
the sentinel vendor id `_multiple`. Don't try to resolve these to a real
vendor, and **don't create a `vendors/_multiple/vendor.yaml` file** — it's
a reserved id handled specially by consumers, not a real canonical entry.

## Tags

`taxonomy/tags.yaml` is the closed set of allowed tags. **A new tag can
never land in the same PR as the product that uses it** — `schema/**` and
`taxonomy/tags.yaml` require maintainer review (see `CODEOWNERS`), and
bundling a tag proposal with a product PR forces the reviewer to block an
otherwise-fine product entry on a taxonomy debate. Open the tag addition
as its own PR first; once merged, add your product referencing it.

## Running validation locally

```bash
uv sync --all-extras
uv run tools/validate.py   # schema, id/path, alias-uniqueness, tag, services checks
uv run pytest              # test suite
uv run ruff check .
uv run mypy --strict tools
```

`tools/validate.py` reports every violation it finds in one pass, not
just the first — read the whole output before fixing.

## What `suggest_match.py` comments mean

On PRs touching `vendors/**`, CI runs a fuzzy match of any new alias value
against the existing alias index and may leave a comment like:

> This alias looks similar to `some-vendor` — please confirm this is a
> distinct vendor/product, not a duplicate.

This is **never a merge blocker** — it's a heuristic (string similarity),
and plenty of genuinely distinct vendors/products have similar names
(`ubuntu` vs `ubuntu-core`, `postgresql` vs `postgres-operator`). If
you're confident yours is distinct, say so in the PR description and
proceed; a maintainer will weigh in if there's real ambiguity.
```

- [ ] **Step 5: Write `SECURITY.md`**

```markdown
# Security Policy

Nomos publishes a static mapping of vendor/product identities; it has no
runtime service, database, or credentials, so most classes of
vulnerability (injection, auth bypass, etc.) don't apply to the data
itself.

If you find a security issue in the *tooling* (`tools/validate.py`,
`tools/suggest_match.py`, `tools/build_index.py`), the CI workflows, or a
supply-chain concern (a compromised dependency, a malicious pull request
payload), please report it privately via GitHub's "Report a vulnerability"
button under this repo's Security tab rather than opening a public issue.

We aim to acknowledge reports within 5 business days.
```

- [ ] **Step 6: Commit**

```bash
git add .github/CODEOWNERS .github/PULL_REQUEST_TEMPLATE.md README.md CONTRIBUTING.md SECURITY.md
git commit -m "docs: add README, CONTRIBUTING, SECURITY, CODEOWNERS, PR template"
```

---

### Task 17: Dependabot configuration

**Files:**
- Create: `.github/dependabot.yml`

- [ ] **Step 1: Create `.github/dependabot.yml`**

```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: /
    schedule:
      interval: weekly
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
```

- [ ] **Step 2: Commit**

```bash
git add .github/dependabot.yml
git commit -m "ci: add Dependabot for pip and github-actions ecosystems"
```

---

### Task 18: Repo hardening — push, branch protection, security settings

**Files:** none (GitHub repo/branch configuration, not repo content).

**Interfaces:** consumes the full commit history from Tasks 1–17; requires `gh` authenticated as a user with admin on `b-mx/Nomos` (confirmed already true — see the repo config review above).

This task has real, externally-visible effects (first push to a public repo's `main`, and repo-wide security settings). Confirm with the user immediately before running Steps 2 and 4.

- [ ] **Step 1: Confirm working tree is clean and all prior tasks are committed**

Run: `git status && git log --oneline`
Expected: clean working tree; one commit per prior task, in order.

- [ ] **Step 2: Push to origin, creating `main` for the first time**

```bash
git push -u origin main
```

- [ ] **Step 3: Wait for and verify CI goes green**

```bash
gh run list --branch main --limit 5
```

Expected: `Validate` and `Publish Index` workflows both complete with `success`. If `Validate` fails, fix the underlying issue and push a new commit (don't force-push) before continuing — branch protection in Step 4 requires this exact check to have a real, referenceable name (`validate`, the job id).

- [ ] **Step 4: Apply branch protection to `main`**

```bash
gh api --method PUT repos/b-mx/Nomos/branches/main/protection --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["validate"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "require_code_owner_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

This requires: a PR (no direct pushes, admins included via `enforce_admins: true`), at least one approving review, a CODEOWNERS review whenever `schema/**` or `taxonomy/tags.yaml` is touched, the `validate` check passing and up to date with the base branch, and forbids force-pushes and branch deletion.

- [ ] **Step 5: Enable Dependabot security updates and branch cleanup on merge**

```bash
gh api --method PATCH repos/b-mx/Nomos --input - <<'EOF'
{
  "delete_branch_on_merge": true,
  "security_and_analysis": {
    "dependabot_security_updates": {"status": "enabled"}
  }
}
EOF
```

- [ ] **Step 6: Verify the settings took effect**

```bash
gh api repos/b-mx/Nomos/branches/main/protection
gh api repos/b-mx/Nomos --jq '{delete_branch_on_merge, security_and_analysis}'
```

Expected: protection JSON reflects Step 4's settings; `delete_branch_on_merge: true`; `dependabot_security_updates.status: "enabled"`.

- [ ] **Step 7: Verify the published site and index**

```bash
gh api repos/b-mx/Nomos/pages 2>&1 || echo "Pages not yet configured — enable it once for the gh-pages branch via: gh api --method POST repos/b-mx/Nomos/pages -f source[branch]=gh-pages -f source[path]=/"
```

If Pages isn't enabled yet, run the suggested `gh api` command once (this is a one-time repo setting, not something `publish-index.yml` can do for itself). Then fetch the published index and confirm it matches `examples/aliases.json` in shape:

```bash
curl -sf "https://b-mx.github.io/Nomos/index/aliases.json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['entries']), 'entries')"
```

Expected: `36 entries` (allow a minute or two after the first `publish-index.yml` run for Pages to serve the new content).

---

### Task 19: Final verification

**Files:** none.

- [ ] **Step 1: Full local check, exactly as CI runs it**

```bash
uv sync --all-extras
uv run ruff check .
uv run mypy --strict tools
uv run pytest -v
uv run tools/validate.py
```

Expected: all green, `All vendor and product entries are valid.`

- [ ] **Step 2: Confirm the site works against the live published index**

Open `https://b-mx.github.io/Nomos/` (from Task 18) in a browser. Confirm search-as-you-type works against the real, live `index/aliases.json` (not the local `examples/` copy this time).

- [ ] **Step 3: Confirm branch protection actually blocks a direct push**

```bash
git commit --allow-empty -m "test: verify branch protection blocks direct push"
git push origin main
```

Expected: **rejected** by GitHub (`protected branch hook declined`). Then:

```bash
git reset --hard HEAD~1
```

to drop the empty test commit locally (it was never accepted upstream, so this is safe — nothing else can depend on a commit that was rejected on push).

- [ ] **Step 4: Open a throwaway test PR to confirm required review + suggest-match**

Create a branch, add a trivially near-duplicate alias (e.g. propose `vendors/ngin-x/vendor.yaml` with alias value `nginx`), push, open a PR. Confirm: the `Validate` check runs, `Suggest Match` posts a comment flagging the similarity to `nginx`, and the merge button is disabled pending review. Close the PR and delete the branch without merging — it was only to exercise the CI/branch-protection path.

This completes the bootstrap: schema-validated data model, three tested CLI tools, 17 vendors / 19 products across all six `type`s, a published index + search site, and a `main` branch that only accepts reviewed PRs passing CI.
