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
    load_taxonomy_tags,
    load_yaml,
)

SCHEMA_DIR = REPO_ROOT / "schema"
SERVICES_ALLOWED_TYPES = {"software", "appliance", "os"}


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
    for vendor_entry in vendors:
        for err in vendor_validator.iter_errors(vendor_entry.data):
            errors.append(
                f"{_rel(vendor_entry.path)}: schema error at {list(err.path)}: {err.message}"
            )
    for product_entry in products:
        for err in product_validator.iter_errors(product_entry.data):
            errors.append(
                f"{_rel(product_entry.path)}: schema error at {list(err.path)}: {err.message}"
            )
    return errors


def validate_ids_and_paths(
    vendors: list[VendorEntry], products: list[ProductEntry]
) -> list[str]:
    errors: list[str] = []
    for vendor_entry in vendors:
        vid = vendor_entry.data.get("id", "")
        if not KEBAB_CASE_RE.match(vid):
            errors.append(f"{_rel(vendor_entry.path)}: id '{vid}' is not lowercase kebab-case")
        dir_name = vendor_entry.path.parent.name
        if vid != dir_name:
            errors.append(
                f"{_rel(vendor_entry.path)}: id '{vid}' does not match directory name '{dir_name}'"
            )
    for product_entry in products:
        pid = product_entry.data.get("id", "")
        if not KEBAB_CASE_RE.match(pid):
            errors.append(f"{_rel(product_entry.path)}: id '{pid}' is not lowercase kebab-case")
        if product_entry.path.stem != pid:
            errors.append(
                f"{_rel(product_entry.path)}: id '{pid}' does not match filename "
                f"'{product_entry.path.stem}'"
            )
        declared_vendor_id = product_entry.data.get("vendor_id", "")
        if declared_vendor_id != product_entry.vendor_id:
            errors.append(
                f"{_rel(product_entry.path)}: vendor_id '{declared_vendor_id}' does not match "
                f"parent vendor directory '{product_entry.vendor_id}'"
            )
    return errors


def validate_alias_uniqueness(
    vendors: list[VendorEntry], products: list[ProductEntry]
) -> list[str]:
    errors: list[str] = []
    seen: dict[tuple[str, str, str], Path] = {}
    for vendor_entry in vendors:
        for alias in vendor_entry.data.get("aliases", []):
            key = ("vendor", alias.get("source", ""), alias.get("value", ""))
            if key in seen:
                errors.append(
                    f"Duplicate alias {key} claimed by both "
                    f"{_rel(seen[key])} and {_rel(vendor_entry.path)}"
                )
            else:
                seen[key] = vendor_entry.path
    for product_entry in products:
        for alias in product_entry.data.get("aliases", []):
            key = ("product", alias.get("source", ""), alias.get("value", ""))
            if key in seen:
                errors.append(
                    f"Duplicate alias {key} claimed by both "
                    f"{_rel(seen[key])} and {_rel(product_entry.path)}"
                )
            else:
                seen[key] = product_entry.path
    return errors


def validate_tags_exist(products: list[ProductEntry]) -> list[str]:
    errors: list[str] = []
    known_tags = load_taxonomy_tags()
    for entry in products:
        for tag in entry.data.get("tags", []):
            if tag not in known_tags:
                errors.append(
                    f"{_rel(entry.path)}: unknown tag '{tag}' not in taxonomy/tags.yaml"
                )
    return errors


def validate_services_allowed(products: list[ProductEntry]) -> list[str]:
    errors: list[str] = []
    for entry in products:
        if "services" in entry.data and entry.data.get("type") not in SERVICES_ALLOWED_TYPES:
            errors.append(
                f"{_rel(entry.path)}: 'services' is not allowed on type "
                f"'{entry.data.get('type')}' (only software, appliance, os)"
            )
    return errors


def validate_taxonomy(taxonomy_file: Path = REPO_ROOT / "taxonomy" / "tags.yaml") -> list[str]:
    errors: list[str] = []
    data = load_yaml(taxonomy_file)
    schema = load_schema("taxonomy.schema.json")
    validator = jsonschema.Draft202012Validator(schema)
    for err in validator.iter_errors(data):
        errors.append(f"{_rel(taxonomy_file)}: schema error at {list(err.path)}: {err.message}")
    seen_ids: set[str] = set()
    for tag in data.get("tags", []):
        tag_id = tag.get("id", "")
        if tag_id in seen_ids:
            errors.append(f"{_rel(taxonomy_file)}: duplicate tag id '{tag_id}'")
        seen_ids.add(tag_id)
    return errors


def validate_directory_structure(vendors_dir: Path = REPO_ROOT / "vendors") -> list[str]:
    errors: list[str] = []
    if not vendors_dir.is_dir():
        return errors
    for entry in sorted(vendors_dir.iterdir()):
        if not entry.is_dir():
            errors.append(
                f"{_rel(entry)}: unexpected file directly under vendors/ "
                "(only vendor directories are allowed)"
            )
            continue
        vendor_file = entry / "vendor.yaml"
        if not vendor_file.exists():
            errors.append(f"{_rel(entry)}/: missing vendor.yaml")
        allowed_children = {"vendor.yaml", "products"}
        for child in entry.iterdir():
            if child.name not in allowed_children:
                errors.append(
                    f"{_rel(child)}: unexpected file/directory in a vendor "
                    "directory (only vendor.yaml and products/ are allowed)"
                )
        products_dir = entry / "products"
        if products_dir.is_dir():
            for child in products_dir.iterdir():
                if not (child.is_file() and child.suffix == ".yaml"):
                    errors.append(
                        f"{_rel(child)}: unexpected entry in products/ "
                        "(only *.yaml files are allowed)"
                    )
    return errors


def validate_vendor_references(
    vendors: list[VendorEntry], products: list[ProductEntry]
) -> list[str]:
    errors: list[str] = []
    known_ids = {v.data.get("id") for v in vendors}
    for entry in products:
        if entry.vendor_id not in known_ids:
            errors.append(
                f"{_rel(entry.path)}: vendor_id '{entry.vendor_id}' has no "
                f"corresponding vendors/{entry.vendor_id}/vendor.yaml"
            )
    return errors


def run_all_checks(vendors_dir: Path = REPO_ROOT / "vendors") -> list[str]:
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    errors: list[str] = []
    errors += validate_schema_conformance(vendors, products)
    errors += validate_ids_and_paths(vendors, products)
    errors += validate_alias_uniqueness(vendors, products)
    errors += validate_tags_exist(products)
    errors += validate_services_allowed(products)
    errors += validate_directory_structure(vendors_dir)
    errors += validate_vendor_references(vendors, products)
    errors += validate_taxonomy()
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
