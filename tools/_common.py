"""Shared helpers for Nomos validation and index-generation tools."""

from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORS_DIR = REPO_ROOT / "data" / "vendors"
TAXONOMY_FILE = REPO_ROOT / "data" / "taxonomy" / "tags.yaml"

KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# libyaml's C extension, when available, is ~5x faster than PyYAML's
# pure-Python SafeLoader for a tree this size — yaml.safe_load() does NOT
# use it automatically, so it must be selected explicitly.
_YAML_LOADER: type[yaml.SafeLoader] = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# Below this many vendor directories, ProcessPoolExecutor's startup cost
# (process spawn + module re-import in each worker) outweighs any benefit —
# matters for the small synthetic trees under tests/fixtures/.
_PARALLEL_THRESHOLD = 32


@dataclass(frozen=True)
class VendorEntry:
    path: Path
    data: dict[str, Any]


@dataclass(frozen=True)
class ProductEntry:
    path: Path
    data: dict[str, Any]
    vendor_id: str


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.load(f, Loader=_YAML_LOADER) or {}


def _load_vendor_file(vendor_dir: Path) -> VendorEntry | None:
    vendor_file = vendor_dir / "vendor.yaml"
    if not vendor_file.exists():
        return None
    return VendorEntry(path=vendor_file, data=load_yaml(vendor_file))


def _load_product_files(vendor_dir: Path) -> list[ProductEntry]:
    products_dir = vendor_dir / "products"
    if not products_dir.is_dir():
        return []
    return [
        ProductEntry(path=product_file, data=load_yaml(product_file), vendor_id=vendor_dir.name)
        for product_file in sorted(products_dir.glob("*.yaml"))
    ]


def _vendor_subdirs(vendors_dir: Path) -> list[Path]:
    return [d for d in sorted(vendors_dir.iterdir()) if d.is_dir()]


def iter_vendors(vendors_dir: Path = VENDORS_DIR) -> list[VendorEntry]:
    if not vendors_dir.is_dir():
        return []
    vendor_dirs = _vendor_subdirs(vendors_dir)
    if len(vendor_dirs) < _PARALLEL_THRESHOLD:
        results = [_load_vendor_file(d) for d in vendor_dirs]
    else:
        with ProcessPoolExecutor(max_workers=os.cpu_count() or 1) as executor:
            # map() preserves input order regardless of completion order,
            # so output stays deterministic (matches vendor_dirs' sort).
            results = list(executor.map(_load_vendor_file, vendor_dirs, chunksize=16))
    return [entry for entry in results if entry is not None]


def iter_products(vendors_dir: Path = VENDORS_DIR) -> list[ProductEntry]:
    if not vendors_dir.is_dir():
        return []
    vendor_dirs = _vendor_subdirs(vendors_dir)
    if len(vendor_dirs) < _PARALLEL_THRESHOLD:
        per_vendor = [_load_product_files(d) for d in vendor_dirs]
    else:
        with ProcessPoolExecutor(max_workers=os.cpu_count() or 1) as executor:
            per_vendor = list(executor.map(_load_product_files, vendor_dirs, chunksize=16))
    products: list[ProductEntry] = []
    for entries in per_vendor:
        products.extend(entries)
    return products


def load_taxonomy_tags(taxonomy_file: Path = TAXONOMY_FILE) -> set[str]:
    data = load_yaml(taxonomy_file)
    return {tag["id"] for tag in data.get("tags", [])}
