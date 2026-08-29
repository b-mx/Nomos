# Resolution Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the source importers and the review UI out of gitignored `tmp/` into tracked, lint-and-type-checked locations, and add the normalised alias-uniqueness guarantee the resolution API depends on.

**Architecture:** Three mechanical promotions plus one new validation check. The importers become a real `tools.sources` package (dropping a `sys.path` hack that only existed because they lived outside the tree), their tests join the committed pytest suite, and the review UI becomes `tools.review_ui` — which also ships a path-traversal fix in its only path guard. Nothing about the data model or published output changes in this plan.

**Tech Stack:** Python 3.12, PyYAML, jsonschema, rapidfuzz, pytest, ruff, mypy (strict), uv.

**Spec:** `docs/superpowers/specs/2026-08-29-source-resolution-api-design.md`

**Scope:** This is **plan 1 of 5** implementing that spec — it covers sequencing steps 1–3 only. See "Remaining plans" at the end. This plan is independently valuable: it ships a security fix and unblocks CI from running importer code, with no consumer-visible change.

**Deliberate deviation from the spec's step order.** The spec lists importers first ("unblocks CI; land first") and the review UI second. This plan promotes the **review UI first**, because it carries a path-traversal fix and has no dependency on any importer task — nothing here needs CI unblocked to proceed. All five tasks are mutually independent apart from Tasks 3 and 4 depending on Task 2, so the reorder costs nothing and gets the security fix merged soonest.

## Global Constraints

- Python `>=3.12`; `target-version = "py312"`.
- ruff `line-length = 100`, rules `["E", "F", "I", "UP", "B"]`. All moved code must pass `ruff check`.
- mypy `strict = true`. All moved code must pass `mypy --strict tools`.
- Dependencies are pinned exactly and must not be changed: `pyyaml==6.0.3`, `jsonschema==4.26.0`, `rapidfuzz==3.14.5`, `pytest==9.1.1`, `ruff==0.16.5`, `mypy==2.3.1`.
- `tmp/` stays in `.gitignore`. `tmp/cache/` (766 MB of upstream feeds) is **not** promoted and must never be committed.
- Key normalisation is **NFC + strip + collapse internal whitespace, case preserved**. Never casefold a key. CISA KEV publishes `IOS Software` and `IOS software` as distinct product strings.
- Only `cisa_kev` is API-eligible in v1.
- The files being moved are **untracked**. `git mv` will not work on them; use `mv` then `git add`.

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `tools/sources/__init__.py` | Makes the importers a package |
| `tools/sources/_lib.py` | Shared importer helpers: fetch/cache, `split_product_names`, slugify, CPE/PURL, `write_new_vendor`/`write_new_product` |
| `tools/sources/pull_cisa_kev.py` | CISA KEV importer |
| `tools/sources/pull_endoflife.py` | endoflife.date importer |
| `tools/sources/pull_nvd_cpe.py` | NVD CPE bulk importer |
| `tools/review_ui/__init__.py` | Makes the review UI a package (hyphen → underscore; `review-ui` is not importable) |
| `tools/review_ui/server.py` | Local-only curation server |
| `tools/review_ui/index.html` | Its single-page front end |
| `tests/sources/__init__.py` | Test package marker |
| `tests/sources/test_split_product_names.py`, `test_cpe.py`, `test_purl.py`, `test_write.py`, `test_pull_nvd_cpe.py` | Promoted importer tests |
| `tests/test_review_ui.py` | New — covers the `resolve()` path guard |

**Modified:** `tools/_common.py` (adds `normalize_key_part`, `API_ELIGIBLE_SOURCES`), `tools/validate.py` (adds `validate_alias_uniqueness_normalized`), `tests/test_validate.py`, `pyproject.toml` (stale comment).

**Not promoted:** `tmp/scripts/dedup_cisa_batch.py` is a one-off batch script, not part of any recurring workflow, and the spec's move table omits it. It stays untracked.

---

### Task 1: Promote the review UI and fix its path guard

`resolve()` guards every write the UI performs, using `str.startswith` — which admits a sibling-prefix escape: `data/vendors-evil/x.yaml` passes. This ships the fix while the UI is still vendor-tab only, so the security change lands alone and reviewable.

`REPO_ROOT` inside `server.py` is computed as `Path(__file__).resolve().parent.parent.parent`, which is correct at both the old and new depth (two levels below the repo root). It needs no change.

