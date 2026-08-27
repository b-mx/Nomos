"""Validate every vendor and product YAML file in the vendors/ tree."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

from tools._common import (
    KEBAB_CASE_RE,
    REPO_ROOT,
    ProductEntry,
    VendorEntry,
    iter_products,
    iter_vendors,
)

SCHEMA_DIR = REPO_ROOT / "schema"


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def load_schema(name: str) -> dict[str, Any]:
    with (SCHEMA_DIR / name).open("r", encoding="utf-8") as f:
        result: dict[str, Any] = json.load(f)
        return result


def validate_schema_conformance(
    vendors: list[VendorEntry], products: list[ProductEntry]
) -> list[str]:
    errors: list[str] = []
    vendor_schema = load_schema("vendor.schema.json")
    product_schema = load_schema("product.schema.json")
    vendor_validator = jsonschema.Draft202012Validator(vendor_schema)
    product_validator = jsonschema.Draft202012Validator(product_schema)
    for entry in vendors:
        for err in vendor_validator.iter_errors(entry.data):
            errors.append(f"{_rel(entry.path)}: schema error at {list(err.path)}: {err.message}")
    for entry in products:
        for err in product_validator.iter_errors(entry.data):
            errors.append(f"{_rel(entry.path)}: schema error at {list(err.path)}: {err.message}")
    return errors


def validate_ids_and_paths(
    vendors: list[VendorEntry], products: list[ProductEntry]
) -> list[str]:
    errors: list[str] = []
    for entry in vendors:
        vid = entry.data.get("id", "")
        if not KEBAB_CASE_RE.match(vid):
            errors.append(f"{_rel(entry.path)}: id '{vid}' is not lowercase kebab-case")
        dir_name = entry.path.parent.name
        if vid != dir_name:
            errors.append(
                f"{_rel(entry.path)}: id '{vid}' does not match directory name '{dir_name}'"
            )
    for entry in products:
        pid = entry.data.get("id", "")
        if not KEBAB_CASE_RE.match(pid):
            errors.append(f"{_rel(entry.path)}: id '{pid}' is not lowercase kebab-case")
        if entry.path.stem != pid:
            errors.append(
                f"{_rel(entry.path)}: id '{pid}' does not match filename '{entry.path.stem}'"
            )
        declared_vendor_id = entry.data.get("vendor_id", "")
        if declared_vendor_id != entry.vendor_id:
            errors.append(
                f"{_rel(entry.path)}: vendor_id '{declared_vendor_id}' does not match "
                f"parent vendor directory '{entry.vendor_id}'"
            )
    return errors


def validate_alias_uniqueness(
    vendors: list[VendorEntry], products: list[ProductEntry]
) -> list[str]:
    errors: list[str] = []
    seen: dict[tuple[str, str], Path] = {}
    for entry in [*vendors, *products]:
        for alias in entry.data.get("aliases", []):
            key = (alias.get("source", ""), alias.get("value", ""))
            if key in seen:
                errors.append(
                    f"Duplicate alias {key} claimed by both "
                    f"{_rel(seen[key])} and {_rel(entry.path)}"
                )
            else:
                seen[key] = entry.path
    return errors


def run_all_checks(vendors_dir: Path = REPO_ROOT / "vendors") -> list[str]:
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    errors: list[str] = []
    errors += validate_schema_conformance(vendors, products)
    errors += validate_ids_and_paths(vendors, products)
    errors += validate_alias_uniqueness(vendors, products)
    return errors


def main() -> int:
    errors = run_all_checks()
    if errors:
        print(f"Found {len(errors)} validation error(s):\n", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("All vendor and product entries are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
