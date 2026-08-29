# Source resolution layer + read-only resolution API

Status: draft, pending user review
Date: 2026-08-29

## Purpose

Nomos maps vendor/product aliases to canonical ids, but a consumer holding a
raw source record cannot always use it. A CISA KEV record carries
`vendorProject` and `product` verbatim, and two shapes of that pair have no
correct answer in the current model:

1. **Fan-out.** `{"vendorProject": "Apple", "product": "iOS, iPadOS, and
   watchOS"}` denotes three canonical products. The importer splits the string
   at ingest and stores `iOS`, `iPadOS`, `watchOS` as three separate aliases;
   the raw compound string is stored nowhere. An exact-match lookup on what KEV
   actually published therefore misses.
2. **Sentinel.** `{"vendorProject": "Apple", "product": "Multiple Products"}`
   names no product at all. It is currently modelled as a real product
   (`data/vendors/apple/products/multiple-products.yaml`, `type: software`), so
   the lookup "succeeds" by returning a product that does not exist.

This project adds three capabilities:

1. **A resolution layer** — a per-source record mapping a verbatim source key
   to the canonical ids it denotes, including one-to-many fan-out and explicit
   sentinels.
2. **A curation workflow** — detection of source records that are not yet
   curated, ranked by how likely they are to be wrong, plus the tooling to
   record a human decision once so it survives dataset rebuilds.
3. **A read-only API** — the resolution layer and alias index published as
   static JSON on the existing GitHub Pages deployment, with no server to run.

## Non-goals

- **A live matching service.** No compute endpoint, no Worker, no function.
  Everything is a static file built by `tools/build_index.py` and deployed by
  `publish-index.yml`. Fuzzy matching against unseen input happens client-side
  against a corpus we publish, or at build time for keys already in the feed.
- **Fixing the splitter.** `split_product_names` stays as it is. Its
  shared-prefix/suffix limitation is documented and accepted; the resolution
  layer exists precisely so a human can override a bad split permanently rather
  than the splitter having to be right.
- **Per-version resolution.** Nomos operates at vendor+product granularity.
  A resolution target is a vendor or a product, never a version range.
- **Retroactive alias cleanup across all sources.** This spec covers
  `cisa_kev` end to end and defines a shape the other three sources can adopt
  later. It does not migrate `nvd`, `osv`, or `endoflife`.

## Background: measured scale

Against the live KEV catalog (1,682 records, cached at
`tmp/cache/cisa-kev.json`) and the current `data/vendors/` tree:

| | count | share |
|---|---|---|
| distinct `(vendorProject, product)` pairs | 706 | |
| raw string already an exact alias — no work | 556 | 79% |
| compound, needs a split decision | 93 | 13% |
| sentinel / collective (`Multiple Products`, `Multiple Routers`, …) | 35 | 5% |
| single name, no alias match | 22 | 3% |
| vendor-level fan-out (`D-Link and TRENDnet`) | 1 | |

**Day-one curation backlog is 150 records (21%).** Ongoing load is 2–3 per
month, since KEV adds 15–20 records monthly and most are 1:1.

The resolution layer holds *only* these exceptions. It does not grow with the
catalog; it grows with the catalog's irregularity. That is the property that
makes it maintainable.

### Existing damage this repairs

Because there is nowhere to record a compound string, bad splits were
materialised as canonical products. Confirmed on disk today:

```
data/vendors/atlassian/products/server.yaml                 name: Server
data/vendors/atlassian/products/data-center.yaml            name: Data Center
data/vendors/atlassian/products/data-server.yaml            name: Data Server
data/vendors/cisco/products/manager.yaml                    name: Manager
data/vendors/cisco/products/rv325-routers.yaml              name: RV325 Routers
data/vendors/gitlab/products/community.yaml                 name: Community
data/vendors/gitlab/products/enterprise-editions.yaml       name: Enterprise Editions
data/vendors/amcrest/products/cameras.yaml                  name: Cameras
data/vendors/ibm/products/server-hypervisor-edition.yaml    name: Server Hypervisor Edition
data/vendors/apple/products/multiple-products.yaml          name: Multiple Products
data/vendors/trend-micro/products/officescan-and-worry-free-business-security-agents.yaml
data/vendors/d-link-and-trendnet/vendor.yaml                (a vendor that does not exist)
```

"Data Center" is not an Atlassian product; it is half of "Confluence Data
Center".

The sentinel side is larger: **34 collective-name products** exist as canonical
entries, from `apple/multiple-products` through `zyxel/multiple-firewalls` and
`samsung/mobile-devices`.

