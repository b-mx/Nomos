# Source resolution layer + read-only resolution API

Status: draft, revised after design review, pending user review
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

This project adds four capabilities:

1. **A tracked source-key snapshot** — the authoritative inventory of every
   `(vendorProject, product)` pair, committed so builds are reproducible and
   offline.
2. **A resolution layer** — a per-source record mapping a verbatim source key
   to the canonical ids it denotes, including fan-out and explicit sentinels.
3. **A curation workflow** — detection of source records not yet curated,
   ranked by likelihood of being wrong, plus tooling to record a human decision
   once so it *governs* subsequent imports rather than merely surviving them.
4. **A read-only API** — snapshot, resolution layer, and alias index published
   as static JSON on the existing GitHub Pages deployment, no server to run.

## Non-goals

- **A live matching service.** No compute endpoint, no Worker, no function.
  Every artifact is a static file built by `tools/build_index.py` and deployed
  by `publish-index.yml`.
- **Fixing the splitter.** `split_product_names` stays as it is. Its
  shared-prefix/suffix limitation is documented and accepted; the resolution
  layer exists precisely so a human can override a bad split permanently.
- **Per-version resolution.** A resolution target is a vendor or a product,
  never a version range.
- **Generalising the key model to all sources.** v1 is CISA KEV only. See D11.
- **Supporting a from-empty rebuild of `data/vendors/`.** See D5; this is now
  an explicit non-goal, and it is a change in policy.

## Resolved decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | `data/sources/cisa_kev/keys.json`, a tracked generated snapshot, is the sole authoritative key inventory. The builder never fetches. | Publishing must work offline and be a pure function of tracked files. Only the drift workflow touches the network. |
| D2 | The bundle and per-key files cover **all** snapshot keys, unresolved ones included. Unknown hashes return HTTP 404. Outside-feed input is client-side only. | A static host cannot synthesise a miss body for an arbitrary path. Making "known-but-unresolved" a real published object keeps the useful half. |
| D3 | Hash = first **16 hex chars** of sha256 over **length-prefixed** NFC parts, whitespace-collapsed, **case preserved**. Two collision checks: normalisation (in the snapshot) and hash (at build). | Case-folding would merge `IOS Software` and `IOS software`, which are distinct upstream keys. Length-prefixing removes any assumption about which bytes can appear in feed text. |
| D4 | Exact resolution uses `cisa_kev` aliases only, never canonical `name`s, never other sources. New `validate_alias_uniqueness_normalized` makes the "0 or 1" guarantee real. | Names are display strings; a cosmetic rename must not change the API. The existing check compares raw strings, so normalised lookup was not actually guaranteed unique. |
| D5 | Curated resolutions are **input** to the importer, consumed before splitting. Full rebuild from empty is unsupported. **Confirmed by the user.** | Protecting the YAML does not stop a rebuild from recreating `atlassian/server`. Target names alone would not suffice either — type, tags, CPE, PURL, services, and aliases are all curation. |
| D6 | Unresolved keys are **never persisted**. They are derived. `targets` is always non-empty. Kind `deferred` (renamed from `unmappable`) records a reviewed decision to wait. | No KEV key is unmappable in principle, so the original framing had no example. The real gap is suppressing endless re-review of a key someone chose not to map yet. |
| D7 | Three orthogonal axes: semantic `kind` (in file), review state (in file), resolution state (derived). | The old single table was not mutually exclusive — a curated record can also be orphaned. |
| D8 | Promote the review UI to tracked `tools/review_ui/`. "Reused unchanged" is retracted; four functions need real changes. | A deliverable that lives only on one laptop cannot be reviewed or reproduced. |
| D9 | Full envelope specified; targets use `vendor_name`/`product_name`; JSON Schemas published and tested. Bundle/per-key entries are **deep-equal**, not byte-identical. No `commit` or `generated_at` in any hashed file. | `name` was overloaded across vendor and product targets. An entry nested at two depths cannot be byte-identical. Commit metadata in hashed files made every unrelated commit look like a data change. |
| D10 | Candidates are dimension-scoped and never cross-vendor. Corpus is `cisa_kev` aliases **plus canonical names**; one max score per target; threshold on the float, published score `int(round(x))`. | Mirrors the per-vendor alias-uniqueness invariant. Candidates are advisory and human-checked, so they may use names that exact resolution must not. |
| D11 | The schema is source-discriminated, and **v1 defines `cisa_kev` only**. | NVD (`part,vendor,product`), OSV (`ecosystem,package`), and endoflife (`product`) have different key dimensions. One shape for all is the same error this spec exists to fix. |
| D12 | Authored in tracked **`data/tombstones.yaml`** (canonical/global, CODEOWNERS-gated), keyed by entry slug, dated with **`removed_on`**, permanent, non-chaining. "Redirect" retracted — advisory only; the old entry 404s. | The spec previously defined only the generated file and never said where tombstones live. A commit SHA in a tracked record is circular; a date is stable under rebase and squash. |
| D13 | Drift is bidirectional; upstream removals mark records `stale`, never auto-delete. Branch safety by **commit ownership + `--force-with-lease`**, not by path. `GITHUB_TOKEN` plus a `workflow_dispatch` of `validate.yml`. **Confirmed by the user.** | Git cannot force-push a single path, and a path-based check would still discard a human edit to `keys.json`. If unattended checks are needed later, a scoped GitHub App token — not a PAT. |
| D14 | `--api-output-dir` alongside `--output-dir`; output dirs cleaned before write; `generated_at` **and `commit`** live only in the manifest. | Any volatile metadata inside a hashed file makes every unrelated commit look like a data change, defeating the manifest's purpose. |

No open questions remain. D5 and D13 were confirmed by the user; every other
decision is settled in the sections below.

## Background: measured scale

