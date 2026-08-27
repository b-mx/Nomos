from pathlib import Path

from tools._common import iter_products, iter_vendors

FIXTURES = Path(__file__).parent / "fixtures"


def test_valid_tree_has_no_schema_or_id_errors():
    from tools.validate import validate_ids_and_paths, validate_schema_conformance

    vendors_dir = FIXTURES / "valid_tree" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    assert validate_schema_conformance(vendors, products) == []
    assert validate_ids_and_paths(vendors, products) == []


def test_duplicate_alias_is_caught_with_both_paths_named():
    from tools.validate import validate_alias_uniqueness

    vendors_dir = FIXTURES / "invalid_duplicate_alias" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    errors = validate_alias_uniqueness(vendors, products)
    assert len(errors) == 1
    assert "acme-one" in errors[0]
    assert "acme-two" in errors[0]


def test_unknown_tag_is_rejected():
    from tools.validate import validate_tags_exist

    vendors_dir = FIXTURES / "invalid_unknown_tag" / "vendors"
    products = iter_products(vendors_dir)
    errors = validate_tags_exist(products)
    assert len(errors) == 1
    assert "definitely-not-a-real-tag" in errors[0]


def test_services_rejected_on_library():
    from tools.validate import validate_services_allowed

    vendors_dir = FIXTURES / "invalid_services_on_library" / "vendors"
    products = iter_products(vendors_dir)
    errors = validate_services_allowed(products)
    assert len(errors) == 1
    assert "library" in errors[0]


def test_self_vendored_validates_normally():
    from tools.validate import (
        validate_alias_uniqueness,
        validate_schema_conformance,
        validate_services_allowed,
        validate_tags_exist,
    )

    vendors_dir = FIXTURES / "self_vendored" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    assert vendors[0].data["id"] == products[0].vendor_id == products[0].data["id"]
    assert validate_schema_conformance(vendors, products) == []
    assert validate_alias_uniqueness(vendors, products) == []
    assert validate_tags_exist(products) == []
    assert validate_services_allowed(products) == []
