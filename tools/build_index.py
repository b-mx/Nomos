"""Flatten vendors/ into index/aliases.json and per-source split files.

Also writes one small JSON file per entry under index/entries/<slug>.json —
the search site fetches these individually instead of loading the full
flattened index, so page weight stays flat as the vendor/product count grows.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools._common import REPO_ROOT, iter_products, iter_vendors


def build_entries(vendors_dir: Path = REPO_ROOT / "vendors") -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for vendor in iter_vendors(vendors_dir):
        entries.append(
            {
                "canonical_type": "vendor",
                "slug": vendor.data["id"],
                "vendor_id": vendor.data["id"],
                "name": vendor.data["name"],
                "icon": vendor.data.get("icon"),
                "aliases": vendor.data.get("aliases", []),
            }
        )
    for product in iter_products(vendors_dir):
        entries.append(
            {
                "canonical_type": "product",
                "slug": f"{product.vendor_id}--{product.data['id']}",
                "vendor_id": product.vendor_id,
                "product_id": product.data["id"],
                "name": product.data["name"],
                "type": product.data["type"],
                "tags": product.data.get("tags", []),
                "icon": product.data.get("icon"),
                "services": product.data.get("services", []),
                "aliases": product.data.get("aliases", []),
            }
        )
    return entries


def build_index(
    generated_at: str, vendors_dir: Path = REPO_ROOT / "vendors"
) -> dict[str, Any]:
    return {"generated_at": generated_at, "entries": build_entries(vendors_dir)}


def build_by_source(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        for alias in entry.get("aliases", []):
            source = alias["source"]
            by_source.setdefault(source, []).append(
                {
                    "canonical_type": entry["canonical_type"],
                    "vendor_id": entry["vendor_id"],
                    "product_id": entry.get("product_id"),
                    "name": entry["name"],
                    "alias": alias,
                }
            )
    return by_source


def write_entry_files(output_dir: Path, entries: list[dict[str, Any]]) -> None:
    entries_dir = output_dir / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        (entries_dir / f"{entry['slug']}.json").write_text(json.dumps(entry, indent=2) + "\n")


def build_stats(entries: list[dict[str, Any]]) -> dict[str, Any]:
    vendor_count = sum(1 for e in entries if e["canonical_type"] == "vendor")
    product_count = sum(1 for e in entries if e["canonical_type"] == "product")
    sources = {alias["source"] for e in entries for alias in e.get("aliases", [])}
    return {
        "vendor_count": vendor_count,
        "product_count": product_count,
        "sources": sorted(sources),
    }


def write_index(
    output_dir: Path, generated_at: str, vendors_dir: Path = REPO_ROOT / "vendors"
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    index = build_index(generated_at, vendors_dir)
    (output_dir / "aliases.json").write_text(json.dumps(index, indent=2) + "\n")
    by_source_dir = output_dir / "by-source"
    by_source_dir.mkdir(parents=True, exist_ok=True)
    for source, items in build_by_source(index["entries"]).items():
        (by_source_dir / f"{source}.json").write_text(json.dumps(items, indent=2) + "\n")
    write_entry_files(output_dir, index["entries"])
    stats = build_stats(index["entries"])
    (output_dir / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "index")
    parser.add_argument(
        "--generated-at",
        required=True,
        help="ISO-8601 timestamp, e.g. output of `date -u +%Y-%m-%dT%H:%M:%SZ`",
    )
    args = parser.parse_args()
    write_index(args.output_dir, args.generated_at)
    print(f"Wrote index to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