**Files:**
- Create: `tools/review_ui/__init__.py`, `tools/review_ui/server.py`, `tools/review_ui/index.html`
- Create: `tests/test_review_ui.py`
- Delete: `tmp/review-ui/server.py`, `tmp/review-ui/index.html`

**Interfaces:**
- Consumes: `tools/validate.py` and `tools/build_index.py` via subprocess; the schema files under `data/schema/`.
- Produces: `tools.review_ui.server.resolve(rel_path: str) -> Path` and `tools.review_ui.server.WRITABLE_ROOTS: tuple[Path, ...]`. **Plan 3 adds `data/sources` to `WRITABLE_ROOTS` and adds the resolutions tab.**

- [ ] **Step 1: Move the files**

The directory is renamed `review-ui` → `review_ui`; a hyphen is not importable as a Python module.

```bash
mkdir -p tools/review_ui
touch tools/review_ui/__init__.py
mv tmp/review-ui/server.py  tools/review_ui/server.py
mv tmp/review-ui/index.html tools/review_ui/index.html
rmdir tmp/review-ui
```

- [ ] **Step 2: Update the module docstring**

Replace the `Usage:` line in `tools/review_ui/server.py`, which still names the old path:

```python
"""Local-only review UI for confidence:auto vendor/product entries.

Maintainer tooling: no auth, binds to 127.0.0.1 only, meant to run on a
maintainer's own machine. Not part of the published site.

Usage:
    uv run python -m tools.review_ui.server [--port 8765]

Then open http://127.0.0.1:8765/
"""
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_review_ui.py`:

```python
"""Covers the path guard that gates every write the review UI performs."""

from pathlib import Path

import pytest

from tools._common import REPO_ROOT
from tools.review_ui.server import resolve


def test_resolve_accepts_a_path_inside_data_vendors() -> None:
    path = resolve("data/vendors/apple/vendor.yaml")
    assert path == (REPO_ROOT / "data" / "vendors" / "apple" / "vendor.yaml").resolve()


def test_resolve_rejects_a_sibling_prefix_escape() -> None:
    # 'data/vendors-evil' shares a string prefix with 'data/vendors' but is a
    # different directory. A str.startswith guard lets it through.
    with pytest.raises(ValueError):
        resolve("data/vendors-evil/x.yaml")


def test_resolve_rejects_a_dotdot_traversal() -> None:
    with pytest.raises(ValueError):
        resolve("data/vendors/../../etc/passwd")


def test_resolve_rejects_a_path_outside_the_repo() -> None:
    with pytest.raises(ValueError):
        resolve("/etc/passwd")
```

- [ ] **Step 4: Run the tests to verify the escape currently succeeds**

```bash
uv run pytest tests/test_review_ui.py -v
```

Expected: `test_resolve_rejects_a_sibling_prefix_escape` **FAILS** (`DID NOT RAISE ValueError`) — that is the vulnerability. The other three should pass.

- [ ] **Step 5: Replace the path guard**

In `tools/review_ui/server.py`, replace the `resolve()` function (currently at line 52) and add the allowlist constant beside the other module constants:

```python
# Every path the UI may write to. Checked with Path.is_relative_to rather
# than str.startswith: 'data/vendors-evil' shares a string prefix with
# 'data/vendors' but is a different directory.
WRITABLE_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "data" / "vendors",
)


def resolve(rel_path: str) -> Path:
    path = (REPO_ROOT / rel_path).resolve()
    if not any(path.is_relative_to(root.resolve()) for root in WRITABLE_ROOTS):
        raise ValueError(f"path {rel_path!r} is outside the writable allowlist")
    return path
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest tests/test_review_ui.py -v
```

Expected: 4 passed.

- [ ] **Step 7: Fix ruff and mypy**

```bash
uv run ruff check tools/review_ui tests/test_review_ui.py
uv run mypy --strict tools tests
```

ruff reports 8 findings, including `E501` at line **399**. mypy reports 24, dominated by `type-arg` (11) and `no-untyped-def` (4) — annotate `load_yaml`, `save_yaml`, `build_groups`, the `Handler` methods, and the `dict` returns with concrete types such as `dict[str, Any]` and `list[dict[str, Any]]`. The 8 `no-untyped-call` errors resolve once their callees are annotated.

- [ ] **Step 8: Verify the server still starts and serves**

