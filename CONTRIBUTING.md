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
   Both may also carry an optional `domain` (see [Domains](#domains)).
   **Never guess either field from a display name** — only set them from
   data you've actually confirmed (e.g. an NVD CPE match, or an existing
   `osv` alias for `purl`).

4. A vendor and its product MAY share the same `(source, value)` pair — e.g.
   NGINX's vendor and product both legitimately claim `nvd: nginx`, since
   CPE's vendor and product fields are separate assertions even when the
   strings match. The global uniqueness rule is scoped per canonical type: two
   vendors (or two products) can never share a `(source, value)` pair, but a
   vendor and its own product can.

## Domains

Vendors and products may carry an optional `domain`: the **registrable
domain of the official website** (the part you would register with a
registrar, not a host under it), useful for linking an entry to its
official site and for branding.

```yaml
id: nginx
vendor_id: f5
name: NGINX
domain: nginx.org
```

- Lowercase, with no scheme, path, port, trailing dot or `www.`:
  `nginx.org`, not `https://nginx.org/`, `www.nginx.org` or `NGINX.org`.
- Internationalized domains use their punycode (`xn--`) form, e.g.
  `xn--bcher-kva.example` rather than `bücher.example`.
- The schema checks the shape; it cannot tell a registrable domain from a
  subdomain, so reviewers check that part.
- **Take it from an authoritative source and cite it in the PR:** the
  vendor's or project's own website, documentation or official repository.
  **Never guess a domain** from the name, and don't use a reseller,
  mirror, or third-party listing.
- Leave it out when there is no clear official site. A product shares its
  vendor's domain only if that is genuinely the product's own site; don't
  copy the vendor's domain onto every product.

## Banner and name aliases

Besides the feed sources (`nvd`, `cisa_kev`, `osv`, `endoflife`),
**products** (not vendors) accept two sources for names seen in the wild:

| Source | Meaning |
|--------|---------|
| `banner` | The product-identifying part of a network service banner (an HTTP `Server:` header, an SSH identification string, an FTP/SMTP greeting), normalized as below. |
| `name` | The product's name as commonly written, e.g. `NGINX`, `OpenSSH`, `Microsoft Exchange Server`. |

**`banner` normalization:**

1. Keep only the product token and its version. Drop protocol framing
   (`SSH-2.0-`, `220 `, the `Server:` header name), hostnames, IP
   addresses, OS/distribution comments such as `(Ubuntu)`, and anything
   after the product/version token.
2. Lowercase it.
3. Replace the version with `*`, keeping the separator in front of it
   (`/`, `_` or a space). If the banner carries no version, the value is
   the bare token.

| Observed banner | `banner` value |
|-----------------|----------------|
| `Server: openresty/1.21.4.1` | `openresty/*` |
| `SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.5` | `openssh_*` |
| `Server: Apache/2.4.58 (Ubuntu)` | `apache/*` |
| `Server: Microsoft-IIS/10.0` | `microsoft-iis/*` |
| `Server: nginx` (version hidden) | `nginx` |
| `220 (vsFTPd 3.0.5)` | `vsftpd *` |

The schema rejects values that are not lowercase, contain characters
other than `a-z 0-9 . _ + / * -` and single spaces, or still have a
version (a digit right after `/`, `_` or a space).

**`name` normalization:** write the name the way the vendor or project
writes it, case included, with single spaces and no leading or trailing
whitespace. Leave out version numbers unless they are part of the
product's name (e.g. `Microsoft 365`).

Both sources are matched without any vendor context, so unlike other
product aliases, **a `banner` or `name` value must be unique across all
products**, not just within one vendor. `tools/validate.py` enforces this.

**Evidence:** cite a public source in the PR for every `banner` or `name`
alias:

- `banner`: the product's own documentation or source code showing the
  banner format (e.g. the version string the software emits), or another
  public reference that documents it.
- `name`: the vendor's or project's own website or documentation using
  that spelling.

**Never let a value identify a private host, organization or internal
system.** Don't submit a banner copied from a host you scanned or operate
without stripping it to the normalized product token as described above.
Hostnames, IP addresses, internal product or project names, customer
names, and banners customized for one organization never belong in
Nomos.

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
