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