```bash
uv run python -m tools.review_ui.server --port 8765 &
sleep 2 && curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/
kill %1
```

Expected: `200`. If it fails to start, the module-level schema loads are resolving `SCHEMA_DIR` wrongly — check `REPO_ROOT` still points at the repo root from the new depth.

- [ ] **Step 9: Run the full gate**

```bash
uv run ruff check . && uv run mypy --strict tools tests && uv run pytest && uv run tools/validate.py
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add tools/review_ui tests/test_review_ui.py
git commit -m "fix: replace review UI path guard with an explicit allowlist

resolve() gated every write with str.startswith against data/vendors,
which admits a sibling-prefix escape: 'data/vendors-evil/x.yaml' passed.
Now checked with Path.is_relative_to against WRITABLE_ROOTS.

Promotes the UI from gitignored tmp/review-ui/ to tools/review_ui/ so it
is reviewable and runs under ruff, mypy --strict, and pytest. It stays
local-only, binds 127.0.0.1, and is not part of the published site."
```

---
### Task 2: Promote `_lib.py` and its unit tests into `tools.sources`

`_lib.py` is the core module the other three importers import. It currently has **zero** mypy errors — the only work is dropping its `sys.path` hack, wrapping five long lines, and repointing its tests.

**Files:**
- Create: `tools/sources/__init__.py`, `tools/sources/_lib.py`, `tests/sources/__init__.py`
- Create: `tests/sources/test_split_product_names.py`, `tests/sources/test_cpe.py`, `tests/sources/test_purl.py`, `tests/sources/test_write.py`
- Delete: `tmp/scripts/_lib.py`, `tmp/scripts/test_split_product_names.py`, `tmp/scripts/test_cpe.py`, `tmp/scripts/test_purl.py`, `tmp/scripts/test_write.py`

**Interfaces:**
- Consumes: `tools._common.{REPO_ROOT, ProductEntry, VendorEntry, iter_products, iter_vendors, load_taxonomy_tags}`; `tools.suggest_match.{AliasRecord, ...}` — all unchanged.
- Produces: module `tools.sources._lib`, exporting at minimum `split_product_names(raw: str) -> list[str]`, `slugify(text: str) -> str`, `NewVendor`, `NewProduct`, `write_new_vendor(v: NewVendor) -> Path`, `write_new_product(p: NewProduct) -> Path`, `CPE_PART_TO_TYPE`, `escape_cpe_component`, `format_cpe_prefix`, `parse_cpe_criteria`, `split_cpe_criteria`, `ecosystem_to_purl`. Tasks 3 and 4 import from here.

- [ ] **Step 1: Create the package directories**

```bash
mkdir -p tools/sources tests/sources
touch tools/sources/__init__.py tests/sources/__init__.py
```

- [ ] **Step 2: Move the library and its four test files**

These files are untracked, so `git mv` will fail. Use plain `mv`:

```bash
mv tmp/scripts/_lib.py                      tools/sources/_lib.py
mv tmp/scripts/test_split_product_names.py  tests/sources/test_split_product_names.py
mv tmp/scripts/test_cpe.py                  tests/sources/test_cpe.py
mv tmp/scripts/test_purl.py                 tests/sources/test_purl.py
mv tmp/scripts/test_write.py                tests/sources/test_write.py
```

Moving (not copying) matters: a leftover copy under `tmp/scripts/` would be collected by pytest a second time and the same tests would run twice under different module names.

- [ ] **Step 3: Drop the `sys.path` hack from `tools/sources/_lib.py`**

Replace the module docstring and the import block at the top. Currently:

```python
"""Shared helpers for the CISA KEV / endoflife.date import scripts.

Not part of the Nomos package — lives under tmp/ (gitignored) and is run
via `uv run tmp/scripts/<script>.py` from the repo root so `tools._common`
and `tools.suggest_match` are importable.
"""
```

Replace that docstring with:

```python
"""Shared helpers for the source import scripts (CISA KEV, endoflife.date, NVD CPE).

Part of the `tools.sources` package. Run the importers with
`uv run python -m tools.sources.pull_cisa_kev` from the repo root.
"""
```

Then delete this line entirely (it is line 20, just above the `tools._common` import):

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
```

and remove the two `# noqa: E402` comments that only existed to silence the resulting import-after-statement warnings, so the imports read:

```python
from tools._common import (
    REPO_ROOT,
    ProductEntry,
    VendorEntry,
    iter_products,
    iter_vendors,
    load_taxonomy_tags,
)
from tools.suggest_match import (
```

