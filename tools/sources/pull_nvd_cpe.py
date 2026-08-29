#!/usr/bin/env python3
"""Backfill cpe onto existing entries and import top-N vendors from NVD's
CPE match dump into data/vendors/.

Dry-run by default — prints a report of what it would do. Pass --apply to
write. Never edits an existing file's other fields — the backfill pass
only ever adds a missing `cpe`/`purl` field, and new-file creation refuses
to overwrite a path that already exists (see write_new_vendor/product).

This does NOT download the NVD CPE match feed itself — NVD blocks
scripted fetches. Download it manually from:
    https://nvd.nist.gov/feeds/json/cpematch/2.0/nvdcpematch-2.0.tar.gz
and place it at tmp/cache/nvdcpematch-2.0.tar.gz.

Usage:
    uv run python -m tools.sources.pull_nvd_cpe                  # dry run
    uv run python -m tools.sources.pull_nvd_cpe --apply           # write new files
    uv run python -m tools.sources.pull_nvd_cpe --top-n 200      # only top 200 (default 1000)
    uv run python -m tools.sources.pull_nvd_cpe --refresh        # re-parse, bypass cache
"""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path
from typing import Any

from tools._common import REPO_ROOT
from tools.suggest_match import AliasRecord

from ._lib import (
    CACHE_DIR,
    CPE_PART_TO_TYPE,
    PRODUCT_KEY_ORDER,
    VENDOR_KEY_ORDER,
    ExistingData,
    NewProduct,
    NewVendor,
    ecosystem_to_purl,
    format_cpe_prefix,
    load_existing,
    parse_cpe_criteria,
    resolve_against_index,
    slugify,
    unescape_cpe_component,
    write_new_product,
    write_new_vendor,
)

SOURCE = "nvd"

NVD_CPE_TARBALL = CACHE_DIR / "nvdcpematch-2.0.tar.gz"
NVD_CPE_REDUCED_CACHE = CACHE_DIR / "nvd-cpe-reduced.json"


def build_reduced_cpe_map(
    tarball_path: Path = NVD_CPE_TARBALL,
    cache_path: Path = NVD_CPE_REDUCED_CACHE,
    *,
    refresh: bool = False,
) -> dict[str, dict[str, str]]:
    """Stream the NVD CPE match tarball into {vendor: {product: part}}.

    Cached at cache_path; reused whenever the cache is newer than the
    source tarball (parsing the full ~3.5GB uncompressed feed is the
    expensive part, and it doesn't change between reruns of this script).
    """
    if not tarball_path.exists():
        raise FileNotFoundError(
            f"{tarball_path} not found. Download it manually from "
            "https://nvd.nist.gov/feeds/json/cpematch/2.0/"
            "nvdcpematch-2.0.tar.gz "
            "(nvd.nist.gov blocks scripted downloads, so this can't be automated) "
            "and place it there."
        )

    if (
        not refresh
        and cache_path.exists()
        and cache_path.stat().st_mtime > tarball_path.stat().st_mtime
    ):
        return dict(json.loads(cache_path.read_text()))

    result: dict[str, dict[str, str]] = {}
    malformed = 0
    unconfirmed = 0
    with tarfile.open(tarball_path, "r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            fileobj = tar.extractfile(member)
            if fileobj is None:
                continue
            chunk = json.load(fileobj)
            for entry in chunk.get("matchStrings", []):
                match_string = entry.get("matchString", {})
                criteria = match_string.get("criteria", "")
                parsed = parse_cpe_criteria(criteria)
                if parsed is None:
                    malformed += 1
                    continue
                # NVD's own confirmation signal: a criteria string only
                # represents a real, dictionary-known CPE when it resolved
                # to at least one concrete cpeName (the `matches` array) AND
                # NVD still considers it current (status == "Active").
                # Unconfirmed/inactive criteria are common analyst-entered
                # match strings that were never validated against the real
                # CPE Dictionary (confirmed via the NVD CPE API on real
                # examples — e.g. "zteusa:zxdsl_831" and "dell:elite_slice"
                # both fail this check despite superficially looking like
                # real vendor/product names) and are a major source of
                # spurious vendor/product duplication if imported.
                if not match_string.get("matches") or match_string.get("status") != "Active":
                    unconfirmed += 1
                    continue
                part, vendor_raw, product_raw = parsed
                vendor = unescape_cpe_component(vendor_raw)
                product = unescape_cpe_component(product_raw)
                result.setdefault(vendor, {})[product] = part

    if malformed:
        print(
            f"  {malformed} malformed/unparseable criteria strings skipped",
            file=sys.stderr,
        )
    if unconfirmed:
        print(
            f"  {unconfirmed} unconfirmed criteria strings skipped (no resolved "
            "CPE Dictionary match, or not Active)",
            file=sys.stderr,
        )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result))
    return result