Against the live KEV catalog (1,682 records) and the current `data/vendors/`
tree:

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
catalog; it grows with the catalog's irregularity.

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
data/vendors/trend-micro/products/officescan-and-worry-free-business-security-agents.yaml
data/vendors/d-link-and-trendnet/vendor.yaml                (a vendor that does not exist)
```

"Data Center" is not an Atlassian product; it is half of "Confluence Data
Center". The sentinel side adds **34 collective-name products**, from
`apple/multiple-products` to `zyxel/multiple-firewalls` and
`samsung/mobile-devices`.

**The phantom list is determined by review, not by rule.** The splitter is not
always wrong — `citrix/gateway`, `microsoft/wordpad`, `adobe/air`, and
`oracle/jre` are legitimate products it happened to get right. A glob for
`multiple-*.yaml` returns 35 files, of which
`themeisle/products/multiple-page-generator.yaml` is a genuine WordPress
plugin. Every removal is a per-case decision; no heuristic gets a vote.

## Design

### D1. The source-key snapshot

**Problem.** `resolutions.yaml` holds only ~150 exceptions, but the bundle must
cover all 706 pairs. CI has no `tmp/cache/cisa-kev.json`, and aliases in
`data/vendors/**` do not preserve the original pairing — a product alias records
the split part, not the record it came from.

**Decision.** A tracked, generated snapshot is the authoritative inventory:

`data/sources/cisa_kev/keys.json`

```jsonc
{
  "source": "cisa_kev",
  "upstream": {
    "url": "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
    "catalog_version": "2026.08.28",
    "date_released": "2026-08-28T14:00:00.000Z"
  },
  "fetched_at": "2026-08-29T03:11:07Z",
  "key_count": 706,
  "keys": [
    {"vendor": "Apple", "product": "Multiple Products"},
    {"vendor": "Apple", "product": "iOS, iPadOS, and watchOS"}
  ]
}
```

Roughly 45 KB for 706 keys. Sorted by `(vendor, product)` under the
normalisation of D3. Generated only — never hand-edited.

**Consequences, stated explicitly:**

- `build_index.py` reads `keys.json` and **never performs network I/O**.
  Publishing succeeds with no network access.
- Build output is a pure function of tracked files: `keys.json`,
  `resolutions.yaml`, `data/vendors/**`, `data/taxonomy/`. Same commit → same
  bytes (see D14 on `generated_at`).
- When upstream changes, nothing moves until the drift workflow opens a PR
  updating `keys.json`. Upstream drift is therefore reviewable, and a bad feed
  day cannot corrupt a publish.
- `fetched_at` and `catalog_version` are provenance for consumers; they are not
  inputs to any hash.

### D2. Static hosting and the "miss" response

**Problem.** A static directory cannot return a `{"resolution": null}` body for
an arbitrary unseen hash. GitHub Pages returns 404.

**Decision.** Three distinct cases, none of which requires computation:

| Input | Behaviour |
|---|---|
| Key in the snapshot, resolvable | 200, resolution object |
| Key in the snapshot, **not** resolvable | 200, `"resolution": null` plus candidates — a real published file |
| Hash not in the snapshot | **HTTP 404.** Documented contract: 404 means "not a key in this source snapshot" |
| String from outside the feed | Not a server operation. Client-side function against the published corpus (D10) |

A per-key file is emitted for **every one of the 706 snapshot keys**, including
the ~150 unresolved. So the miss shape is genuinely served — for known keys.
The earlier draft implied it was served for anything, which was wrong.

Consumers are told plainly: treat 404 as "unknown key", not as "no mapping".

### D3. Normalisation and hash derivation

Applied identically at build time, in validation, and in the reference client.

**Normalisation** (`normalize_key_part`):

1. Unicode **NFC**.
2. Strip leading/trailing whitespace (Unicode-aware `str.strip()`).
3. Collapse internal whitespace runs (`\s+`) to a single U+0020.
4. **Case is preserved.** No casefold, no lower.

**Serialisation and hash — length-prefixed, no delimiter assumption:**

```python
vb = normalize(vendor).encode("utf-8")
pb = normalize(product).encode("utf-8")
raw = b"%d:%s%d:%s" % (len(vb), vb, len(pb), pb)   # netstring-style
key_hash = hashlib.sha256(raw).hexdigest()[:16]
```

- **Length prefixes, not a delimiter byte.** An earlier draft used U+001F and
  argued it "cannot appear in feed text". That is an assumption about upstream
  data, and the hash must not rest on one. Length-prefixing is unambiguous for
  *any* byte content: `("ab", "c")` → `430fb1b4ac43316e` and `("a", "bc")` →
  `5310a58788781ab2`.
- Encoding is UTF-8 throughout.
- **`sha256-16` means 16 lowercase hexadecimal characters** — the first 8 bytes,
  64 bits. The earlier draft was ambiguous; the field is renamed `key_hash` and
  the term `sha256-16` is dropped.
- Normalisation additionally **rejects C0/C1 control characters** in either key
  part. They have no legitimate place in a vendor or product name, and
  rejecting them at snapshot-generation time keeps malformed upstream data out
  of the key space. This is belt-and-braces: correctness no longer depends on
  it.

**Case preservation is load-bearing.** These are distinct upstream keys:

```
d5d67a5538ae09b9   Cisco / 'IOS Software'
75bedee2535bf5c1   Cisco / 'IOS software'
```

**Fixed test vectors** (verified against the implementation above):

| vendor | product | key_hash |
|---|---|---|
| `Apple` | `iOS, iPadOS, and watchOS` | `404574ff5888b597` |
| `Apple` | `Multiple Products` | `304d5ecebbbdc942` |
| `D-Link and TRENDnet` | `Multiple Devices` | `a3ecd0d9b695140d` |
| `Cisco` | `IOS Software` | `67699a06d172bb2f` |
| `Cisco` | `IOS software` | `d1cc7d8fcdf44975` |
| `␠␠Apple␠␠` | `iOS,\tiPadOS,␠␠and␠␠␠watchOS` | `404574ff5888b597` |

Row 6 pins whitespace normalisation: it must equal row 1. Rows 4 and 5 pin
case preservation: they must differ.

**Two distinct collision checks**, at different layers:

1. **Normalisation collision, in `keys.json` generation and in validation.**
   Two *distinct verbatim* keys that normalise to the same normalised key are
   rejected — the snapshot may not contain both. This is the stronger check and
   the one that matters: it catches `"Apple "` and `"Apple"` before they ever
   reach a hash.
2. **Hash collision, in `build_index.py`.** Two *distinct normalised* keys
   sharing a truncated hash fail the build. At 706 keys the probability is
   ~1.4e-14; the check exists because a silent collision would serve one
   vendor's data under another's URL.

The earlier draft described only the second, which would have let a
normalisation collision through as a single silently-merged key.

**Correction to the earlier draft.** It claimed `"iOS and iPadOS"` and
`"iOS, iPadOS"` slugify identically. They do not — the current `slugify()`
yields `ios-and-ipados` and `ios-ipados`. The real collisions are case-only:

```
'IOS Software'  and 'IOS software'   -> cisco--ios-software
'Drupal Core'   and 'Drupal core'    -> drupal--drupal-core
'N-Central'     and 'N-central'      -> n-able--n-central
```

Three real collisions in the current feed. Slugs are rejected for keying
because they are lossy by construction (case-folding plus punctuation
collapse), and new collisions can appear at any time without warning.

### D4. The exact-resolution algorithm

For a snapshot key with no resolution record, resolution is a two-step lookup.
Both steps use the D3 normalisation and are otherwise **exact** — no fuzzy, no
substring, no case-insensitivity.

**A prerequisite validation gap, closed.** The existing
`validate_alias_uniqueness` compares **raw** `(source, value)` strings, while
this algorithm looks aliases up **normalised**. Two `cisa_kev` aliases
differing only by whitespace or Unicode composition would pass validation and
then produce two normalised matches — so the "0 or 1" claim below is not
currently guaranteed. A new check, `validate_alias_uniqueness_normalized`,
enforces uniqueness of `(source, normalize(value))` for vendors globally and
`(vendor_id, source, normalize(value))` for products, restricted to
API-eligible sources (`cisa_kev` in v1).

Verified against the current tree: **zero** normalised collisions exist today,
for vendors or products. The check can therefore land without a data migration.

1. **Vendor.** Look up the normalised `vendor` among vendor aliases where
   `source == "cisa_kev"`. With the check above, vendor aliases are globally
   unique under normalisation, so this yields 0 or 1.
   - 0 → the key is `unresolved` (vendor-unresolved). Stop.
   - The vendor string having its own fan-out record is handled by that record;
     such a key can never be exact-resolved.
2. **Product.** Within that vendor only, look up the normalised `product` among
   product aliases where `source == "cisa_kev"`. Product aliases are unique per
   vendor under normalisation, so this yields 0 or 1.
   - 0 → `unresolved` (product-unresolved). Stop.

**Eligibility rules, stated so they are testable:**

- Only `source: cisa_kev` aliases are eligible. An `nvd` or `osv` alias whose
  value happens to equal a KEV product string is coincidence, not evidence.
- Canonical `name` fields are **not** eligible. Names are display strings; if
  they resolved, renaming `Confluence` to `Atlassian Confluence` would silently
  change the API.
- Zero matches → `unresolved`, never a guess.
- Multiple matches are impossible given validation. If encountered,
  `build_index.py` **fails the build** rather than picking one.

**Confidence composition.** A synthesized exact resolution inherits the
*weaker* of the two aliases, ordering `curated > auto`:

```
resolution.confidence = min(vendor_alias.confidence, product_alias.confidence)
```

A resolution is only as trustworthy as its weakest link. `provenance`
distinguishes how it was produced:

- `"alias-index"` — synthesized by this algorithm, no record exists
- `"resolution"` — read from `resolutions.yaml`

Synthesized exact resolutions always have `kind: "exact"`.

### D5. Curated resolutions govern the importer

**Problem.** "Never overwrite curated records" preserves YAML but does not stop
a rebuild from recreating `atlassian/server`. The protection was on the wrong
artifact.

**Decision.** `resolutions.yaml` is read **before** splitting and is
authoritative over it. Per snapshot key, the importer:

1. Loads curated records first.
2. **If a curated record exists → does not call `split_product_names` at all**,
   and creates nothing from that key. This is what prevents phantom recreation.
3. Verifies every curated target exists. A missing target is reported as an
   error and the importer **does not create it** — see below.
4. If no curated record exists, behaves as today (split, propose, create), and
   may freely overwrite any existing `auto` record.

**Curated resolutions do not create aliases.** The record *is* the mapping.
Adding a synthetic alias such as `"Confluence Server"` to `atlassian/confluence`
would assert a source string that key never carried, and would reintroduce the
fan-out-versus-uniqueness conflict the layer exists to avoid. Aliases remain
strictly for 1:1 exact matches.

**Missing curated targets are a curation bug, not an import task.** The
importer reports; `validate.py` fails (referential integrity); the fix is to
create the product through the normal vendor/product flow. The review UI
enforces the ordering by offering only existing products as targets, with an
explicit "create product" action that writes the product file in the same
change.

**Partial mappings.** If some targets exist and some do not, nothing is
created and the whole record is reported. No half-applied state.

**Full rebuild from empty is unsupported — a policy change.** Commit
`c41254096` wiped and rebuilt `data/vendors/` from scratch. That was correct
bootstrapping and is now destructive: once 150 resolution judgments plus tag,
type, icon, and CPE curation exist, the tree is no longer a pure function of
upstream feeds. That is the entire point of Nomos. The importers become
**incremental only** — they propose additions to an existing tree. A
`--bootstrap` flag may exist for disaster recovery but is not part of any
routine workflow and is not exercised by CI.

**Regression test (required).** Seed a fixture tree containing
`atlassian/confluence` and `atlassian/confluence-data-center`, plus a curated
record for `Atlassian / "Confluence Data Center and Server"`. Run the importer
over a KEV fixture containing that pair. Assert:

- `data/vendors/atlassian/products/server.yaml` **is not created**
- no product named `Server` exists under `atlassian`
- the curated record is byte-identical afterwards

Asserting only that the YAML record survived would pass while the phantom was
recreated, which is exactly the failure this test exists to catch.

### D6. Unresolved, proposed, curated

**Problem.** The drift workflow promised `confidence: auto` records for
unresolved keys, but the record shape requires real targets and referential
integrity rejects missing ones.

**Decision.** `targets` is **always non-empty** (`minItems: 1`). Unresolved
keys are never persisted — they are derived at queue and build time as:

```
unresolved = snapshot_keys − resolution_keys − exact_resolvable_keys
```

State transitions:

| From | To | Trigger |
|---|---|---|
| *unresolved* (derived, no record) | `auto` record | importer split succeeded **and** every part resolves to an existing product |
| *unresolved* | `curated` record | human decision in the review UI |
| `auto` record | `auto` record | importer re-run; overwritten freely |
| `auto` record | `curated` record | human accepts or edits |
| `curated` record | `curated` record | only a human may change it |

The drift workflow therefore **updates `keys.json` only** and lists unresolved
keys in the PR body. It writes no resolution records, because for an
unresolvable key it has no valid targets to write.

**New kind `deferred`** (renamed from the earlier `unmappable`). It records
"reviewed; deliberately not mapped yet, and here is what would change that".
Distinct from `sentinel`, which means "the source itself names no product". It
carries exactly one vendor-only target and a required `note`, so referential
integrity holds.

The earlier draft illustrated this with `Cisco / "Catalyst SD-WAN Manger"`,
which was wrong — that key is a plain upstream typo with an obvious correct
target, and D9 rightly maps it as a curated `exact` override. It cannot be
both.

Reviewing all 22 unmatched single-name keys, **none is unmappable in
principle**; every one has a defensible target. So `unmappable` as originally
framed had no justifying example. But removing the kind outright reopens a real
gap: a reviewer who concludes "not now" has nowhere to record it, and the key
resurfaces in the queue on every drift run forever. `deferred` closes that gap
honestly — it asserts a *decision to wait*, not an impossibility:

```yaml
- key: {vendor: "Google", product: "Chromium WebP"}
  kind: deferred
  confidence: curated
  targets: [{vendor_id: google}]
  note: >-
    Names the bundled WebP codec inside Chromium, not a distinct Google
    product. Blocked on a modeling decision: whether Nomos gives bundled
    third-party libraries their own entries (libwebp is upstream
    webmproject, not Google). Revisit when that policy is settled.
```

`deferred` records are hidden from the main queue and listed under a separate
low-priority filter, so a decision to wait is visible without being noise.

### D7. Three orthogonal axes

The earlier single state table was not mutually exclusive. Replaced by:

**Axis 1 — semantic kind** (stored, describes the *product* dimension only):

- `exact` — the product string denotes exactly one product
- `split` — it denotes several products
- `sentinel` — the source names no product (`Multiple Products`)
- `deferred` — reviewed; deliberately not mapped yet, with a stated reason

Vendor fan-out is **not** a kind. It is implicit in `targets` carrying more than
one distinct `vendor_id`. Without this rule the D-Link/TRENDnet record is
ambiguous — vendor `split` and product `sentinel` at once — and two curators
would classify it differently. It is a `sentinel`, because that is what its
product field is.

**Axis 2 — review state** (stored): `auto` | `curated`.

**Axis 3 — resolution state** (derived, never stored):

- `resolved-exact` — no record; the D4 algorithm succeeds
- `resolved-record` — a record exists and all targets resolve
- `unresolved` — no record and D4 fails
- `orphaned` — a record exists but ≥1 target is missing

**Queue membership and priority** (first match wins):

| Priority | Condition |
|---|---|
| 1 | `orphaned` |
| 2 | `unresolved` **and** matches the sentinel lexicon |
| 3 | `unresolved`, ranked by the S1/S2 signals below |
| 4 | `resolved-record` **and** review state `auto` |
| — hidden — | `resolved-exact`, or `resolved-record` + `curated` |

The sentinel lexicon is a **priority hint**, not a state. A collective string
that already has a curated record is satisfied and hidden.

**Orphans versus a failing repository.** Referential integrity makes a
committed orphan a CI failure, so the review UI must not depend on validation
passing:

- `build_resolution_queue()` computes orphan status itself and **never invokes
  `validate.py`**.
- The normal case is an orphan created in the working tree (a product deleted
  before its resolution was updated); it is fixed and committed together, and
  validation passes at commit time.
- A *committed* orphan means `main` is already broken. The UI displays it
  read-only with that explanation and does not offer a one-click commit, since
  the repair belongs in a normal reviewed PR.

### D8. The review UI becomes a tracked deliverable

**Decision.** Promote `tmp/review-ui/` → `tools/review_ui/` (tracked). It
remains local-only, binds 127.0.0.1, has no auth, and is excluded from the
published site. Being tracked, it is subject to `ruff`, `mypy --strict`, and
`pytest` like the rest of `tools/`.

Rationale: the curation workflow is load-bearing for this spec. A component
that exists only on one machine cannot be reviewed, shipped, or reproduced by a
second maintainer.

**"Reused unchanged" is retracted.** Verified against the current source, four
functions need real changes:

| Function | Current behaviour | Required change |
|---|---|---|
| `git_status_for_vendors()` (:195) | `git status -- data/vendors` only | Generalise to `git_status_for_paths(paths)` over `data/vendors`, `data/sources`, `data/tombstones.yaml`, `data/examples` |
| `do_commit_pr()` (:295) | `git add data/vendors data/examples`; `git commit -- data/vendors data/examples`; raises "nothing to commit under data/vendors/" | Widen to the same path list, so a resolutions-only or tombstone-only change commits; must stage deletions; error text corrected |
| `rebuild_examples()` (:280) | `build_index.py --output-dir data/examples` into an existing directory; never deletes | Clean the output tree first, else deleted phantom entries leave stale `entries/*.json` that CI's `diff -r` will flag |
| `resolve()` (:52) | `str(path).startswith(str(VENDORS_DIR))` | Replace with `Path.is_relative_to()` against an explicit allowlist of writable roots |

The `resolve()` change is a real defect fix, not a widening. `str.startswith`
admits a sibling-prefix escape — `data/vendors-evil/` passes the current check.
The replacement:

```python
WRITABLE_ROOTS = (
    REPO_ROOT / "data" / "vendors",
    REPO_ROOT / "data" / "sources",
)

def resolve(rel_path: str) -> Path:
    path = (REPO_ROOT / rel_path).resolve()
    if not any(path.is_relative_to(root.resolve()) for root in WRITABLE_ROOTS):
        raise ValueError("path outside the writable allowlist")
    return path
```

**Two tabs, not one queue.** The vendor tab edits `data/vendors/**`, where an
alias must be unique per vendor; the resolutions tab edits `data/sources/**`,
where a key deliberately fans out. One save path cannot enforce both
invariants clearly.

Additions: `build_resolution_queue()`, `GET /api/resolutions`,
`POST /api/resolutions/save` (always writes `confidence: curated`), and a
list-aware `save_yaml`.

### D9. The API contract

All files under `dist/api/v1/`. Ordering, field presence, and schemas are
normative.

**Bundle envelope** — `api/v1/sources/cisa_kev.json`:

```jsonc
{
  "schema_version": "1.0",
  "source": "cisa_kev",
  "upstream": {"catalog_version": "2026.08.28", "date_released": "..."},
  "matching": {
    "normalization": "nfc+strip+collapse-ws, case-preserved",
    "scorer": "rapidfuzz.fuzz.token_sort_ratio",
    "scorer_normalization": "nfc+strip+collapse-ws+casefold",
    "threshold": 70,
    "max_candidates": 5,
    "rapidfuzz_version": "3.14.5"
  },
  "counts": {"keys": 706, "resolved_exact": 556, "resolved_record": 150,
             "unresolved": 0, "curated": 150, "auto": 0, "orphaned": 0},
  "entries": [ /* one entry object per snapshot key, ordered by (vendor, product) */ ]
}
```

**No deployment metadata in hashed files.** The bundle carries neither
`generated_at` nor `commit`. Both live only in `manifest.json` (D14). An
earlier draft put `commit` in the bundle, every per-key file, and
`tombstones.json` — which meant an unrelated README commit changed every API
file and every content hash while no mapping data had changed, defeating the
manifest's entire purpose. Hashed artifacts are now a pure function of tracked
*data*; deployment provenance is separated into the one file that is not
hashed.

**Per-key file** — `api/v1/sources/cisa_kev/<key_hash>.json`:

```jsonc
{"schema_version": "1.0", "source": "cisa_kev",
 "entry": { /* deep-equal to the same entry object in the bundle */ }}
