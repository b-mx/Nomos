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