def _reorder(data: dict[str, Any], order: list[str]) -> dict[str, Any]:
    ordered = {k: data[k] for k in order if k in data}
    remainder = {k: v for k, v in data.items() if k not in order}
    return {**ordered, **remainder}


def _nvd_alias_value(data: dict[str, Any]) -> str | None:
    return next((a["value"] for a in data.get("aliases", []) if a.get("source") == "nvd"), None)


def backfill_cpe_fields(
    existing: ExistingData, reduced_map: dict[str, dict[str, str]], *, apply: bool
) -> tuple[int, int]:
    """Set `cpe` on any existing vendor/product whose `nvd` alias exactly
    matches an entry in reduced_map and doesn't already have `cpe` set.
    Returns (vendors_matched, products_matched) — counted whether or not
    `apply` is True; files are only written when `apply=True`."""
    import yaml

    vendors_matched = 0
    products_matched = 0

    for vendor in existing.vendors:
        if vendor.data.get("cpe"):
            continue
        nvd_value = _nvd_alias_value(vendor.data)
        if nvd_value is None or nvd_value not in reduced_map:
            continue
        vendors_matched += 1
        if apply:
            data = dict(vendor.data)
            data["cpe"] = format_cpe_prefix("*", nvd_value)
            vendor.path.write_text(
                yaml.dump(_reorder(data, VENDOR_KEY_ORDER), sort_keys=False, allow_unicode=True)
            )

    for product in existing.products:
        if product.data.get("cpe"):
            continue
        nvd_value = _nvd_alias_value(product.data)
        if nvd_value is None:
            continue
        vendor_entry = existing.vendor_by_id(product.vendor_id)
        if vendor_entry is None:
            continue
        vendor_nvd = _nvd_alias_value(vendor_entry.data)
        if vendor_nvd is None:
            continue
        part = reduced_map.get(vendor_nvd, {}).get(nvd_value)
        if part is None:
            continue
        products_matched += 1
        if apply:
            data = dict(product.data)
            data["cpe"] = format_cpe_prefix(part, vendor_nvd, nvd_value)
            product.path.write_text(
                yaml.dump(_reorder(data, PRODUCT_KEY_ORDER), sort_keys=False, allow_unicode=True)
            )

    return vendors_matched, products_matched


def select_top_vendors(reduced_map: dict[str, dict[str, str]], top_n: int) -> list[str]:
    return sorted(reduced_map, key=lambda v: len(reduced_map[v]), reverse=True)[:top_n]


def create_new_coverage(
    existing: ExistingData,
    reduced_map: dict[str, dict[str, str]],
    top_n: int,
    threshold: int,
) -> tuple[list[NewVendor], list[NewProduct]]:
    vendor_entries = existing.vendor_entries_with_aliases()
    new_vendors: dict[str, NewVendor] = {}
    new_products: list[NewProduct] = []

    for cpe_vendor in select_top_vendors(reduced_map, top_n):
        vendor_res = resolve_against_index(
            cpe_vendor, SOURCE, vendor_entries, existing.vendor_alias_index, threshold, cpe_vendor
        )
        if vendor_res.status == "review":
            continue
        if vendor_res.status == "mapped":
            vendor_id = vendor_res.canonical_id or ""
            vendor_known = True
        else:
            vendor_id = slugify(cpe_vendor)
            if not vendor_id:
                continue
            vendor_known = False

        product_entries = existing.product_entries_with_aliases(vendor_id) if vendor_known else []
        product_alias_index = (
            existing.product_alias_index_for_vendor(vendor_id) if vendor_known else []
        )

        vendor_new_products = []
        # Two distinct reduced-map product strings for this vendor can slugify
        # to the same id (e.g. differing only in case/punctuation) — track
        # what this batch has already created so the second one resolves
        # against the first instead of silently colliding at write time.
        pending_product_slugs: dict[str, str] = {}  # slug -> the cpe_product that claimed it
        pending_alias_records: list[AliasRecord] = []
        for cpe_product, part in reduced_map[cpe_vendor].items():
            product_res = resolve_against_index(
                cpe_product,
                SOURCE,
                product_entries,
                product_alias_index + pending_alias_records,
                threshold,
                cpe_product,
            )
            if product_res.status != "new":
                continue
            product_slug = slugify(cpe_product)
            if not product_slug or product_slug in pending_product_slugs:
                continue
            pending_product_slugs[product_slug] = cpe_product
            pending_alias_records.append(
                AliasRecord(value=cpe_product, canonical_id=f"{vendor_id}/{product_slug}")
            )
            vendor_new_products.append(
                NewProduct(
                    id=product_slug,
                    vendor_id=vendor_id,
                    name=cpe_product.replace("_", " ").title(),
                    type=CPE_PART_TO_TYPE.get(part, "software"),
                    tags=[],
                    aliases=[{"source": SOURCE, "value": cpe_product, "confidence": "auto"}],
                    cpe=format_cpe_prefix(part, cpe_vendor, cpe_product),
                )
            )

        if not vendor_known and vendor_id not in new_vendors and vendor_new_products:
            new_vendors[vendor_id] = NewVendor(
                id=vendor_id,
                name=cpe_vendor.replace("_", " ").title(),
                aliases=[{"source": SOURCE, "value": cpe_vendor, "confidence": "auto"}],
                cpe=format_cpe_prefix("*", cpe_vendor),
            )

        new_products.extend(vendor_new_products)

    return list(new_vendors.values()), new_products