`sys` may now be unused — ruff's `F401` will say so in step 6. If it is unused, remove `import sys`.

- [ ] **Step 4: Repoint the test imports**

In `tests/sources/test_split_product_names.py`, `test_cpe.py`, and `test_purl.py`, change the bare import to a package import. For example, `test_split_product_names.py`:

```python
from tools.sources._lib import split_product_names
```

`test_cpe.py`:

```python
from tools.sources._lib import (
    CPE_PART_TO_TYPE,
    escape_cpe_component,
    format_cpe_prefix,
    parse_cpe_criteria,
    split_cpe_criteria,
)
```

In `tests/sources/test_write.py`, change `import _lib` to:

```python
from tools.sources import _lib
```

Also update the stale "Run directly: uv run pytest tmp/scripts/..." docstring at the top of each of the four files to name its new path.

- [ ] **Step 5: Run the moved tests**

```bash
uv run pytest tests/sources -v
```

Expected: PASS. If any test fails with `ModuleNotFoundError: No module named '_lib'`, a bare import was missed in step 4.

- [ ] **Step 6: Fix ruff findings**

```bash
uv run ruff check tools/sources tests/sources
```

`E501` is not auto-fixable; wrap these lines manually. In `tools/sources/_lib.py` they are at **64, 104, 218, 258, 368**; in `tests/sources/test_cpe.py` at **75**; in `tests/sources/test_write.py` at **34** and **57**. Re-run until clean.

- [ ] **Step 7: Add type annotations until mypy is clean**

```bash
uv run mypy --strict tools tests/sources
```

`_lib.py` itself reports zero errors. The four test files report 29, essentially all `no-untyped-def` — every test function needs `-> None`, and the fixtures need parameter types. For example, in `tests/sources/test_write.py`:

```python
@pytest.fixture
def isolated_vendors_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ...


def test_write_new_vendor_includes_cpe_when_set(isolated_vendors_dir: Path) -> None:
    ...
```

`tests/sources/test_write.py` will need `from pathlib import Path` added. Where mypy reports `var-annotated`, add the annotation it suggests in the hint.

- [ ] **Step 8: Run the full gate**

```bash
uv run ruff check . && uv run mypy --strict tools && uv run pytest && uv run tools/validate.py
```

Expected: all four pass. `pytest` collects the four promoted files from their new location only.

- [ ] **Step 9: Commit**

```bash
git add tools/sources tests/sources
git commit -m "refactor: promote importer library into tracked tools.sources package

_lib.py and its four unit test files move out of gitignored tmp/scripts/
into tools/sources/ and tests/sources/. Dropping the sys.path hack that
existed only because the module lived outside the package tree, and adding
the type annotations mypy --strict requires.

CI can now run this code; previously it existed only on one machine."
```

---

### Task 3: Promote the CISA KEV and endoflife importers

**Files:**
- Create: `tools/sources/pull_cisa_kev.py`, `tools/sources/pull_endoflife.py`
- Delete: `tmp/scripts/pull_cisa_kev.py`, `tmp/scripts/pull_endoflife.py`

**Interfaces:**
- Consumes: `tools.sources._lib` (Task 2); `tools._common.REPO_ROOT`; `tools.suggest_match.AliasRecord`.
- Produces: `tools.sources.pull_cisa_kev.main() -> int` and `tools.sources.pull_endoflife.main() -> int`. Plan 2 modifies `pull_cisa_kev` to consume curated resolutions.

- [ ] **Step 1: Move both files**

```bash
mv tmp/scripts/pull_cisa_kev.py  tools/sources/pull_cisa_kev.py
mv tmp/scripts/pull_endoflife.py tools/sources/pull_endoflife.py
```

- [ ] **Step 2: Fix the re-export type errors**

Both files import `REPO_ROOT` and `AliasRecord` *through* `_lib`, which mypy rejects under `--strict` (`Module "_lib" does not explicitly export attribute`). Import them from their real homes instead. In `tools/sources/pull_cisa_kev.py` (around line 24) and `tools/sources/pull_endoflife.py` (around line 33), remove `REPO_ROOT` and `AliasRecord` from the `_lib` import list and add:

```python
from tools._common import REPO_ROOT
from tools.suggest_match import AliasRecord
```

Keep every other name importing from `._lib`, and change that import to be explicitly relative:

