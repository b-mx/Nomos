#!/usr/bin/env python3
"""Import vendor/product candidates from endoflife.date into data/vendors/.

Dry-run by default — prints a report of what it would do. Pass --apply to
actually write new vendor.yaml / product.yaml files. Never edits an
existing file; it only ever creates brand-new ones, and only when it found
no plausible existing match (exact or fuzzy) for the endoflife.date entry.

Vendor identity is derived from the product's `cpe` identifier when
endoflife.date provides one (e.g. cpe:2.3:o:canonical:ubuntu_linux ->
vendor "canonical"), matching the nvd convention already used in this
repo. Products with no cpe identifier fall back to the repo's
self-vendored convention (vendor id == product id) — e.g. most
language/framework/package entries.

Usage:
    uv run python -m tools.sources.pull_endoflife            # dry run
    uv run python -m tools.sources.pull_endoflife --apply     # write new files
    uv run python -m tools.sources.pull_endoflife --refresh   # bypass the 1-day cache
    uv run python -m tools.sources.pull_endoflife --limit 30  # only look at the first 30 products

Fetching per-product detail is one HTTP request per product (~500) the
first time; each is cached individually for 1 day so re-runs the same day
are fast. After --apply, run `uv run tools/validate.py` and review every
entry this created — all aliases are written with confidence: auto.
"""

from __future__ import annotations

import argparse
import hashlib
import time
from pathlib import Path
from urllib.parse import quote

from tools._common import REPO_ROOT
from tools.suggest_match import AliasRecord

from ._lib import (
    CACHE_DIR,
    NewProduct,
    NewVendor,
    RunStats,
    fetch_json_cached,
    load_existing,
    resolve_against_index,
    slugify,
    write_new_product,
    write_new_vendor,
)

PRODUCTS_URL = "https://endoflife.date/api/v1/products"
PRODUCT_DETAIL_URL = "https://endoflife.date/api/v1/products/{name}"

# endoflife.date `category` -> Nomos product `type`
CATEGORY_TO_TYPE = {
    "os": "os",
    "device": "hardware",
    "hardware": "hardware",
    "firmware": "firmware",
    "app": "software",
    "server-app": "software",
    "service": "software",
    "framework": "library",
    "library": "library",
    "lang": "library",
    "language": "library",
    "db": "software",
    "database": "software",
}


def parse_cpe_vendor_product(identifiers: list[dict[str, str]]) -> tuple[str, str] | None:
    for ident in identifiers:
        if ident.get("type") != "cpe":
            continue
        parts = ident.get("id", "").split(":")
        # cpe:2.3:<part>:<vendor>:<product>[...]
        if len(parts) >= 5 and parts[0] == "cpe":
            return parts[3], parts[4]
    return None


def product_detail_cache_path(name: str, detail_cache_dir: Path) -> Path:
    """Derive a safe, stable cache filename for an endoflife.date product name.

    `name` comes straight from the upstream API response and must never be
    used as a raw path component: a name containing '../', a leading '/',
    or embedded separators would otherwise escape `detail_cache_dir` and
    let the API response choose where a `.json` file gets written on disk.

    percent-encoding (`quote(name, safe="")`) turns every '/' and non-ASCII
    byte into an ASCII-safe escape sequence, which is deterministic (same
    input always yields the same output, so the on-disk cache keeps
    working across runs) and collapses any embedded separators into a
    single filename component. It does *not* touch '.', so a name of '.'
    or '..' would encode to itself — that's handled explicitly below.
    A resolved-path containment check is kept as a second, independent
    guard rather than trusting the encoding alone.

    A very long or heavily non-ASCII name (every non-ASCII byte becomes a
    3-character '%XX' escape) can percent-encode to a filename component
    longer than the ~255-byte limit most filesystems impose, which would
    otherwise raise an uncaught OSError at write time. When the encoded
    name plus the '.json' suffix would exceed that budget, it is truncated
    and a short hash of the *full* original name is appended, so the
    mapping stays stable (same input always yields the same output) and
    collision-resistant (two names that share a long common prefix still
    truncate to different filenames).
    """
    if not name:
        raise ValueError("product name must not be empty")
    encoded = quote(name, safe="")
    if encoded in (".", ".."):
        raise ValueError(f"unsafe product name: {name!r}")

    suffix = ".json"
    max_filename_bytes = 255
    if len(encoded) + len(suffix) > max_filename_bytes:
        digest = hashlib.sha256(name.encode()).hexdigest()[:16]
        keep = max_filename_bytes - len(suffix) - len(digest) - 1  # '-' separator
        encoded = f"{encoded[:keep]}-{digest}"

    cache_dir_resolved = detail_cache_dir.resolve()
    candidate = (detail_cache_dir / f"{encoded}{suffix}").resolve()
    if not candidate.is_relative_to(cache_dir_resolved):
        raise ValueError(f"cache path for product name {name!r} escapes {detail_cache_dir}")
    return candidate