def backfill_purl_fields(existing: ExistingData, *, apply: bool) -> int:
    """Set `purl` on any product with an osv alias whose ecosystem maps to
    a known PURL type and doesn't already have `purl` set. Returns the
    match count regardless of `apply`; only writes when `apply=True`."""
    import yaml

    matched = 0
    for product in existing.products:
        if product.data.get("purl"):
            continue
        osv_alias = next(
            (a for a in product.data.get("aliases", []) if a.get("source") == "osv"), None
        )
        if osv_alias is None or "ecosystem" not in osv_alias:
            continue
        purl = ecosystem_to_purl(osv_alias["ecosystem"], osv_alias["value"])
        if purl is None:
            continue
        matched += 1
        if apply:
            data = dict(product.data)
            data["purl"] = purl
            product.path.write_text(
                yaml.dump(_reorder(data, PRODUCT_KEY_ORDER), sort_keys=False, allow_unicode=True)
            )
    return matched


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="write files (default: dry run)")
    parser.add_argument(
        "--top-n",
        type=int,
        default=1000,
        help="how many not-yet-covered vendors to create, ranked by distinct product count",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="bypass the reduced-map cache and re-parse the tarball",
    )
    parser.add_argument("--threshold", type=int, default=85, help="fuzzy match threshold (0-100)")
    args = parser.parse_args()

    print(
        "Building reduced CPE vendor/product map (first run parses ~3.5GB, "
        "can take a few minutes; cached after)..."
    )
    reduced_map = build_reduced_cpe_map(refresh=args.refresh)
    pair_count = sum(len(products) for products in reduced_map.values())
    print(f"  {len(reduced_map)} unique vendors, {pair_count} unique vendor/product pairs")

    existing = load_existing()

    v_backfilled, p_backfilled = backfill_cpe_fields(existing, reduced_map, apply=args.apply)
    print(f"\ncpe backfill: {v_backfilled} vendor(s), {p_backfilled} product(s) matched"
          + ("" if args.apply else " (dry run — nothing written)"))

    new_vendors, new_products = create_new_coverage(
        existing, reduced_map, args.top_n, args.threshold
    )
    print(f"\nnew coverage (top {args.top_n} vendors by product count): "
          f"{len(new_vendors)} new vendor(s), {len(new_products)} new product(s)")

    purl_matched = backfill_purl_fields(existing, apply=args.apply)
    print(f"\npurl backfill: {purl_matched} product(s) matched"
          + ("" if args.apply else " (dry run — nothing written)"))

    if not args.apply:
        print("\nDry run — no files written. Re-run with --apply.")
        return 0

    print("\nWriting new files...")
    skipped = 0
    for v in new_vendors:
        try:
            path = write_new_vendor(v)
            print(f"  wrote {path.relative_to(REPO_ROOT)}")
        except FileExistsError as exc:
            print(f"  SKIPPED: {exc}")
            skipped += 1
    for p in new_products:
        try:
            path = write_new_product(p)
            print(f"  wrote {path.relative_to(REPO_ROOT)}")
        except FileExistsError as exc:
            print(f"  SKIPPED: {exc}")
            skipped += 1
    if skipped:
        print(f"\n{skipped} file(s) skipped due to path collisions — see above, review manually.")

    print("\nRunning tools/validate.py...")
    import subprocess

    result = subprocess.run(
        ["uv", "run", "tools/validate.py"], cwd=REPO_ROOT, check=False
    )
    if result.returncode != 0:
        print(
            "\nvalidate.py failed — see above. New/backfilled files may be "
            "malformed; review before committing.",
            file=sys.stderr,
        )
        return 1

    print(
        "\nAll entries valid. Before committing, review the new "
        "(confidence: auto) entries, then regenerate data/examples/ "
        "(otherwise CI's data/examples/ freshness check will fail):\n"
        "  generated_at=$(python3 -c \"import json; "
        "print(json.load(open('data/examples/aliases.json'))['generated_at'])\")\n"
        "  uv run tools/build_index.py --output-dir data/examples "
        "--generated-at \"$generated_at\""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