```python
from ._lib import (
    split_product_names,
    write_new_product,
    # ...remaining names unchanged
)
```

- [ ] **Step 3: Verify the type errors are gone**

```bash
uv run mypy --strict tools
```

Expected: no errors mentioning `pull_cisa_kev.py` or `pull_endoflife.py`. These were 4 of the 12 production-code errors.

- [ ] **Step 4: Fix ruff line lengths**

```bash
uv run ruff check tools/sources
```

Wrap manually. `pull_cisa_kev.py`: lines **46, 49, 90, 109, 110, 112, 187**. `pull_endoflife.py`: lines **90, 98, 156, 173, 175, 221, 250**. Re-run until clean.

- [ ] **Step 5: Verify the importers still run**

`tmp/cache/cisa-kev.json` is present locally, so this exercises the real path without network access:

```bash
uv run python -m tools.sources.pull_cisa_kev --dry-run --limit 5
```

Expected: exits 0 and prints its stats report. If `--dry-run` is not the exact flag name, check `main()`'s `argparse` setup and use the flag that suppresses writes. **Do not run it without a dry-run flag** — it writes into `data/vendors/`.

- [ ] **Step 6: Confirm nothing was written**

```bash
git status --porcelain data/
```

Expected: empty output. If not, the previous step wrote files; `git checkout -- data/` and re-run with the correct flag.

- [ ] **Step 7: Run the full gate**

```bash
uv run ruff check . && uv run mypy --strict tools && uv run pytest && uv run tools/validate.py
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add tools/sources
git commit -m "refactor: promote CISA KEV and endoflife importers into tools.sources

Both now import REPO_ROOT and AliasRecord from their real modules rather
than re-exporting them through _lib, which mypy --strict rejects."
```

---

### Task 4: Promote the NVD CPE importer and its test suite

The largest single chunk: 5 production type errors and 38 in the 488-line test file.

**Files:**
- Create: `tools/sources/pull_nvd_cpe.py`, `tests/sources/test_pull_nvd_cpe.py`
- Delete: `tmp/scripts/pull_nvd_cpe.py`, `tmp/scripts/test_pull_nvd_cpe.py`

**Interfaces:**
- Consumes: `tools.sources._lib` (Task 2); `tools._common.REPO_ROOT`; `tools.suggest_match.AliasRecord`.
- Produces: `tools.sources.pull_nvd_cpe.main() -> int`. No later task depends on its internals.

- [ ] **Step 1: Move both files**

```bash
mv tmp/scripts/pull_nvd_cpe.py      tools/sources/pull_nvd_cpe.py
mv tmp/scripts/test_pull_nvd_cpe.py tests/sources/test_pull_nvd_cpe.py
```

- [ ] **Step 2: Repoint imports**

In `tools/sources/pull_nvd_cpe.py` (around line 30), apply the same fix as Task 3 — remove `REPO_ROOT` and `AliasRecord` from the `_lib` import, add:

```python
from tools._common import REPO_ROOT
from tools.suggest_match import AliasRecord
```

and make the remaining `_lib` import relative (`from ._lib import ...`).

In `tests/sources/test_pull_nvd_cpe.py`, change any `import pull_nvd_cpe` / `from pull_nvd_cpe import ...` to:

```python
from tools.sources import pull_nvd_cpe
```

and any `from _lib import ...` to `from tools.sources._lib import ...`.

- [ ] **Step 3: Run the moved tests**

```bash
uv run pytest tests/sources/test_pull_nvd_cpe.py -v
```

Expected: PASS. Fix any remaining `ModuleNotFoundError` before continuing.

- [ ] **Step 4: Fix the three remaining production type errors**

```bash
uv run mypy --strict tools
```

Three errors in `pull_nvd_cpe.py`:

- Lines **139** and **145**: `Missing type arguments for generic type "dict"`. Replace the bare `dict` annotation with the concrete type, e.g. `dict[str, Any]`.
- Line **241**: `Need type annotation for "pending_alias_records"`. Annotate it:

```python
pending_alias_records: list[AliasRecord] = []
```

Re-run until `tools` is clean.

- [ ] **Step 5: Fix ruff findings**

```bash
uv run ruff check tools/sources tests/sources --fix
uv run ruff check tools/sources tests/sources
```