def guess_type(category: str) -> str:
    return CATEGORY_TO_TYPE.get(category, "software")


def guess_tags(category: str, tags: list[str], known_tags: set[str]) -> list[str]:
    candidates = {category, *tags}
    return sorted(t for t in candidates if t in known_tags)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="write new files (default: dry run)")
    parser.add_argument("--refresh", action="store_true", help="bypass the 1-day download cache")
    parser.add_argument("--limit", type=int, default=None, help="only process the first N products")
    parser.add_argument("--threshold", type=int, default=85, help="fuzzy match threshold (0-100)")
    args = parser.parse_args()

    print(f"Fetching endoflife.date product list{' (forced refresh)' if args.refresh else ''}...")
    listing = fetch_json_cached(
        PRODUCTS_URL, CACHE_DIR / "endoflife-products.json", refresh=args.refresh
    )
    products = listing["result"]
    print(f"  total={listing.get('total')} products")
    if args.limit:
        products = products[: args.limit]

    existing = load_existing()
    vendor_entries = existing.vendor_entries_with_aliases()
    stats = RunStats(total_candidates=len(products))
    pending_vendors: dict[str, NewVendor] = {}
    pending_products: dict[str, NewProduct] = {}

    detail_cache_dir = CACHE_DIR / "endoflife" / "products"
    for summary in products:
        name = summary["name"]
        label = summary.get("label", name)
        detail_cache_path = product_detail_cache_path(name, detail_cache_dir)
        cache_is_fresh = (
            not args.refresh
            and detail_cache_path.exists()
            and (time.time() - detail_cache_path.stat().st_mtime) < 24 * 60 * 60
        )
        detail = fetch_json_cached(
            PRODUCT_DETAIL_URL.format(name=name),
            detail_cache_path,
            refresh=args.refresh,
        )["result"]
        if not cache_is_fresh:
            time.sleep(0.1)  # be polite when actually hitting the network

        identifiers = detail.get("identifiers", [])
        category = detail.get("category", "")
        cpe = parse_cpe_vendor_product(identifiers)

        product_aliases = [{"source": "endoflife", "value": name, "confidence": "auto"}]
        vendor_aliases: list[dict[str, str]] = []
        if cpe:
            cpe_vendor, cpe_product = cpe
            product_aliases.append({"source": "nvd", "value": cpe_product, "confidence": "auto"})
            vendor_aliases.append({"source": "nvd", "value": cpe_vendor, "confidence": "auto"})

        # --- resolve vendor ---
        if cpe:
            cpe_vendor, _ = cpe
            probe_value, probe_source = cpe_vendor, "nvd"
            vendor_display_name = cpe_vendor.replace("_", " ").title()
            vendor_slug = slugify(cpe_vendor)
        else:
            # self-vendored convention: vendor id/name mirrors the product
            probe_value, probe_source = name, "endoflife"
            vendor_display_name = label
            vendor_slug = slugify(name)

        if vendor_slug and vendor_slug in pending_vendors:
            vendor_id = vendor_slug
            vendor_known = True
        else:
            vendor_res = resolve_against_index(
                probe_value,
                probe_source,
                vendor_entries,
                existing.vendor_alias_index,
                args.threshold,
                probe_value,
            )
            if vendor_res.status == "review":
                stats.review_vendor.append(f"{vendor_res.note} (for product '{name}')")
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

        # --- resolve product ---
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
            name,
            "endoflife",
            product_entries + pending_this_vendor,
            product_alias_index + pending_alias_records,
            args.threshold,
            f"{label} ({name})",
        )
        if product_res.status == "mapped":
            stats.already_mapped += 1
            continue
        if product_res.status == "review":
            stats.review_product.append(product_res.note)
            continue

        product_slug = slugify(name)
        if not product_slug:
            stats.skipped += 1
            continue
        pending_key = f"{vendor_id}/{product_slug}"
        if pending_key in pending_products:
            continue

        new_product = NewProduct(
            id=product_slug,
            vendor_id=vendor_id,
            name=label,
            type=guess_type(category),
            tags=guess_tags(category, detail.get("tags", []), existing.known_tags),
            aliases=product_aliases,
        )
        pending_products[pending_key] = new_product
        stats.new_products.append(new_product)

        if not vendor_known and vendor_slug not in pending_vendors:
            new_vendor = NewVendor(
                id=vendor_id,
                name=vendor_display_name,
                aliases=vendor_aliases
                or [{"source": "endoflife", "value": name, "confidence": "auto"}],
            )
            pending_vendors[vendor_slug] = new_vendor
            stats.new_vendors.append(new_vendor)

    stats.report(source_label="endoflife.date")

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