```

**Structural, not byte, identity.** The entry appears at different nesting
depths in the two files, so their serialised bytes differ by indentation. The
requirement is **deep equality of the parsed objects**:

```python
json.load(bundle)["entries"][i] == json.load(per_key)["entry"]
```

Guaranteed by construction — the builder creates one dict and serialises it
into both — and asserted by test over every key. The earlier draft said
"byte-identical", which is not achievable without injecting a pre-serialised
byte sequence, and that complexity buys nothing.

**Entry object rules:**

- `key_hash` and `key` always present.
- `resolution` is an object or `null`.
- When `resolution` is an object, `kind`, `confidence`, `provenance`,
  `product_resolved`, and `targets` are **always present**. `product_resolved`
  is `true` iff every target carries a `product_id`.
- `note` present only when the record has one.
- Targets use `vendor_name` and `product_name` — never a bare `name`.
- Target ordering: sorted by `(vendor_id, product_id or "")`; vendor-only
  targets sort before product targets of the same vendor.
- Targets are unique on `(vendor_id, product_id)`.
- `candidates` present only when `resolution` is `null`.

**Seven response examples.**

```jsonc
// 1. Synthesized exact match (no record; D4 algorithm)
{"key_hash": "…", "key": {"vendor": "Mozilla", "product": "Firefox"},
 "resolution": {"kind": "exact", "confidence": "auto", "provenance": "alias-index",
   "product_resolved": true,
   "targets": [{"vendor_id": "mozilla", "vendor_name": "Mozilla",
                "product_id": "firefox", "product_name": "Firefox"}]}}