`--fix` resolves the `I001` import-sort in the test file. Then wrap the remaining `E501` lines manually: `pull_nvd_cpe.py` at **315, 317, 318, 322, 333**; `test_pull_nvd_cpe.py` at **141, 142, 443**.

- [ ] **Step 6: Annotate the test file**

```bash
uv run mypy --strict tools tests/sources
```

38 errors, almost all `no-untyped-def`. Add `-> None` to every test function and annotate helper functions and fixtures. The helper `_make_chunk` is called from typed contexts, so it needs a full signature — give it concrete parameter and return types matching its usage. Where mypy reports `var-annotated` for `reduced_map` (lines 268, 284), add the annotation from its hint.

- [ ] **Step 7: Run the full gate**

```bash
uv run ruff check . && uv run mypy --strict tools tests && uv run pytest && uv run tools/validate.py
```

Expected: all pass, with 0 mypy errors across the whole tree.

- [ ] **Step 8: Update the stale pyproject comment**

`pyproject.toml`'s `norecursedirs` comment explains itself in terms of `tmp/scripts/` test files, which no longer exist. The setting is still needed for `.claude/worktrees`. Replace the comment block with:

```toml
[tool.pytest.ini_options]
# .claude/worktrees/** holds full nested checkouts of this repo (used for
# isolated agent work) — without this, a bare `pytest` run from the repo
# root also recurses into them and re-collects the whole suite a second
# time from each nested checkout.
norecursedirs = [".claude", ".git", ".venv"]
```

- [ ] **Step 9: Commit**

```bash
git add tools/sources tests/sources pyproject.toml
git commit -m "refactor: promote NVD CPE importer and its tests into the tracked tree

Completes the importer promotion. All four importer modules and their
tests now run under ruff, mypy --strict, and pytest in CI.

Also refreshes the norecursedirs comment, which described a tmp/scripts/
collection hazard that no longer exists."
```

---

### Task 5: Normalised alias uniqueness

The spec's exact-resolution algorithm looks aliases up **normalised**, but `validate_alias_uniqueness` compares **raw** strings — so two `cisa_kev` aliases differing only in whitespace or Unicode composition would pass validation and then produce two normalised matches, breaking the "0 or 1" guarantee. Verified against the current tree: **zero** collisions exist today, so this lands as a pure guard with no data migration.

**Files:**
- Modify: `tools/_common.py` (add `normalize_key_part`, `API_ELIGIBLE_SOURCES`)
- Modify: `tools/validate.py` (add `validate_alias_uniqueness_normalized`, wire into `run_all_checks`)
- Modify: `tests/test_validate.py`

**Interfaces:**
- Produces: `tools._common.normalize_key_part(value: str) -> str` and `tools._common.API_ELIGIBLE_SOURCES: frozenset[str]`. **Plans 2–4 depend on `normalize_key_part` for snapshot keys, hashing, and exact resolution — it is the single normalisation definition for the whole spec.**
- Produces: `tools.validate.validate_alias_uniqueness_normalized(vendors: list[VendorEntry], products: list[ProductEntry]) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_validate.py`:

