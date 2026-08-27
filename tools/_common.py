"""Shared helpers for Nomos validation and index-generation tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORS_DIR = REPO_ROOT / "vendors"
TAXONOMY_FILE = REPO_ROOT / "taxonomy" / "tags.yaml"

KEBAB_CASE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


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
        return yaml.safe_load(f) or {}


def iter_vendors(vendors_dir: Path = VENDORS_DIR) -> list[VendorEntry]:
    entries: list[VendorEntry] = []
    if not vendors_dir.is_dir():
        return entries
    for vendor_dir in sorted(vendors_dir.iterdir()):
        if not vendor_dir.is_dir():
            continue
        vendor_file = vendor_dir / "vendor.yaml"
        if vendor_file.exists():
            entries.append(VendorEntry(path=vendor_file, data=load_yaml(vendor_file)))
    return entries


def iter_products(vendors_dir: Path = VENDORS_DIR) -> list[ProductEntry]:
    entries: list[ProductEntry] = []
    if not vendors_dir.is_dir():
        return entries
    for vendor_dir in sorted(vendors_dir.iterdir()):
        if not vendor_dir.is_dir():
            continue
        products_dir = vendor_dir / "products"
        if not products_dir.is_dir():
            continue
        for product_file in sorted(products_dir.glob("*.yaml")):
            entries.append(
                ProductEntry(
                    path=product_file,
                    data=load_yaml(product_file),
                    vendor_id=vendor_dir.name,
                )
            )
    return entries


def load_taxonomy_tags(taxonomy_file: Path = TAXONOMY_FILE) -> set[str]:
    data = load_yaml(taxonomy_file)
    return {tag["id"] for tag in data.get("tags", [])}