// 2. Curated exact override (upstream typo mapped by hand)
{"key_hash": "…", "key": {"vendor": "Cisco", "product": "Catalyst SD-WAN Manger"},
 "resolution": {"kind": "exact", "confidence": "curated", "provenance": "resolution",
   "product_resolved": true,
   "targets": [{"vendor_id": "cisco", "vendor_name": "Cisco",
                "product_id": "catalyst-sd-wan-manager",
                "product_name": "Catalyst SD-WAN Manager"}],
   "note": "Upstream typo: 'Manger'"}}

// 3. Auto split proposal (importer split cleanly; not yet reviewed)
{"key_hash": "13e9e0c3b56cb69d", "key": {"vendor": "Apple", "product": "iOS, iPadOS, and watchOS"},
 "resolution": {"kind": "split", "confidence": "auto", "provenance": "resolution",
   "product_resolved": true,
   "targets": [{"vendor_id": "apple", "vendor_name": "Apple", "product_id": "ios",     "product_name": "iOS"},
               {"vendor_id": "apple", "vendor_name": "Apple", "product_id": "ipados",  "product_name": "iPadOS"},
               {"vendor_id": "apple", "vendor_name": "Apple", "product_id": "watchos", "product_name": "watchOS"}]}}

// 4. Curated split (splitter was wrong; corrected by hand)
{"key_hash": "…", "key": {"vendor": "Atlassian", "product": "Confluence Data Center and Server"},
 "resolution": {"kind": "split", "confidence": "curated", "provenance": "resolution",
   "product_resolved": true,
   "targets": [{"vendor_id": "atlassian", "vendor_name": "Atlassian",
                "product_id": "confluence", "product_name": "Confluence"},
               {"vendor_id": "atlassian", "vendor_name": "Atlassian",
                "product_id": "confluence-data-center", "product_name": "Confluence Data Center"}]}}