```python
def test_normalize_key_part_collapses_whitespace_and_preserves_case() -> None:
    from tools._common import normalize_key_part

    assert normalize_key_part("  Apple  ") == "Apple"
    assert normalize_key_part("iOS,\tiPadOS,  and   watchOS") == "iOS, iPadOS, and watchOS"
    # Case is deliberately preserved: CISA KEV publishes both of these as
    # distinct product strings, and folding them would merge two keys.
    assert normalize_key_part("IOS Software") != normalize_key_part("IOS software")


def test_normalized_product_alias_collision_is_caught_within_a_vendor() -> None:
    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness_normalized

    products = [
        ProductEntry(
            path=Path("data/vendors/cisco/products/ios.yaml"),
            data={"aliases": [{"source": "cisa_kev", "value": "IOS Software", "confidence": "auto"}]},
            vendor_id="cisco",
        ),
        ProductEntry(
            path=Path("data/vendors/cisco/products/ios-dup.yaml"),
            data={
                "aliases": [
                    {"source": "cisa_kev", "value": "IOS  Software", "confidence": "auto"}
                ]
            },
            vendor_id="cisco",
        ),
    ]
    errors = validate_alias_uniqueness_normalized([], products)
    assert len(errors) == 1
    assert "ios.yaml" in errors[0]
    assert "ios-dup.yaml" in errors[0]


def test_normalized_alias_check_preserves_case_distinction() -> None:
    # 'IOS Software' and 'IOS software' are two real, distinct CISA KEV
    # product strings. They must NOT be reported as a collision.
    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness_normalized

    products = [
        ProductEntry(
            path=Path("data/vendors/cisco/products/ios-software.yaml"),
            data={"aliases": [{"source": "cisa_kev", "value": "IOS Software", "confidence": "auto"}]},
            vendor_id="cisco",
        ),
        ProductEntry(
            path=Path("data/vendors/cisco/products/ios-software-lower.yaml"),
            data={"aliases": [{"source": "cisa_kev", "value": "IOS software", "confidence": "auto"}]},
            vendor_id="cisco",
        ),
    ]
    assert validate_alias_uniqueness_normalized([], products) == []


def test_normalized_alias_check_ignores_non_api_eligible_sources() -> None:
    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness_normalized

    products = [
        ProductEntry(
            path=Path("data/vendors/cisco/products/a.yaml"),
            data={"aliases": [{"source": "nvd", "value": "ios software", "confidence": "auto"}]},
            vendor_id="cisco",
        ),
        ProductEntry(
            path=Path("data/vendors/cisco/products/b.yaml"),
            data={"aliases": [{"source": "nvd", "value": "ios  software", "confidence": "auto"}]},
            vendor_id="cisco",
        ),
    ]
    assert validate_alias_uniqueness_normalized([], products) == []


def test_normalized_product_alias_collision_is_scoped_per_vendor() -> None:
    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness_normalized

    products = [
        ProductEntry(
            path=Path("data/vendors/synology/products/chat.yaml"),
            data={"aliases": [{"source": "cisa_kev", "value": "Chat", "confidence": "auto"}]},
            vendor_id="synology",
        ),
        ProductEntry(
            path=Path("data/vendors/zoom/products/chat.yaml"),
            data={"aliases": [{"source": "cisa_kev", "value": "Chat", "confidence": "auto"}]},
            vendor_id="zoom",
        ),
    ]
    assert validate_alias_uniqueness_normalized([], products) == []


def test_normalized_vendor_alias_collision_is_global() -> None:
    from tools._common import VendorEntry
    from tools.validate import validate_alias_uniqueness_normalized

    vendors = [
        VendorEntry(
            path=Path("data/vendors/acme-one/vendor.yaml"),
            data={"aliases": [{"source": "cisa_kev", "value": "Acme Corp", "confidence": "auto"}]},
        ),
        VendorEntry(
            path=Path("data/vendors/acme-two/vendor.yaml"),
            data={
                "aliases": [{"source": "cisa_kev", "value": "Acme   Corp", "confidence": "auto"}]
            },
        ),
    ]
    errors = validate_alias_uniqueness_normalized(vendors, [])
    assert len(errors) == 1
    assert "acme-one" in errors[0]
    assert "acme-two" in errors[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_validate.py -k normalize -v
uv run pytest tests/test_validate.py -k normalized -v
```

Expected: FAIL with `ImportError: cannot import name 'normalize_key_part'` and `cannot import name 'validate_alias_uniqueness_normalized'`.

- [ ] **Step 3: Add the normalisation helper to `tools/_common.py`**

Add `import unicodedata` to the import block (`re` is already imported). Then add below `KEBAB_CASE_RE`:

```python
# The one normalisation used for every source-key comparison and for the
# published API's key hashes. Case is deliberately preserved: CISA KEV
# publishes "IOS Software" and "IOS software" as two distinct product
# strings, and folding them would silently merge two keys.
_WHITESPACE_RUN_RE = re.compile(r"\s+")

# Sources whose aliases the resolution API resolves against. Only these are
# subject to normalised-uniqueness validation.
API_ELIGIBLE_SOURCES = frozenset({"cisa_kev"})


def normalize_key_part(value: str) -> str:
    """Normalise one half of a source key for exact lookup and hashing.

    NFC, strip, collapse internal whitespace runs to a single space. Case is
    NOT folded — see the comment above.
    """
    return _WHITESPACE_RUN_RE.sub(" ", unicodedata.normalize("NFC", value).strip())
```

- [ ] **Step 4: Add the validation check to `tools/validate.py`**

Extend the existing import from `tools._common` to include `API_ELIGIBLE_SOURCES` and `normalize_key_part`, then add this function directly below `validate_alias_uniqueness`:

