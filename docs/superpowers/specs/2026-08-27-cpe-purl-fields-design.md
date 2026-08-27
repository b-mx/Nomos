# CPE / PURL fields + NVD CPE bulk importer

Status: draft, pending user review
Date: 2026-08-27

## Purpose

Nomos already resolves vendor/product identity across CISA KEV, endoflife.date,
and OSV, and records a CPE-flavored name fragment via the `nvd_cpe` alias
source (e.g. `adaptive_security_appliance`, not a full CPE string). This
project adds two capabilities:

1. **Bidirectional CPE/PURL lookup.** Given a CPE string or PURL, find the
   canonical Nomos vendor/product and everything known about it from other
   sources. Given a name, retrieve its CPE/PURL prefix.
2. **Bulk coverage from NVD's CPE match data.** A script that reads NVD's
   official CPE match dictionary and creates vendor/product entries for the
   top N vendors (by distinct product count) not yet in Nomos, plus backfills
   the new `cpe` field onto every existing entry NVD data confirms.

## Non-goals

- Per-version CPE/PURL tracking. Nomos operates at vendor+product
  granularity; `cpe`/`purl` are version-wildcarded prefixes, never a specific
  version.
- Inventing a `cpe`/`purl` value from a display name. Both fields are only
  ever set from data actually retrieved from NVD (for `cpe`) or an existing
  `osv` alias (for `purl`) — never guessed.
- An automated re-download of the NVD CPE match feed. NVD's site blocks
  scripted fetches (Cloudflare, confirmed while researching this); the
  importer reads a local file the user obtains manually. Automating that
  fetch is future scope if a reliable method turns up.

## Schema changes

### Rename `nvd_cpe` → `nvd` (alias source)

`nvd_cpe` was named for the CPE format but only ever stored the bare
vendor/product name fragment used for match-scoring, not a real CPE string.
Now that a real `cpe` field exists, keeping the alias source named `nvd_cpe`
would be confusing alongside it. Renamed to `nvd`.

Touches (mechanical, ~81 files):
- `data/schema/vendor.schema.json`, `data/schema/product.schema.json`:
  `"source": {"enum": [...]}` — `nvd_cpe` → `nvd`.
- Every `source: nvd_cpe` in `data/vendors/**/*.yaml` (~36 occurrences) →
  `source: nvd`.
- `tools/_common.py`, `tools/validate.py`, `tools/build_index.py`,
  `tools/suggest_match.py`: doc comments mentioning `nvd_cpe`. No hardcoded
  source-string logic needs to change — `by-source/<source>.json` grouping
  in `build_index.py` is data-driven, so `by-source/nvd_cpe.json` becomes
  `by-source/nvd.json` automatically on the next regen.
- `tmp/scripts/pull_endoflife.py`: 3 hardcoded `"nvd_cpe"` string literals
  (source of the alias it writes when a CPE identifier is found).
- `README.md`, `CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md`,
  `site/search.js` comments: prose references.

### New field: `cpe` (vendor and product, optional)

Full CPE 2.3 formatted string, sourced verbatim from NVD (version and
everything after it wildcarded to `*`), same field name on both:

```yaml
# vendor.yaml — part and product wildcarded (a vendor spans multiple parts/products)
cpe: "cpe:2.3:*:cisco:*:*:*:*:*:*:*:*:*"

# product.yaml — part+vendor+product fixed, everything else wildcarded
cpe: "cpe:2.3:h:cisco:adaptive_security_appliance:*:*:*:*:*:*:*:*"
```

Schema: `{"type": "string", "pattern": "^cpe:2\\.3:[*aoh]:[^:]+:[^:]+?:\\*(:\\*){7}$"}`
roughly — exact pattern finalized during implementation, validated by
`tools/validate.py` alongside everything else. Optional — omitted entirely
when Nomos has no confirmed NVD data for that entry (never backfilled with a
guess).

### New field: `purl` (product only, optional)

```yaml
purl: "pkg:pypi/pytorch"
```

Derived mechanically from an existing `osv` alias (`ecosystem` + `value`) via
a known ecosystem→PURL-type table — never invented independently. Today's
data only uses `PyPI` and `Maven` as ecosystems; the table covers the common
OSV ecosystem strings so it doesn't need revisiting per-import:

| OSV ecosystem | PURL type |
|---|---|
| PyPI | pypi |
| npm | npm |
| Maven | maven |
| Go | golang |
| crates.io | cargo |
| NuGet | nuget |
| RubyGems | gem |
| Packagist | composer |
| Hex | hex |
| Pub | pub |

Ecosystems not in this table are left unset rather than guessed. `Maven`
purls need a namespace (`pkg:maven/{groupId}/{artifactId}`) — OSV's `value`
for Maven is typically already `groupId:artifactId`, so the importer splits
on `:` and re-joins with `/`; if it isn't in that shape, `purl` is left unset
for that entry rather than guessed.

## Data source: NVD CPE match dictionary