// 5. Sentinel — vendor fan-out with no product on either side
{"key_hash": "3c1f352c5f5b84a2", "key": {"vendor": "D-Link and TRENDnet", "product": "Multiple Devices"},
 "resolution": {"kind": "sentinel", "confidence": "curated", "provenance": "resolution",
   "product_resolved": false,
   "targets": [{"vendor_id": "d-link",   "vendor_name": "D-Link"},
               {"vendor_id": "trendnet", "vendor_name": "Trendnet"}],
   "note": "KEV names two vendors and no specific device"}}
// vendor_name is the canonical display name from vendor.yaml ("Trendnet"),
// deliberately not the upstream spelling in key.vendor ("TRENDnet").

// 6. Known but unresolved key — a real published file
{"key_hash": "…", "key": {"vendor": "Apache", "product": "Struts 1"},
 "resolution": null, "candidates_are_unverified": true,
 "candidates": [{"dimension": "product", "vendor_id": "apache",
                 "product_id": "struts-2", "product_name": "Struts 2",
                 "matched_on": "name", "matched_value": "Struts 2", "score": 88}]}

// 7. Unknown hash -> no file exists -> HTTP 404 (no JSON body)
```

**Published schemas.** `api/v1/schema/bundle.schema.json`,
`entry.schema.json`, `manifest.schema.json`, `tombstones.schema.json`. Build
output is validated against them in CI, so the published contract is tested,
not merely documented.

### D10. Candidate generation

`tools/suggest_match.py` performs source-agnostic global alias similarity for
*duplicate detection*. It is not a pair resolver, so candidate generation is a
new function that reuses only the rapidfuzz dependency.

**Dimension scoping:**

| Situation | Candidates |
|---|---|
| Vendor unresolved | Vendor candidates across all vendors. `dimension: "vendor"` |
| Vendor resolved, product unresolved | Product candidates **within that vendor only**. `dimension: "product"` |

Cross-vendor product candidates are **not** emitted. Product aliases are unique
per vendor, not globally, so a cross-vendor suggestion has no basis.

**The corpus — which strings are scored.** Deliberately wider than D4's
exact-resolution eligibility:

| | exact resolution (D4) | candidates (D10) |
|---|---|---|
| `cisa_kev` aliases | eligible | eligible |
| aliases from other sources | **not** eligible | **not** eligible |
| canonical `name` fields | **not** eligible | **eligible** |

Rationale for the one difference: exact resolution is authoritative and must be
conservative — a canonical rename must never silently change the API. A
candidate is advisory and a human checks it, so recall matters more, and a
product's display name is often the closest thing to the source string.
Other-source aliases stay out of both: an `nvd` value equal to a KEV string is
coincidence, and offering it as evidence would mislead the reviewer.

**One score per canonical target, not per string.** A target's score is the
**maximum** over its eligible strings. Each candidate reports `matched_on`
(`"alias"` or `"name"`) and `matched_value`, so the reviewer sees which string
produced the score.

**Scoring.** `rapidfuzz.fuzz.token_sort_ratio` over strings normalised by D3
*plus* `casefold()`. Casefolding is correct for scoring and wrong for hashing;
the two normalisations are deliberately different and separately named
(`normalize_key_part` vs `normalize_for_scoring`).

**Float to integer.** RapidFuzz returns a float in `[0, 100]`. The threshold is
applied to the **float**; the published `score` is `int(round(raw))` using
Python's round-half-to-even. Both steps are specified because rounding first
would let a 69.5 through and rounding differently would make the published
integer platform-dependent.

`token_sort_ratio` rather than `suggest_match.py`'s `fuzz.ratio`, because these
strings are word-order variant (`Reader and Acrobat` / `Acrobat and Reader`).
The divergence is deliberate and documented in both files.

**Determinism:** threshold 70 (advisory, versus 85 for duplicate warnings);
max 5 candidates; sort by score desc, then `vendor_id` asc, then `product_id`
asc. Ties therefore resolve identically on every build.

**Provenance.** Per-candidate: `dimension`, ids, display names, integer
`score`. Bundle-level `matching` block (D9) carries scorer, normalisation,
threshold, cap, and rapidfuzz version. `candidates_are_unverified: true` is
always present alongside candidates.

**Outside-feed clients** download `index/by-source/cisa_kev.json` — already
published, no new artifact. Each item there carries both the alias and the
entry's canonical `name`, so the client can build exactly the corpus defined
above with no additional fetch.

**Keeping the client aligned.** The reference implementation ships at
`docs/clients/resolve.py`. A test asserts that, over a fixture, it reproduces
the build-time candidate lists exactly. If the scorer, threshold, or
normalisation changes on either side, that test fails.

### D11. Schema invariants, and scope

`data/schema/resolutions.schema.json`. `data/schema/**` is CODEOWNERS-gated
(`/data/schema/ @b-mx`), so the new file inherits maintainer review.

**Invariants:**

- Top level: `{source, resolutions[]}`; `additionalProperties: false`
  throughout.
- Record required: `key`, `kind`, `confidence`, `targets`. Optional: `note`.
- `key`: for `cisa_kev`, required `vendor` and `product`, both `minLength: 1`.
  Keys must be upstream-verbatim; validation cross-checks each against
  `keys.json` (a key absent upstream is reported `stale` — a warning, not a
  failure; see D13).
- `targets`: `minItems: 1`, `maxItems: 32`, unique on `(vendor_id, product_id)`.
- Per-kind cardinality:

| kind | targets | `product_id` |
|---|---|---|
| `exact` | exactly 1 | required |
| `split` | ≥ 2 | required on all |
| `sentinel` | ≥ 1 | absent on all |
| `deferred` | exactly 1 | absent; `note` required |

- **Mixed product and vendor-only targets in one record are illegal.** Each
  kind pins it, so a mixed record cannot validate.
- Source directory name matches `^[a-z0-9_]+$` and must appear in the alias
  `source` enum. Only `data/sources/<source>/{keys.json,resolutions.yaml}` may
  exist under a source directory, plus `data/tombstones.yaml` at the data root
  (D12); anything else fails, mirroring `validate_directory_structure`.
- **Duplicate-key detection uses the D3-normalised key**, not the verbatim
  string, so two records that would collide in the published API are caught at
  validation rather than at build.

**Scope: v1 is CISA KEV only.** The schema is source-discriminated — `source`
selects the `key` shape — but v1 defines `cisa_kev`'s `{vendor, product}` and
nothing else. NVD CPE keys are `{part, vendor, product}`, OSV is
`{ecosystem, package}`, endoflife is `{product}`. Forcing one key shape across
all four would repeat the exact error this spec exists to correct. Adding a
source means adding a discriminated branch and its own `keys.json`, which the
directory layout already anticipates.

### D12. Tombstone semantics

**The tracked source file.** The earlier draft defined only the generated
`api/v1/tombstones.json` and never said where tombstones are *authored*. They
are permanent records and must be tracked:

**`data/tombstones.yaml`** — a single **canonical, global** file, not
per-source. A tombstone is a fact about a canonical entry (a vendor or product
slug), and a deleted entry may carry aliases from several sources; filing it
under `data/sources/cisa_kev/` would misattribute it. Placed alongside
`data/taxonomy/tags.yaml` as a top-level curated data file, and **added to
CODEOWNERS** (`/data/tombstones.yaml @b-mx`) because it is a permanent public
contract.

```yaml
tombstones:
- slug: apple--multiple-products
  canonical_type: product
  vendor_id: apple
  product_id: multiple-products
  removed_on: 2026-09-14
  reason: sentinel-materialized
  replaced_by:
  - {vendor_id: apple}
  note: "KEV 'Multiple Products' resolves to the vendor via the resolution layer"
```

**`removed_on`, not `removed_in`.** A tracked record cannot contain the SHA of
the commit that is being created — that is circular, and resolving it would
need a two-commit dance. It is a **UTC date** (`YYYY-MM-DD`), written by the
review UI at deletion time. A date is sufficient for the only real question a
consumer asks ("when did this go away?") and is stable under rebase, squash,
and cherry-pick, which a SHA is not. The repo publishes no releases, so a
release identifier is not available.

**Keyed by entry slug**, matching `index/entries/<slug>.json`, because the slug
is what consumers fetch. `apple--multiple-products`, not
`apple/multiple-products`. `vendor_id`/`product_id` carry the canonical id
separately; `build_index.py` asserts the slug and the ids agree.

**Directory allowlist (updated).** D11's structure check permits
`data/sources/<source>/{keys.json,resolutions.yaml}` and, at the data root,
`data/tombstones.yaml`. The earlier draft's allowlist had no place for
tombstones, which contradicted D8's claim that the UI stages them.

**Consumption.** `build_index.py` reads `data/tombstones.yaml` and emits
`api/v1/tombstones.json` — the same records, sorted by `slug`, with display
names resolved for every `replaced_by` target and **no `commit` field** (D14):

```jsonc
{"schema_version": "1.0",
 "tombstones": [
   {"slug": "apple--multiple-products",
    "canonical_type": "product",
    "canonical_id": {"vendor_id": "apple", "product_id": "multiple-products"},
    "removed_on": "2026-09-14",
    "reason": "sentinel-materialized",
    "replaced_by": [{"vendor_id": "apple", "vendor_name": "Apple"}],
    "note": "KEV 'Multiple Products' resolves to the vendor via the resolution layer"}]}
```

**Authoring in the UI.** Deleting an entry from the resolutions tab appends a
`data/tombstones.yaml` record in the same working-tree change, stamping
`removed_on` from the current UTC date. `do_commit_pr`'s path list therefore
includes `data/tombstones.yaml` (D8).

- `replaced_by` uses the D9 target shape. Zero targets is legal (removed with
  no replacement) and requires a `note`. Vendor-only and one-to-many are both
  legal.
- **"Redirect" is retracted.** No HTTP redirect is emitted and the old
  `entries/<slug>.json` **404s after removal**. `tombstones.json` is an
  advisory lookup a client consults on 404. Stated plainly so nobody expects
  transparent behaviour.
- `reason` is an enum: `sentinel-materialized`, `split-fragment`,
  `phantom-vendor`, `duplicate`, `merged`.
- **Alias migration.** Deleting an entry carrying non-`cisa_kev` aliases
  requires each alias to be moved to a real entry or explicitly dropped with a
  note. Post-deletion there is nothing left to validate, so this is enforced by
  the review UI (it surfaces every alias on a deletion candidate) and by a PR
  template checklist item — not by `validate.py`.
- **Retention: permanent within `api/v1`.** ~40 entries; the cost of keeping
  them is far below the cost of silently breaking an old consumer.
- **No chaining.** A tombstone's `replaced_by` may not name a tombstoned slug.
  If a replacement is later removed, the earlier tombstone is **rewritten** to
  point at the final targets. A validation check enforces this.
- **Relationship to `api/v1` stability:** `api/v1` guarantees *envelope*
  stability — field names, types, response shapes. It does **not** guarantee
  canonical-id permanence. Ids are curated data and may be corrected;
  tombstones are the mechanism by which that correction is discoverable. Worth
  saying in the README, since the earlier draft implied more than it delivered.

### D13. Bidirectional drift

`.github/workflows/source-drift.yml`, scheduled daily.

**Both directions:**

| Upstream change | Behaviour |
|---|---|
| Key added | Added to `keys.json`; listed in the PR body with S1/S2 ranking |
| Key removed | Removed from `keys.json`. Any resolution record for it is reported **`stale`** — never auto-deleted |
| Key changed (rename/typo fix) | Surfaces as one removal + one addition. The PR body pairs them by similarity as a *hint*; a human confirms |

A record whose upstream key has disappeared is not evidence the curation was
wrong — KEV has corrected strings before. `validate.py` reports `stale` records
as a **warning**, not a failure, so an upstream removal never breaks the build.

**Mechanics:**

- Fixed branch `automation/source-drift`. Idempotent: if the regenerated
  `keys.json` is byte-identical to the branch's, the job exits without
  touching anything.
- **Branch safety is defined by commit ownership, not by path.** The earlier
  draft said the workflow "force-pushes only `data/sources/*/keys.json`", which
  is not a thing git can do — a force-push replaces branch history wholesale.
  Worse, a path-based check would still discard a human edit *to `keys.json`
  itself*. The actual rule:

  1. Record the remote head SHA at job start.
  2. Enumerate commits between `merge-base(main, automation/source-drift)` and
     the remote head.
  3. Rewrite the branch **only if every one of those commits is
     automation-owned** — authored by `github-actions[bot]` and carrying the
     `automation:` subject prefix.
  4. Push with
     `--force-with-lease=refs/heads/automation/source-drift:<recorded-sha>`, so
     a commit landing mid-job aborts the push rather than clobbering it.
  5. Otherwise: **do not push.** Comment on the open PR explaining that the
     branch carries human commits and needs manual reconciliation.

  Any human commit on the branch — to `keys.json` or anything else — therefore
  stops automation dead.
- `concurrency: {group: source-drift, cancel-in-progress: false}` so two runs
  cannot race.
- **Feed validation before writing:** top-level `vulnerabilities` must be a
  non-empty array and every record must carry `vendorProject`, `product`, and
  `cveID`. A network error or malformed feed fails the job with no partial
  write and no PR; the next scheduled run retries.
- A sudden drop of more than 10% in key count aborts as a suspected truncated
  feed.

**Token: `GITHUB_TOKEN`, decided.** No PAT.

A correction to the earlier draft, which asserted that `GITHUB_TOKEN`-created
PRs produce "no check runs". That is no longer accurate as a universal
statement — per GitHub's current *Triggering a workflow* documentation, PRs
created or updated with `GITHUB_TOKEN` can produce **approval-required**
workflow runs rather than none at all. The recursive-trigger suppression that
motivated the old advice has explicit exceptions, and `workflow_dispatch` and
`repository_dispatch` are among them.

The design therefore is:

1. The drift job runs the **complete** suite itself before touching the PR —
   `ruff`, `mypy --strict tools`, `pytest`, `tools/validate.py`, and a full
   `build_index.py` including the API surface. A failure means no push and no
   PR.
2. After updating the branch, the job **dispatches `validate.yml` via
   `workflow_dispatch` against `automation/source-drift`**, so validation is a
   visible workflow run attached to the branch rather than a buried job log.
   `workflow_dispatch` is an exception to recursive-trigger suppression, so
   this fires reliably. `validate.yml` gains a `workflow_dispatch:` trigger.
3. If fully unattended PR-triggered checks are wanted later, the escalation is
   a **narrowly scoped GitHub App installation token** — not a personal PAT.
   An app token is scoped to the repository, has no human owner to offboard,
   and rotates automatically.

### D14. Build outputs and reproducibility

**CLI contract.** `build_index.py` keeps `--output-dir` (the index directory,
unchanged) and gains:

- `--api-output-dir DIR` — when omitted, **no API files are written**
- `--commit SHA` — recorded in envelopes; `null` when omitted

`publish-index.yml` passes `--output-dir dist/index --api-output-dir
dist/api/v1 --commit ${{ github.sha }}`. Keeping the two flags separate avoids
moving `data/examples/aliases.json`, whose path is documented in the README.

**`data/examples/` holds the legacy index only.** Rationale: it exists to make
the CI `diff -r` freshness check cheap and the README example stable. API
output is covered instead by fixture-based integration tests (below), which
give stronger guarantees than a committed snapshot.

**Cleaning.** Stale output is a real hazard — `rebuild_examples()` currently
writes into an existing tree and never deletes, so removing the 34 phantom
products would leave orphaned `entries/*.json` that CI's `diff -r` flags.
`build_index.py` therefore removes and recreates the directories it owns
(`index/entries`, `index/by-source`, `api/v1`) before writing. Guard: it
refuses to delete a directory containing files it did not produce, since
`--output-dir data/examples` points inside the repo.

**Manifest.** `api/v1/manifest.json` is the only artifact carrying
`generated_at`:

```jsonc
{"schema_version": "1.0", "generated_at": "2026-08-29T03:11:07Z", "commit": "acc6e4ae9",
 "files": [{"path": "sources/cisa_kev.json", "sha256": "<64 hex>", "bytes": 143012}],
 "counts": {"sources": 1, "keys": 706, "resolved_exact": 556, "resolved_record": 150,
            "unresolved": 0, "orphaned": 0, "curated": 150, "auto": 0, "tombstones": 34}}
```

- Content hashes are **sha256 over each output file's exact bytes as written**,
  full 64 hex chars (not truncated — this is integrity, not addressing).
- **No hashed file contains volatile metadata.** Neither `generated_at` nor
  `commit` appears in the bundle, in any per-key file, or in
  `tombstones.json`. Both live only here, and `manifest.json` is not hashed by
  itself.
- This is load-bearing, not tidiness. With `commit` embedded in every API file,
  an unrelated README commit would change every content hash while no mapping
  data had changed — the manifest would report a full-scale change on every
  deploy and consumers would re-download everything for nothing.
- Consequence: identical tracked *data* produces byte-identical bundle,
  per-key, and tombstone files regardless of which commit deployed them, and a
  changed hash means the data genuinely changed.

**Deterministic ordering.**

- `resolutions.yaml`: records sorted by the D3-normalised key, written with
  `sort_keys=False` to preserve the documented field order.
- New API JSON: `json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)`
  plus a trailing newline.
- The legacy `index/` writer is left exactly as it is, to avoid a large
  no-op diff in `data/examples/`.

## Data flow

```
CISA KEV feed
      │  (network — drift workflow only)
      ▼
source-drift.yml ──► data/sources/cisa_kev/keys.json      (tracked snapshot, PR)
                                │
      ┌─────────────────────────┤
      ▼                         ▼
tools/sources/pull_cisa_kev.py  │        reads curated records FIRST (D5);
      │  proposes auto records  │        never splits a curated key
      ▼                         │
data/sources/cisa_kev/resolutions.yaml
      ▲                         │
      │ writes curated          │
tools/review_ui (tracked) ──────┤
                                │
                                ▼
tools/validate.py  (schema, referential integrity, normalized-key uniqueness,
                    stale-key warnings, tombstone non-chaining)
                                │
                                ▼
tools/build_index.py  --output-dir dist/index
                      --api-output-dir dist/api/v1 --commit <sha>
                      (no network; pure function of tracked files)
                                │
                                ▼
publish-index.yml ──► gh-pages
```

## Testing

**Hashing and normalisation (D3)** — the six fixed test vectors, including the
whitespace vector that must equal row 1 and the `IOS Software` / `IOS software`
pair that must differ. Length-prefixing: `("ab","c")` and `("a","bc")` hash
differently. C0/C1 control characters in a key part are rejected. A snapshot
containing two verbatim keys that normalise identically is rejected; separately,
a synthetic hash collision fails the build.

**Normalised alias uniqueness (D4)** — two `cisa_kev` product aliases under one
vendor differing only in whitespace are rejected; the same values under
different vendors are accepted; a non-eligible source (`nvd`) is unaffected.

**Exact resolution (D4)** — resolves via a `cisa_kev` alias; does **not**
resolve via a canonical `name`; does **not** resolve via an `nvd` alias of the
same value; unknown vendor yields `unresolved` without consulting products;
confidence composition returns `auto` when either side is `auto`.

**Rebuild safety (D5)** — the required regression test: a curated record for
`Atlassian / "Confluence Data Center and Server"` must leave
`atlassian/products/server.yaml` uncreated after a full importer run. Separately,
`auto` records are overwritten and `curated` records are byte-identical.

**State machine (D6, D7)** — every kind/cardinality rule from the D11 table,
positive and negative; a record with empty `targets` is rejected; `deferred`
without `note` is rejected; orphan detection works on a tree that fails
`validate.py`.

**Review UI (D8)** — `resolve()` rejects `data/vendors-evil/x.yaml` (the
sibling-prefix escape the current `startswith` admits) and accepts paths under
both allowlisted roots; `git_status_for_paths` reports changes under
`data/sources`; `do_commit_pr` stages a resolutions-only change without raising
"nothing to commit"; `rebuild_examples` removes an entry file whose product was
deleted.

**API contract (D9)** — all seven response shapes validate against the
published schemas; for every key, the parsed entry in the per-key file is
**deep-equal** to its bundle counterpart; target ordering and uniqueness hold;
`product_resolved` is present on every non-null resolution; no bundle, per-key,
or tombstone file contains `commit` or `generated_at`.

**Candidates (D10)** — vendor-unresolved yields only `dimension: "vendor"`;
product candidates never cross vendors; a canonical `name` can produce a
candidate but never an exact resolution; a target scored via two strings
reports the maximum once, with `matched_on`/`matched_value`; a raw score of
69.5 is excluded by the float threshold; ties break deterministically across
two runs; `docs/clients/resolve.py` reproduces build-time candidates over a
fixture.

**Tombstones (D12)** — chaining is rejected; a zero-target tombstone without a
note is rejected; `removed_on` must be a valid date; slug and `vendor_id`/
`product_id` must agree; a tombstone file outside the directory allowlist is
rejected.

**Drift (D13)** — added, removed, and renamed keys each produce the documented
outcome; a removed key with a curated record yields a `stale` warning and no
deletion; a malformed feed writes nothing; a >10% key drop aborts. Branch
safety: a branch carrying a human commit — including one touching only
`keys.json` — stops the push and comments; a remote head that moved mid-job
fails the `--force-with-lease` rather than clobbering.

**Build (D14)** — two builds from identical data at **different commits**
produce byte-identical bundle, per-key, and tombstone files, and identical
manifest content hashes; only the manifest's own `commit`/`generated_at`
differ. Deleting a product removes its entry file rather than leaving it stale;
omitting `--api-output-dir` writes no API files.

**Existing suite** — `ruff`, `mypy --strict tools`, `pytest`, and
`tools/validate.py` stay green, including the `data/examples/` freshness check,
which needs regenerating.

## Risks

- **Phantom removal is breaking.** `tombstones.json` helps only consumers who
  read it. Warrants a README note and an announcement.
- **The sentinel lexicon is curated and will lag.** A new collective phrasing
  lands as `unresolved`, not `sentinel`, and a reviewer must recognise it. The
  alternative — a `Devices$` suffix rule — silently swallows real model names
  like `DCS-930L Devices`, so lag is the correct trade. The lexicon needs an
  owner.
- **Importer promotion under `mypy --strict`** may be larger than one table
  row suggests, and everything depends on it. It is sequenced first for that
  reason.
- **`d-link-and-trendnet` removal is unblocked** — both `d-link/` and
  `trendnet/` already exist.

## Sequencing

Revised so no step leaves schema-valid but unpublishable or unrebuildable
state. Each step is independently green.

1. **Promote importers** to `tools/sources/` under `ruff`/`mypy --strict`/
   `pytest`. No behaviour change. Unblocks CI; land first.
2. **Promote the review UI** to `tools/review_ui/`, including the `resolve()`
   path-guard fix. Still vendor-tab only. The security fix ships early and
   alone.
3. **Normalised alias uniqueness (D4)** — add
   `validate_alias_uniqueness_normalized`. Verified to pass on current data, so
   it lands as a pure guard with no migration. Sequenced before the snapshot
   because the snapshot's uniqueness guarantee depends on it.
4. **Snapshot** — add `keys.json`, its generator, normalisation-collision
   rejection, and control-character rejection. Nothing consumes it yet, so it
   cannot break a publish.
5. **Schema and validation** — `resolutions.schema.json`, referential
   integrity, normalised-key uniqueness, directory structure allowlist.
   `resolutions.yaml` is still empty, so all checks pass trivially.
6. **Importer consumes curated records (D5)** plus the phantom-recreation
   regression test — *before* any curated record exists, so the guard is proven
   before it is relied upon.
7. **Review UI resolutions tab**; curate the ~150-record backlog. Data-only;
   nothing is published yet.
8. **API surface** — `--api-output-dir`, cleaning, manifest, published schemas.
   First consumer-visible change, and by now the data behind it is curated.
9. **Tombstone plumbing** — `data/tombstones.yaml`, its schema, the CODEOWNERS
   entry, the directory-allowlist update, and `build_index.py` consumption.
   Lands **empty**, so the mechanism is proven and published before it carries
   a single record.
10. **Phantom removal**, once resolutions cover every affected key and the
    tombstone file is already live. This is the breaking step.
11. **Drift workflow**, plus a `workflow_dispatch:` trigger on `validate.yml`,
    after the shape it maintains is stable.

Steps 1–7 are invisible to consumers. Splitting tombstone *plumbing* (9) from
tombstone *use* (10) means the deletion PR adds only data to a mechanism
already deployed and tested — no step ships an entry whose tombstone cannot yet
be published.