**The phantom list is determined by review, not by rule.** The splitter is not
always wrong — `citrix/gateway`, `microsoft/wordpad`, `adobe/air`, and
`oracle/jre` are legitimate products it happened to get right. And a glob for
`multiple-*.yaml` returns 35 files, of which
`themeisle/products/multiple-page-generator.yaml` is a genuine WordPress
plugin. Every removal is a deliberate per-case decision; no heuristic gets a
vote on what is deleted.

## Design

### 1. The resolution layer

New file per source: `data/sources/<source>/resolutions.yaml`, a list of
records keyed on the verbatim source pair.

```yaml
- key: {vendor: "Apple", product: "iOS, iPadOS, and watchOS"}
  kind: split
  confidence: auto
  targets:
    - {vendor_id: apple, product_id: ios}
    - {vendor_id: apple, product_id: ipados}
    - {vendor_id: apple, product_id: watchos}

- key: {vendor: "Apple", product: "Multiple Products"}
  kind: sentinel
  confidence: curated
  targets:
    - {vendor_id: apple}
  note: "KEV names no specific product"

- key: {vendor: "Atlassian", product: "Confluence Data Center and Server"}
  kind: split
  confidence: curated          # splitter got this wrong; corrected by hand
  targets:
    - {vendor_id: atlassian, product_id: confluence-data-center}
    - {vendor_id: atlassian, product_id: confluence}

# CVE-2015-1187 — vendor fan-out AND sentinel product at the same time
- key: {vendor: "D-Link and TRENDnet", product: "Multiple Devices"}
  kind: sentinel
  confidence: curated
  targets:                      # fan-out across vendors, no product on either
    - {vendor_id: d-link}
    - {vendor_id: trendnet}
  note: "KEV names two vendors and no specific device"
```

The D-Link/TRENDnet record is the case that justifies the flat `targets` shape:
its vendor field fans out to two vendors *and* its product field identifies
nothing. A shape of `vendor_id` plus `product_ids[]` could not express it.

`targets` is a flat list of `{vendor_id, product_id?}` so that product fan-out,
cross-vendor fan-out, and vendor-only sentinels all use one shape. A target
with no `product_id` means the source record does not identify a product.

**`kind` describes the product dimension only.** Vendor fan-out is not a
`kind`; it is implicit in `targets` carrying more than one distinct
`vendor_id`. Without this rule the D-Link/TRENDnet record is ambiguous — a
vendor `split` and a product `sentinel` simultaneously — and two curators would
classify it differently. It is a `sentinel`, because that is what its product
field is.