Local file at `tmp/cache/nvdcpematch-2.0.tar.gz` (already present, sourced
from `https://nvd.nist.gov/feeds/json/cpematch/2.0/nvdcpematch-2.0.tar.gz`).
Inspected directly:

- 68 JSON chunks (`nvdcpematch-2.0-chunks/nvdcpematch-2.0-chunk-NNNNN.json`),
  ~3.5GB uncompressed total, 649,217 `matchString` entries.
- Each entry's `matchString.criteria` is a full CPE 2.3 URI **for one
  specific version** (e.g. `cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*`) — the
  same vendor:product pair repeats across every version NVD has an entry
  for. "Vendor with the most products" requires parsing everything and
  deduping to unique (vendor, product) pairs.
- No `title`/human-readable name field anywhere in this feed (that only
  exists in the separate CPE Dictionary feed, which the user did not
  provide). Display names for newly-created entries are heuristically
  formatted from the CPE slug (title-case, underscore→space) — same
  approach already used for CISA/endoflife imports, `confidence: auto`,
  human review expected.
- The `part` component (`a`/`o`/`h`) is a genuine type signal, mapped
  `a → software`, `o → os`, `h → hardware` — better default accuracy than
  CISA KEV's blanket `software` default.

## Import script: `tmp/scripts/pull_nvd_cpe.py`

Same house style as `pull_cisa_kev.py`/`pull_endoflife.py`: dry-run by
default, `--apply` to write, never edits an existing file's *other* fields
(only ever adds the new optional `cpe`/`purl` fields to existing files, or
creates brand-new files), reuses `_lib.py`'s `resolve_against_index`
matching machinery.

1. **Extract + reduce (cached).** Stream each of the 68 chunk files via
   `tarfile` (no full extraction to disk), parse `criteria` into
   `(vendor, product, part)`, build `{vendor_slug: {product_slug: part}}`.
   This reduced map is cached to `tmp/cache/nvd-cpe-reduced.json` (keyed off
   the source tarball's mtime) since the full parse is the expensive part
   and reruns (different `--top-n`, code iteration) shouldn't repeat it.
2. **Backfill pass** (always runs, not gated by `--top-n`). For every
   existing Nomos vendor/product with an `nvd` alias, check whether that
   exact fragment exists in the reduced map. If so and `cpe` isn't already
   set, set it (formatted per the vendor/product forms above). This never
   touches any other field.
3. **New-coverage pass.** Rank vendors in the reduced map by distinct
   product count, descending. Take the top `--top-n` (default 1000) whose
   vendor isn't already resolved (via `resolve_against_index`, same
   exact/fuzzy/review tiers as the existing importers) against current
   Nomos data. For each, create the vendor (if new) and its products
   (skipping ones that already resolve to an existing product), each with
   an `nvd` alias (`confidence: auto`) and a `cpe` field. Type set from
   `part` per the mapping above; tags left empty (no signal available).
4. **`purl` backfill.** Independent small pass: for every product with an
   `osv` alias and no `purl` set, apply the ecosystem table above.

CLI: `--apply`, `--top-n N` (default 1000), `--refresh` (bypass the reduced
map cache), `--threshold` (fuzzy match, default 85, same as other importers).

## Error handling

- Missing tarball: clear error naming the expected path and the source URL,
  noting NVD may require a browser (not a script) to download it.
- Malformed `criteria` string (unexpected token count): skip that entry,
  count it, report the count at the end — don't fail the whole run over a
  handful of bad rows in a 649K-entry feed.
- Existing-file writes (backfill) always go through the same schema
  validation as `tools/validate.py` would apply, so a bad `cpe` format can't
  silently land — dry-run reports it, `--apply` still runs `tools/validate.py`
  as a final check same as the review-UI's commit flow does.

## Testing

- Unit-test the CPE criteria parser and the vendor/product prefix formatter
  against a handful of real sample `criteria` strings (including edge cases:
  fewer than 13 colon-separated tokens, vendor/product containing escaped
  colons per CPE 2.3 quoting rules).
- Unit-test the ecosystem→PURL mapping function, including the Maven
  groupId:artifactId split.
- Manual smoke test: `--apply --top-n 5` against the real local tarball,
  confirm `tools/validate.py` passes and the file count/content looks sane,
  same verification style used for the CISA/endoflife importers.

## Resolved during spec review

CPE 2.3's escaped-colon quoting **does** appear in this feed — confirmed by
sampling chunk 1: 59 of 44,340 entries (~0.13%) have a component containing
`\:`, almost all Perl-style module names (`Data::FormValidator` →
`data\:\:formvalidator`, `App::Context` → `app\:\:context`). A naive
`.split(":")` corrupts these. The parser must split on *unescaped* colons
only (e.g. `re.split(r'(?<!\\):', criteria)`, then unescape `\:` → `:` per
component) — not a hypothetical edge case, a confirmed ~1-in-750 occurrence
worth handling correctly from the start.

## Open items for implementation time

- Exact regex pattern for the `cpe` field in the JSON Schema (drafted above,
  not final).
