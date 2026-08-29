#!/usr/bin/env python3
"""Import vendor/product candidates from the CISA KEV catalog into data/vendors/.

Dry-run by default — prints a report of what it would do. Pass --apply to
actually write new vendor.yaml / product.yaml files. Never edits an
existing file; it only ever creates brand-new ones, and only when it found
no plausible existing match (exact or fuzzy) for the CISA entry.

Usage:
    uv run python -m tools.sources.pull_cisa_kev                 # dry run
    uv run python -m tools.sources.pull_cisa_kev --apply          # write new files
    uv run python -m tools.sources.pull_cisa_kev --refresh        # bypass the 1-day cache
    uv run python -m tools.sources.pull_cisa_kev --limit 50       # only look at the first 50 pairs

After --apply, run `uv run tools/validate.py` and review every entry this
created — all aliases are written with confidence: auto and type defaults
to "software" since CISA KEV doesn't tell us the real product type.
"""

from __future__ import annotations

import argparse

from tools._common import REPO_ROOT
from tools.suggest_match import AliasRecord

from ._lib import (
    CACHE_DIR,
    MULTIPLE_VENDORS_SENTINEL,
    NewProduct,
    NewVendor,
    RunStats,
    fetch_json_cached,
    load_existing,
    resolve_against_index,
    slugify,
    split_product_names,
    write_new_product,
    write_new_vendor,
)

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
SOURCE = "cisa_kev"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="write new files (default: dry run)")
    parser.add_argument("--refresh", action="store_true", help="bypass the 1-day download cache")
    parser.add_argument(
        "--limit", type=int, default=None, help="only process the first N vendor/product pairs"
    )
    parser.add_argument("--threshold", type=int, default=85, help="fuzzy match threshold (0-100)")
    parser.add_argument(
        "--only-products",
        default=None,
        help="'|'-separated list of exact CISA product strings to reprocess "
        "(skips everything else) — for targeted reruns after a script fix, "
        "without touching the rest of the already-reviewed catalog",
    )
    args = parser.parse_args()

    print(f"Fetching CISA KEV catalog{' (forced refresh)' if args.refresh else ''}...")
    catalog = fetch_json_cached(KEV_URL, CACHE_DIR / "cisa-kev.json", refresh=args.refresh)
    vulns = catalog["vulnerabilities"]
    print(f"  catalogVersion={catalog.get('catalogVersion')} count={catalog.get('count')}")

    pairs = list(dict.fromkeys((v["vendorProject"].strip(), v["product"].strip()) for v in vulns))
    if args.only_products:
        wanted = set(args.only_products.split("|"))
        pairs = [pair for pair in pairs if pair[1] in wanted]
        print(f"  --only-products: restricting to {len(pairs)} matching pair(s)")
    if args.limit:
        pairs = pairs[: args.limit]

    existing = load_existing()
    vendor_entries = existing.vendor_entries_with_aliases()
    stats = RunStats(total_candidates=len(pairs))
    pending_vendors: dict[str, NewVendor] = {}
    pending_products: dict[str, NewProduct] = {}  # keyed by "vendor_id/product_id"

    for vendor_name, product_name in pairs:
        if not vendor_name or not product_name or vendor_name.lower() in MULTIPLE_VENDORS_SENTINEL:
            stats.skipped += 1
            continue

        vendor_slug = slugify(vendor_name)
        if vendor_slug and vendor_slug in pending_vendors:
            vendor_id = vendor_slug
            vendor_known = True
        else:
            vendor_res = resolve_against_index(
                vendor_name,
                SOURCE,
                vendor_entries,
                existing.vendor_alias_index,
                args.threshold,
                vendor_name,
            )
            if vendor_res.status == "review":
                stats.review_vendor.append(f"{vendor_res.note} (for product '{product_name}')")
                continue
            if vendor_res.status == "mapped":
                vendor_id = vendor_res.canonical_id or ""
                vendor_known = True
            else:
                if not vendor_slug:
                    stats.skipped += 1
                    continue
                vendor_id = vendor_slug
                vendor_known = False

        # A CISA "product" field can list several distinct products separated by
        # commas (e.g. "iOS, macOS, watchOS") — resolve/create each independently
        # so partially-known lists only create the genuinely-new parts.
        for product_part in split_product_names(product_name):
            product_entries = (
                existing.product_entries_with_aliases(vendor_id) if vendor_known else []
            )
            product_alias_index = (
                existing.product_alias_index_for_vendor(vendor_id) if vendor_known else []
            )
            pending_this_vendor = [
                (key, p.aliases)
                for key, p in pending_products.items()
                if key.startswith(f"{vendor_id}/")
            ]
            pending_alias_records = [
                AliasRecord(value=a["value"], canonical_id=key)
                for key, aliases in pending_this_vendor
                for a in aliases
            ]

            product_res = resolve_against_index(
                product_part,
                SOURCE,
                product_entries + pending_this_vendor,
                product_alias_index + pending_alias_records,
                args.threshold,
                product_part,
            )
            if product_res.status == "mapped":
                stats.already_mapped += 1
                continue
            if product_res.status == "review":
                stats.review_product.append(product_res.note)
                continue

            product_slug = slugify(product_part)
            if not product_slug:
                stats.skipped += 1
                continue
            pending_key = f"{vendor_id}/{product_slug}"
            if pending_key in pending_products:
                continue

            new_product = NewProduct(
                id=product_slug,
                vendor_id=vendor_id,
                name=product_part,
                type="software",
                tags=[],
                aliases=[{"source": SOURCE, "value": product_part, "confidence": "auto"}],
            )
            pending_products[pending_key] = new_product
            stats.new_products.append(new_product)

            if not vendor_known and vendor_slug not in pending_vendors:
                new_vendor = NewVendor(
                    id=vendor_id,
                    name=vendor_name,
                    aliases=[{"source": SOURCE, "value": vendor_name, "confidence": "auto"}],
                )
                pending_vendors[vendor_slug] = new_vendor
                stats.new_vendors.append(new_vendor)

    stats.report(source_label="CISA KEV")

    if not args.apply:
        print("\nDry run — no files written. Re-run with --apply to create the files above.")
        return 0

    print("\nWriting new files...")
    skipped = 0
    for v in stats.new_vendors:
        try:
            path = write_new_vendor(v)
            print(f"  wrote {path.relative_to(REPO_ROOT)}")
        except FileExistsError as exc:
            print(f"  SKIPPED: {exc}")
            skipped += 1
    for p in stats.new_products:
        try:
            path = write_new_product(p)
            print(f"  wrote {path.relative_to(REPO_ROOT)}")
        except FileExistsError as exc:
            print(f"  SKIPPED: {exc}")
            skipped += 1
    if skipped:
        print(f"\n{skipped} file(s) skipped due to path collisions — see above, review manually.")
    print(
        "\nDone. Now run `uv run tools/validate.py` and review the new (confidence: auto) entries."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