- `split` — the product string denotes several canonical products
- `sentinel` — the product string names no product (`Multiple Products`)
- `exact` — a 1:1 override, used when a raw string must map somewhere the alias
  index would not take it (upstream typos, e.g. KEV's `Catalyst SD-WAN Manger`)

`confidence` is `auto` (importer-proposed) or `curated` (human-ruled).

**Records are only written for exceptions.** The 556 pairs that already resolve
via an exact alias get no record. `resolutions.yaml` for `cisa_kev` will hold
roughly 150 entries.

New schema at `data/schema/resolutions.schema.json`, validated the same way as
the existing three. `data/schema/**` is already CODEOWNERS-gated; the new file
inherits that.

### 2. Importer promotion into `tools/`

`.gitignore:11` ignores `tmp/`, so `pull_cisa_kev.py`, `_lib.py`, and
`split_product_names` are untracked — the entire dataset is generated by
scripts that exist only on one machine. CI cannot run them, which blocks both
generation and drift detection below.

Move into the repo:

| From (untracked) | To |
|---|---|
| `tmp/scripts/_lib.py` | `tools/sources/_lib.py` |
| `tmp/scripts/pull_cisa_kev.py` | `tools/sources/pull_cisa_kev.py` |
| `tmp/scripts/pull_endoflife.py` | `tools/sources/pull_endoflife.py` |
| `tmp/scripts/pull_nvd_cpe.py` | `tools/sources/pull_nvd_cpe.py` |
| `tmp/scripts/test_*.py` | `tests/sources/` |

Stays untracked and local-only: `tmp/review-ui/` (a maintainer's own tool, no
auth, binds 127.0.0.1) and `tmp/cache/` (766 MB of upstream feeds).

The moved code must pass the gates the rest of `tools/` passes: `ruff`,
`mypy --strict`, `pytest`. The existing tests (`test_split_product_names.py`,
`test_cpe.py`, `test_purl.py`, `test_pull_nvd_cpe.py`, `test_write.py`) come
with it, so this is mostly a typing and lint pass rather than new test work.

### 3. Generation: the importer proposes

`pull_cisa_kev.py:108` already loops `for product_part in
split_product_names(product_name):` with the raw string and its parts both in
scope, and discards the raw one. Capture it and emit a resolution record with
`confidence: auto`.

**The importer must never overwrite a record whose `confidence` is `curated`.**
This is the load-bearing rule of the whole design. Commit `c41254096` wiped and
rebuilt `data/vendors/` from scratch; without this guard, the next such rebuild
destroys every human correction and the file becomes a treadmill.

### 4. Detection: which records need attention

The curation queue is **computed on every run and never stored**. The only
state is `resolutions.yaml` itself, so there is no separate "reviewed" ledger
that can drift. Each source pair falls into exactly one state:

| State | Rule | Queued? |
|---|---|---|
| `curated` | resolution exists, `confidence: curated` | no |
| `auto` | resolution exists, `confidence: auto` | yes, low priority |
| `exact` | no resolution; raw string matches exactly one product alias under the mapped vendor | no |
| `unresolved` | no resolution and no exact match | yes |
| `sentinel` | raw string is in the sentinel lexicon | yes — **overrides `exact`** |
| `orphaned` | resolution exists but a target file is gone | yes, top priority |

The `sentinel` override is required, not cosmetic. Measuring the states
naively returns zero sentinels, because `Multiple Products` *is* an exact alias
hit today — it resolves, to a phantom. Sentinels must be extracted by lexicon,
not by match failure.

**The lexicon is a prefix rule plus a short explicit list, never a suffix
regex.** Derived from the live catalog, `Multiple <anything>` is reliably
collective (9 distinct forms: `Multiple Products`, `Multiple Devices`,
`Multiple Routers`, `Multiple Chipsets`, `Multiple Firewalls`, `Multiple IP
Cameras`, `Multiple NAS Devices`, `Multiple Archer Devices`, `Multiple Vigor
Routers`), plus `MobileIron Multiple Products` and three one-offs (`Mobile
Devices`, `Wireless Access Point (WAP) Devices`, `DSL CPE Devices`). That is 14
distinct strings over 35 pairs.

A `Devices$` suffix regex is the obvious shortcut and it is wrong: `DCS-930L
Devices`, `JGS516PE Devices`, `DGN2200 Devices`, and `NVRmini2 Devices` are
specific model names that must keep resolving to real products. The lexicon
grows by curation, which is why unrecognised collectives surface through the
drift workflow rather than being auto-classified.

Collective-but-scoped strings (`Multiple Vigor Routers`) resolve to the vendor
with the phrase preserved in `note`. Mapping them onto the tag taxonomy is
deliberately out of scope — a consumer gets "DrayTek, unspecified routers",
which is honest, rather than an invented product set.

### 5. Prioritisation: which need *special* attention

150 records in alphabetical order buries the dangerous ones. Three signals were
tested over the 68 bare-`and` pairs; 31 flagged, and signal count tracks
correctness:

- **S1 modifier-suffix** — the part is a trailing token-run of another product
  name for the same vendor (`"Data Center"` ⊂ `"Crowd Data Center"`)
- **S2 KEV self-corroboration** — `left[0] + " " + right` appears as a split
  part in another KEV record for the same vendor
- **S3 token imbalance** — one side multi-token, the other a single short token

```
S1+S2+S3  Confluence Data Center and Server        → Confluence Server
S1+S2+S3  NetScaler ADC and Gateway                → NetScaler Gateway
S1+S2     Confluence Server and Data Center        → Confluence Data Center
S1+S3     Catalyst SD-WAN Controller and Manager   → Catalyst SD-WAN Manager
S1        Bitbucket Server and Data Center         → Bitbucket Data Center
S1        Jira Server and Data Center              → Jira Data Center
```

**Rank by S1 and S2. S3 is a tiebreaker, never a trigger** — alone it fires on
`Flash Player and AIR`, `Edge and Internet Explorer`, and `IOS and IOS XE`,
all correct splits.

A rejected approach, recorded so it is not retried: detecting a bad split by
checking whether the *repaired* name already exists as an alias flags nothing
at all. The repair does not exist precisely because the bug prevented its
creation. Evidence the bug destroyed cannot be queried.

The signals propose; they never auto-apply. Two classes of finding need a human
regardless: upstream typos (`Cisco / "Catalyst SD-WAN Manger"`) and true
duplicates that look like split bugs (`"IOS XE Software"` vs `"Cisco IOS XE
Software"`).

### 6. Curation: a second tab in the review UI

`tmp/review-ui/server.py` already implements the whole back half of the loop.
The additions are small:

| Exists | Add |
|---|---|
| `build_groups()` (:68) | `build_resolution_queue()` — states + signal ranking |
| `GET /api/groups` (:388) | `GET /api/resolutions` |
| `update_file()` (:134) | `POST /api/resolutions/save` — upsert one record by `key` |
| `save_yaml()` (:44) | extend — it assumes a dict; resolutions is a list |
| `run_validate()`, `rebuild_examples()`, `do_commit_pr()` | reused unchanged |

**Two tabs, not one merged queue.** The existing queue edits `data/vendors/**`,
where an alias must be unique per vendor; the new one edits `data/sources/**`,
where a key deliberately fans out to many. One save path cannot enforce both
invariants without the write-protection rule becoming hard to reason about.

**The path guard must widen.** `resolve()` at `server.py:52` rejects any path
outside `data/vendors/`. It is the tool's only path-escape defence, so it is
widened deliberately and narrowly to also permit `data/sources/` — not replaced
with a looser check.

`POST /api/resolutions/save` always writes `confidence: curated`. That is what
makes a human decision survive the next rebuild.

One reviewer action, end to end:

```
queue:    Atlassian / "Confluence Data Center and Server"   [S1+S2+S3]
          naive:    ['Confluence Data Center', 'Server']
          proposed: ['Confluence Data Center', 'Confluence Server']
accept →  upsert record, confidence: curated
       →  run_validate() → rebuild_examples() → do_commit_pr()
```

### 7. Validation

Two checks added to `tools/validate.py`, both merge gates:

- **Schema conformance** for `data/sources/**/resolutions.yaml`, mirroring
  `validate_schema_conformance`.
- **Referential integrity** — every `{vendor_id, product_id}` target resolves
  to a real file. Same shape as the existing `validate_vendor_references`.
  This is what turns `orphaned` from silent rot into a failed build.

A third check guards the key space: no two records in one source file may share
a `key`.

### 8. Drift detection

New scheduled workflow, `.github/workflows/source-drift.yml`, daily:

1. Fetch the KEV catalog.
2. Diff its `(vendorProject, product)` key set against `resolutions.yaml` keys
   plus the exact-alias index.
3. If anything is unresolved, open (or update) a PR adding `confidence: auto`
   records for the new keys, with the S1/S2 ranking in the PR body.

This is why the importers must be tracked — CI cannot run code that lives only
in a gitignored directory. The workflow needs `contents: write` and
`pull-requests: write`; it fetches an external feed, so it runs on `main` only
and opens a PR rather than committing directly.

### 9. Published API surface

Static files only — no query parameters, so every key lives in a path. Emitted
by `tools/build_index.py` into the `dist/` tree `publish-index.yml` already
deploys.

**Bundle (primary).** `api/v1/sources/cisa_kev.json` — all 706 pairs, roughly
140 KB. This matches actual consumption: nobody resolves one KEV record in
isolation, they process the feed. One conditional GET, resolve in memory.

**Per-key (convenience).** `api/v1/sources/cisa_kev/<sha256-16>.json` for point
lookups without pulling the bundle, keyed on a hash of the normalised
`(vendor, product)` pair. Slugs are rejected because they collide — `"iOS and
iPadOS"` and `"iOS, iPadOS"` slugify identically, and a wrong answer is worse
than an opaque URL. 706 files; `index/entries/` already ships ~29k, so the
scale is proven.

Three response shapes:

```jsonc
// fan-out
{"key": {"vendor": "Apple", "product": "iOS, iPadOS, and watchOS"},
 "resolution": {"kind": "split", "confidence": "curated",
   "targets": [{"vendor_id": "apple", "product_id": "ios",     "name": "iOS"},
               {"vendor_id": "apple", "product_id": "ipados",  "name": "iPadOS"},
               {"vendor_id": "apple", "product_id": "watchos", "name": "watchOS"}]}}

// sentinel — product_id absent, and said explicitly so no consumer invents one
{"key": {"vendor": "Apple", "product": "Multiple Products"},
 "resolution": {"kind": "sentinel", "confidence": "curated",
   "product_resolved": false,
   "targets": [{"vendor_id": "apple", "name": "Apple"}],
   "note": "KEV names no specific product"}}

// miss
{"key": {"vendor": "Acme", "product": "Widget"},
 "resolution": null, "candidates_are_unverified": true,
 "candidates": [{"vendor_id": "atlassian", "product_id": "confluence", "score": 91}]}
```

**Fuzzy matching against unseen input.** Neither mechanism is a live matcher:

- Keys present in the feed but not yet curated ship *with* precomputed
  candidates in the bundle, scored at build time by the existing `rapidfuzz`
  logic in `tools/suggest_match.py`. A new KEV entry therefore carries
  candidates from the next publish, and publish runs on every merge to `main`.
- For input originating outside the feed, the bundle doubles as the corpus:
  the client scores locally against aliases it has already downloaded. A short
  reference implementation ships in the docs.

**Supporting files.**

- `api/v1/manifest.json` — per-source counts, content hash, `generated_at`,
  commit sha. Lets a consumer detect "nothing changed" in one small request.
- `api/v1/tombstones.json` — every deleted phantom slug mapped to its
  replacement targets, so anyone already resolving to `apple/multiple-products`
  or `d-link-and-trendnet` gets a redirect rather than a silent 404.

Versioning is by path (`api/v1/`). A breaking change to the envelope means
`api/v2/` published alongside, not an edit in place.

## Data flow

```
CISA KEV feed
      │
      ▼
tools/sources/pull_cisa_kev.py ──► data/vendors/**          (aliases, as today)
      │                        └─► data/sources/cisa_kev/
      │                              resolutions.yaml        (confidence: auto)
      │                              ▲
      │                              │ never overwrites confidence: curated
      ▼                              │
review UI, resolutions tab ──────────┘  (writes confidence: curated)
      │
      ▼
tools/validate.py  (schema + referential integrity + key uniqueness)
      │
      ▼
tools/build_index.py ──► dist/index/**   (as today)
                     └─► dist/api/v1/**  (bundle, per-key, manifest, tombstones)
      │
      ▼
publish-index.yml ──► gh-pages
```

## Testing

- **Unit** — state classification (all six states, including the `sentinel`
  override of `exact`); S1/S2/S3 signals against the known-good and known-bad
  examples in this spec; hash key derivation, including the `"iOS and iPadOS"`
  / `"iOS, iPadOS"` collision case that motivated hashing.
- **Unit** — importer write-protection: a `curated` record survives a full
  re-import. This is the rule most worth a regression test, since the failure
  is silent and only visible after a rebuild.
- **Validation** — referential integrity fails on a target pointing at a
  deleted product; duplicate keys in one source file fail.
- **Integration** — `build_index.py` over a fixture tree produces all three
  response shapes; every `targets` entry in the bundle resolves against
  `index/entries/`.
- **Existing suite** — `ruff`, `mypy --strict tools`, `pytest`, and
  `tools/validate.py` must stay green, including the `data/examples/` freshness
  check in `validate.yml`, which will need regenerating.

## Risks and open questions

- **Phantom removal is a breaking change.** Deleting `apple/multiple-products`
  and friends changes the public id set. `tombstones.json` is the mitigation,
  but it only helps consumers who read it. Worth a note in the README and a
  minor version marker on the published index.
- **`d-link-and-trendnet` removal is unblocked.** Both `data/vendors/d-link/`
  and `data/vendors/trendnet/` already exist, so the phantom vendor can be
  deleted and its single record (CVE-2015-1187) redirected without creating
  anything first.

- **The sentinel lexicon is curated and will lag.** A new collective phrasing
  KEV invents lands as `unresolved`, not `sentinel`, and the reviewer must
  recognise it. This is intentional — the alternative is a suffix rule that
  silently swallows real model names — but it means the lexicon needs an owner.
- **The 22 unmatched single names are a mixed bag.** Some are upstream typos
  (`Catalyst SD-WAN Manger`), some are genuine gaps (`Apache / Struts 1`).
  They share a queue but not a fix; the `exact` resolution kind covers typos
  while genuine gaps need a normal vendor/product PR instead.
- **Scope of the importer promotion.** Moving four scripts into `tools/` under
  `mypy --strict` may turn out larger than it looks. If it does, it should be
  split out and landed first, since everything else depends on it.

## Sequencing

1. Promote importers into `tools/` (unblocks CI; independently useful).
2. Schema, resolution layer, validation checks — data model with no consumers.
3. Importer emits `auto` records; write-protection rule and its test.
4. Review UI resolutions tab; curate the ~150-record backlog.
5. Phantom removal plus `tombstones.json`.
6. `build_index.py` API surface and drift workflow.

Steps 1–3 are safe to land without any consumer-visible change. The break comes
at step 5, after the backlog is curated.
