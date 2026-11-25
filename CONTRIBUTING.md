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
   NumPy's vendor and product both legitimately claim `nvd: numpy`, since
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

## Licences

Products may carry an optional `licenses` list: which licence applies to which
versions of the product. Each item has:

| Field | Meaning |
|-------|---------|
| `spdx` | Required. An SPDX licence expression. |
| `versions` | Optional. The versions it covers, as a `vers:generic/` range. |
| `evidence` | Required. An `https://` link to a public source (below). |
| `confidence` | Optional, `curated` (default) or `auto`, as for aliases. |

```yaml
licenses:
  - spdx: BSD-3-Clause
    versions: vers:generic/<7.4.0
    evidence: https://github.com/redis/redis/blob/7.2.4/COPYING
  - spdx: LicenseRef-RSALv2 OR SSPL-1.0
    versions: vers:generic/>=7.4.0|<8.0.0
    evidence: https://redis.io/blog/redis-adopts-dual-source-available-licensing/
  - spdx: AGPL-3.0-only OR LicenseRef-RSALv2 OR SSPL-1.0
    versions: vers:generic/>=8.0.0
    evidence: https://redis.io/blog/agplv3/
```

**`spdx`.** Operators are `AND`, `OR`, `WITH` (uppercase) and parentheses.
Every licence id is on the [SPDX licence list](https://spdx.org/licenses/), spelled
as listed (write `GPL-2.0-or-later`, not `GPL-2.0+`), or is a custom
`LicenseRef-[A-Za-z0-9.-]+` for a licence that is not on the list, such as
`LicenseRef-RSALv2`. A bare `RSALv2` is rejected. Every exception after `WITH` is on the
SPDX exceptions list; a `LicenseRef-` cannot be an exception.

**`versions`.** `vers:generic/` followed by constraints joined by `|`. A
constraint is `<`, `<=`, `>`, `>=` or `!=` followed by a version, or a bare
version meaning equality (`vers:generic/8.0`; `vers:generic/=8.0` is refused).
Other schemes (`vers:pypi/...`) and `*` are refused; leave `versions` out to
cover every version. As the [vers specification](https://github.com/package-url/vers-spec)
requires, constraints are in ascending version order, each version appears once,
and the `<`/`<=` and `>`/`>=` constraints alternate (equality and `!=` aside).

Periods of one product must not overlap. At most one period may leave out
`versions`, and only when it is the product's only licence.

**Evidence.** An `https://` link to a public source for that licence *and
those versions*: the upstream LICENSE or COPYING file at a tag, or the project's
licensing announcement. Never guess a licence or a version boundary; if you
cannot cite a source, leave the period out.

### How Nomos compares `vers:generic` versions

The vers specification leaves the ordering of `generic` versions unspecified.
This is Nomos's rule, implemented in `tools/vers.py` and pinned by
`tests/fixtures/vers_generic_vectors.json`:

1. **Not a version:** the empty string; anything with a character outside
   `A-Za-z0-9.~+-` (so no whitespace, `*`, `_`, or the vers delimiters); a
   dangling or doubled dot (`1.`, `1..2`); a trailing separator (`1.0-`);
   an `x`/`X` component (`2.2.X`); a range (`2.2.X - 2.3.X`); anything that
   does not start with a digit (`latest`).
2. **Release and qualifier.** The release is the leading run of dot-separated
   numbers; everything after it is the qualifier. The qualifier starts at the
   first letter, or at `-`, `~` or `+`, or at a `.` followed by a letter.
3. **Releases** compare number by number; a missing number counts as 0, so
   trailing zeros never change an answer (`1` = `1.0` = `1.0.0`, `1.01` = `1.1`).
   Qualifiers are only compared when the releases are equal.
4. **Qualifier kinds:** pre-release < none < post-release. A pre-release is,
   after an optional `-`, `~` or `.`, one of `dev`, `alpha`, `beta`, `pre`,
   `preview`, `rc` (optionally followed by digits and dots), or the single letter
   `a` or `b` followed by a digit; case-insensitive. They order `dev` < `alpha`/`a`
   < `beta`/`b` < `pre`/`preview`/`rc`, then by number (`rc` < `rc1`).
   Anything else is a post-release, including a bare letter with no digit
   (`1.1.1b`).
5. **Two post-releases** compare as runs of digits and letters, separators
   ignored: digits numerically, letters case-insensitively, a letter run
   before a digit run at the same position, a prefix first
   (`2.4.58-1ubuntu2` < `2.4.58-2` < `2.4.58-10`; `1p1` < `1-1`).

Examples: `8.0-rc1` < `8.0rc2` < `8.0` = `8.0.0` < `8.0.1`;
`1.1.1` < `1.1.1b` < `1.1.1w` < `1.1.2`; `7.4.0` < `7.4.0.GA`. Because `1.0` and `1`
are equal, `vers:generic/8.0` contains `8.0` and `8.0.0` but not `8.0.1`.

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

## Releases

Releases are cut by a maintainer from the Actions tab: run the **Release**
workflow on `main`. It:

1. refuses to run on any branch other than `main`;
2. creates nothing unless the `Validate` workflow succeeded on the exact
   commit at the head of `main`;
3. creates an annotated tag `vYYYY.MM.DD` (UTC) on that commit, or
   `vYYYY.MM.DD.N` if the day's tag already exists;
4. publishes a GitHub Release for the tag. The repository zipball is the
   artifact; no extra assets are attached.

The release notes are generated: vendor, product and source counts from
`data/examples/stats.json`, the vendor/product files added, modified and
removed since the previous tag, whether `data/taxonomy/` or `data/schema/`
changed, and whether `tools/`, `pyproject.toml` or `uv.lock` changed.
Consumers may pin the validation tooling separately from the data, so a PR
that touches those files should say so in its description; it will be
called out in the next release's notes.

A release guarantees what `Validate` checks, that ids are stable, and that
`data/examples/` matches the data (see "Releases" in `README.md`), so keep
`data/examples/` regenerated in every PR that changes `data/vendors/`:

```bash
uv run tools/build_index.py --output-dir data/examples \
  --generated-at "$(python3 -c "import json;print(json.load(open('data/examples/aliases.json'))['generated_at'])")"
```
