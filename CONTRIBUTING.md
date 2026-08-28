# Contributing to Nomos

## Adding a new vendor + product

1. Pick a stable, lowercase, kebab-case `id` for the vendor. This is
   immutable once merged — downstream consumers key off it.
2. Create `data/vendors/<vendor-id>/vendor.yaml`:

   ```yaml
   id: acme
   name: Acme Corp
   aliases:
     - source: nvd
       value: acme
       confidence: curated
   ```

3. Create `data/vendors/<vendor-id>/products/<product-id>.yaml`:

   ```yaml
   id: widget
   vendor_id: acme
   name: Acme Widget
   type: software
   tags: [database]
   aliases:
     - source: nvd
       value: widget
       confidence: curated
   ```

   Both files may also carry an optional `cpe` field (a full, version-
   wildcarded CPE 2.3 string, e.g. `cpe:2.3:a:acme:widget:*:*:*:*:*:*:*:*`),
   and products may carry an optional `purl` field (e.g. `pkg:pypi/widget`).
   **Never guess either field from a display name** — only set them from
   data you've actually confirmed (e.g. an NVD CPE match, or an existing
   `osv` alias for `purl`).

4. A vendor and its product MAY share the same `(source, value)` pair — e.g.
   NGINX's vendor and product both legitimately claim `nvd: nginx`, since
   CPE's vendor and product fields are separate assertions even when the
   strings match. The global uniqueness rule is scoped per canonical type: two
   vendors (or two products) can never share a `(source, value)` pair, but a
   vendor and its own product can.

## Self-vendored products

If there's no real company behind the product (most npm/PyPI/crates
packages), the vendor directory reuses the product's own slug, e.g.
`data/vendors/pytorch/vendor.yaml` + `data/vendors/pytorch/products/pytorch.yaml`,
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
vendor, and **don't create a `data/vendors/_multiple/vendor.yaml` file** —
it's a reserved id handled specially by consumers, not a real canonical
entry.

## Tags

`data/taxonomy/tags.yaml` is the closed set of allowed tags. **A new tag
can never land in the same PR as the product that uses it** —
`data/schema/**` and `data/taxonomy/tags.yaml` require maintainer review
(see `CODEOWNERS`), and
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

## Testing the site locally

`site/` has two pages — `index.html` (the project landing page) and
`search.html` (the search UI, powered by [Pagefind](https://pagefind.app),
built at publish time from the generated index). Neither the search index
nor the landing page's coverage numbers are committed, so build both from
the example data:

```bash
uv run tools/build_index.py --output-dir /tmp/nomos-site/index --generated-at 2026-01-01T00:00:00Z
cd site && npm ci && cd ..
node site/build-pagefind.mjs --index /tmp/nomos-site/index/aliases.json --output /tmp/nomos-site/pagefind
cp site/index.html site/search.html site/style.css site/search.js site/landing.js site/theme.js site/favicon.svg /tmp/nomos-site/
cp docs/nomos-banner.svg /tmp/nomos-site/
cd /tmp/nomos-site && python3 -m http.server 8000
```

Then open `http://localhost:8000/`. Use `data/examples/aliases.json`
instead of a fresh `tools/build_index.py` run if you just want to test
against the seed data without regenerating it.

## What `suggest_match.py` comments mean

On PRs touching `data/vendors/**`, CI runs a fuzzy match of any new alias value
against the existing alias index and may leave a comment like:

> This alias looks similar to `some-vendor` — please confirm this is a
> distinct vendor/product, not a duplicate.

This is **never a merge blocker** — it's a heuristic (string similarity),
and plenty of genuinely distinct vendors/products have similar names
(`ubuntu` vs `ubuntu-core`, `postgresql` vs `postgres-operator`). If
you're confident yours is distinct, say so in the PR description and
proceed; a maintainer will weigh in if there's real ambiguity.