```python
def validate_alias_uniqueness_normalized(
    vendors: list[VendorEntry], products: list[ProductEntry]
) -> list[str]:
    """Aliases for API-eligible sources must stay unique under the key
    normalisation the resolution API uses for lookup.

    validate_alias_uniqueness compares raw strings; the API looks aliases up
    normalised. Without this check, two aliases differing only in whitespace
    or Unicode composition would pass validation and then produce two matches
    for one source key, which the resolver treats as impossible."""
    errors: list[str] = []

    seen_vendor: dict[tuple[str, str], Path] = {}
    for vendor_entry in vendors:
        for alias in vendor_entry.data.get("aliases", []):
            source = alias.get("source", "")
            if source not in API_ELIGIBLE_SOURCES:
                continue
            key = (source, normalize_key_part(alias.get("value", "")))
            if key in seen_vendor:
                errors.append(
                    f"Vendor alias {key} collides under key normalisation with "
                    f"{_rel(seen_vendor[key])}, claimed by {_rel(vendor_entry.path)}"
                )
            else:
                seen_vendor[key] = vendor_entry.path

    seen_product: dict[tuple[str, str, str], Path] = {}
    for product_entry in products:
        for alias in product_entry.data.get("aliases", []):
            source = alias.get("source", "")
            if source not in API_ELIGIBLE_SOURCES:
                continue
            key = (
                product_entry.vendor_id,
                source,
                normalize_key_part(alias.get("value", "")),
            )
            if key in seen_product:
                errors.append(
                    f"Product alias {key} collides under key normalisation with "
                    f"{_rel(seen_product[key])}, claimed by {_rel(product_entry.path)}"
                )
            else:
                seen_product[key] = product_entry.path

    return errors
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/test_validate.py -k "normalize or normalized" -v
```

Expected: 6 passed.

- [ ] **Step 6: Wire the check into `run_all_checks`**

In `tools/validate.py`, add the call directly after the existing uniqueness check:

```python
    errors += validate_alias_uniqueness(vendors, products)
    errors += validate_alias_uniqueness_normalized(vendors, products)
```

- [ ] **Step 7: Verify the real dataset passes**

```bash
uv run tools/validate.py
```

Expected: exits 0 with no errors. This confirms the measured result that no normalised collisions exist across all 1,458 vendors and 28,391 products. **If it reports collisions, stop** — the plan's assumption is wrong and the findings need review before proceeding.

- [ ] **Step 8: Run the full gate**

```bash
uv run ruff check . && uv run mypy --strict tools tests && uv run pytest && uv run tools/validate.py
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add tools/_common.py tools/validate.py tests/test_validate.py
git commit -m "feat: validate alias uniqueness under key normalisation

The resolution API looks aliases up NFC-normalised with whitespace
collapsed, but validate_alias_uniqueness compares raw strings. Two
cisa_kev aliases differing only in whitespace would pass validation and
then match the same source key twice.

normalize_key_part deliberately preserves case: CISA KEV publishes
'IOS Software' and 'IOS software' as distinct product strings.

Verified clean against the current tree, so no data migration is needed."
```

---


## Verification

After all five tasks:

```bash
uv run ruff check .
uv run mypy --strict tools tests
uv run pytest
uv run tools/validate.py
git status --porcelain
```

Expected: four clean runs, and `git status` shows nothing under `data/` — this plan changes no data.

Confirm the promotion is complete:

```bash
ls tmp/scripts/          # only dedup_cisa_batch.py should remain
ls tools/sources/        # __init__, _lib, pull_cisa_kev, pull_endoflife, pull_nvd_cpe
```

## Remaining plans

This plan covers spec sequencing steps 1–3. The rest, each producing working software on its own:

| Plan | Spec steps | Deliverable |
|---|---|---|
| 2 — Resolution data model | 4–6 | `keys.json` snapshot + generator, `resolutions.schema.json`, referential integrity, importer consumes curated records, phantom-recreation regression test |
| 3 — Curation tooling | 7 | Resolutions tab, queue states, S1/S2 signal ranking; then the ~150-record backlog is curated as data work |
| 4 — Published API | 8–9 | `--api-output-dir`, key hashing, bundle + per-key files, manifest, published JSON Schemas, empty tombstone plumbing |
| 5 — Removal and drift | 10–11 | Phantom removal with tombstones, bidirectional drift workflow |

Plan 2 depends on `normalize_key_part` from Task 5 here.
