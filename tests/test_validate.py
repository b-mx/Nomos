from pathlib import Path

import pytest

from tools._common import iter_products, iter_vendors
from tools.validate import run_all_checks

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


def test_product_alias_uniqueness_is_scoped_per_vendor():
    # CPE's own uniqueness guarantee is the (vendor, product) pair, not the
    # product name alone — two different, unrelated real vendors legitimately
    # sharing a product-name fragment (e.g. both having a "chat" product) is
    # not a data error, and must not be flagged as one.
    from pathlib import Path

    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness

    products = [
        ProductEntry(
            path=Path("data/vendors/synology/products/chat.yaml"),
            data={"aliases": [{"source": "nvd", "value": "chat", "confidence": "auto"}]},
            vendor_id="synology",
        ),
        ProductEntry(
            path=Path("data/vendors/zoom/products/chat.yaml"),
            data={"aliases": [{"source": "nvd", "value": "chat", "confidence": "auto"}]},
            vendor_id="zoom",
        ),
    ]
    assert validate_alias_uniqueness([], products) == []


def test_product_alias_uniqueness_still_catches_same_vendor_collision():
    from pathlib import Path

    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness

    products = [
        ProductEntry(
            path=Path("data/vendors/acme/products/widget.yaml"),
            data={"aliases": [{"source": "nvd", "value": "thing", "confidence": "auto"}]},
            vendor_id="acme",
        ),
        ProductEntry(
            path=Path("data/vendors/acme/products/gadget.yaml"),
            data={"aliases": [{"source": "nvd", "value": "thing", "confidence": "auto"}]},
            vendor_id="acme",
        ),
    ]
    errors = validate_alias_uniqueness([], products)
    assert len(errors) == 1
    assert "widget.yaml" in errors[0]
    assert "gadget.yaml" in errors[0]


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


def test_vendor_and_product_can_share_alias_across_canonical_types():
    from tools.validate import validate_alias_uniqueness

    vendors_dir = FIXTURES / "self_vendored" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    # Vendor and product both declare (osv, widgetlib) — legal because their
    # canonical_type differs.
    assert vendors[0].data["aliases"][0]["value"] == "widgetlib"
    assert products[0].data["aliases"][0]["value"] == "widgetlib"
    assert validate_alias_uniqueness(vendors, products) == []


def test_orphan_product_without_vendor_yaml_is_rejected():
    from tools.validate import validate_directory_structure, validate_vendor_references

    vendors_dir = FIXTURES / "invalid_orphan_product" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    structure_errors = validate_directory_structure(vendors_dir)
    reference_errors = validate_vendor_references(vendors, products)
    assert any("missing vendor.yaml" in e for e in structure_errors)
    assert any("has no corresponding" in e for e in reference_errors)


def test_stray_non_yaml_file_in_products_is_rejected():
    from tools.validate import validate_directory_structure

    vendors_dir = FIXTURES / "invalid_stray_file" / "vendors"
    errors = validate_directory_structure(vendors_dir)
    assert any("unexpected entry in products/" in e for e in errors)


def test_duplicate_taxonomy_tag_id_is_rejected():
    from tools.validate import validate_taxonomy

    errors = validate_taxonomy(FIXTURES / "invalid_duplicate_tag" / "taxonomy.yaml")
    assert any("duplicate tag id 'webserver'" in e for e in errors)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "invalid_duplicate_alias",
        "invalid_unknown_tag",
        "invalid_services_on_library",
        "invalid_orphan_product",
        "invalid_stray_file",
    ],
)
def test_run_all_checks_catches_every_invalid_fixture(fixture_name):
    vendors_dir = FIXTURES / fixture_name / "vendors"
    errors = run_all_checks(vendors_dir=vendors_dir)
    assert errors, f"run_all_checks() found no errors in {fixture_name}, but it should have"


def test_run_all_checks_includes_taxonomy_validation():
    # A syntactically-broken taxonomy file should surface through
    # run_all_checks() even though the real taxonomy/tags.yaml is valid —
    # this just confirms validate_taxonomy() is actually called, not that
    # the real file passes (that's covered by validate.py's normal green run).
    import inspect

    source = inspect.getsource(run_all_checks)
    assert "validate_taxonomy" in source, "run_all_checks() must call validate_taxonomy()"


def test_cpe_field_accepts_well_formed_prefix():
    from tools.validate import validate_schema_conformance

    vendors_dir = FIXTURES / "valid_tree" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    assert validate_schema_conformance(vendors, products) == []


def test_cpe_field_rejects_malformed_string():
    import jsonschema

    from tools.validate import load_schema

    schema = load_schema("product.schema.json")
    data = {
        "id": "widget",
        "vendor_id": "acme",
        "name": "Acme Widget",
        "type": "software",
        "tags": [],
        "cpe": "not-a-cpe-string",
        "aliases": [{"source": "nvd", "value": "widget", "confidence": "curated"}],
    }
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(data))
    assert len(errors) == 1


def test_purl_field_rejects_malformed_string():
    import jsonschema

    from tools.validate import load_schema

    schema = load_schema("product.schema.json")
    data = {
        "id": "widget",
        "vendor_id": "acme",
        "name": "Acme Widget",
        "type": "software",
        "tags": [],
        "purl": "not-a-purl",
        "aliases": [{"source": "nvd", "value": "widget", "confidence": "curated"}],
    }
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(data))
    assert len(errors) == 1


def test_cpe_field_accepts_escaped_colon_in_product_name():
    # Real-world case: a product like "Data::FormValidator" (a Perl module)
    # has a literal colon in its CPE 2.3 name, escaped as `\:` per CPE 2.3
    # formatted-string binding rules. The pattern must accept a backslash-
    # escaped colon inside a component without treating it as a delimiter.
    import jsonschema

    from tools.validate import load_schema

    schema = load_schema("product.schema.json")
    data = {
        "id": "data-formvalidator",
        "vendor_id": "mark-stosberg",
        "name": "Data::FormValidator",
        "type": "library",
        "tags": [],
        "cpe": r"cpe:2.3:a:mark_stosberg:data\:\:formvalidator:*:*:*:*:*:*:*:*",
        "aliases": [{"source": "nvd", "value": "data::formvalidator", "confidence": "auto"}],
    }
    validator = jsonschema.Draft202012Validator(schema)
    assert list(validator.iter_errors(data)) == []


def test_cpe_field_still_rejects_unescaped_colon_as_delimiter():
    # A bare (unescaped) colon inside a component must still be rejected —
    # it would otherwise be indistinguishable from an extra CPE token.
    import jsonschema

    from tools.validate import load_schema

    schema = load_schema("product.schema.json")
    data = {
        "id": "widget",
        "vendor_id": "acme",
        "name": "Acme Widget",
        "type": "software",
        "tags": [],
        "cpe": "cpe:2.3:a:acme:wid:get:*:*:*:*:*:*:*:*",
        "aliases": [{"source": "nvd", "value": "widget", "confidence": "curated"}],
    }
    validator = jsonschema.Draft202012Validator(schema)
    assert len(list(validator.iter_errors(data))) == 1


def test_normalize_key_part_collapses_whitespace_and_preserves_case() -> None:
    from tools._common import normalize_key_part

    assert normalize_key_part("  Apple  ") == "Apple"
    assert normalize_key_part("iOS,\tiPadOS,  and   watchOS") == "iOS, iPadOS, and watchOS"
    # Case is deliberately preserved: CISA KEV publishes both of these as
    # distinct product strings, and folding them would merge two keys.
    assert normalize_key_part("IOS Software") != normalize_key_part("IOS software")


def test_normalized_product_alias_collision_is_caught_within_a_vendor() -> None:
    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness_normalized

    products = [
        ProductEntry(
            path=Path("data/vendors/cisco/products/ios.yaml"),
            data={
                "aliases": [{"source": "cisa_kev", "value": "IOS Software", "confidence": "auto"}]
            },
            vendor_id="cisco",
        ),
        ProductEntry(
            path=Path("data/vendors/cisco/products/ios-dup.yaml"),
            data={
                "aliases": [{"source": "cisa_kev", "value": "IOS  Software", "confidence": "auto"}]
            },
            vendor_id="cisco",
        ),
    ]
    errors = validate_alias_uniqueness_normalized([], products)
    assert len(errors) == 1
    assert "ios.yaml" in errors[0]
    assert "ios-dup.yaml" in errors[0]


def test_normalized_alias_check_preserves_case_distinction() -> None:
    # 'IOS Software' and 'IOS software' are two real, distinct CISA KEV
    # product strings. They must NOT be reported as a collision.
    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness_normalized

    products = [
        ProductEntry(
            path=Path("data/vendors/cisco/products/ios-software.yaml"),
            data={
                "aliases": [{"source": "cisa_kev", "value": "IOS Software", "confidence": "auto"}]
            },
            vendor_id="cisco",
        ),
        ProductEntry(
            path=Path("data/vendors/cisco/products/ios-software-lower.yaml"),
            data={
                "aliases": [{"source": "cisa_kev", "value": "IOS software", "confidence": "auto"}]
            },
            vendor_id="cisco",
        ),
    ]
    assert validate_alias_uniqueness_normalized([], products) == []


def test_normalized_alias_check_ignores_non_api_eligible_sources() -> None:
    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness_normalized

    products = [
        ProductEntry(
            path=Path("data/vendors/cisco/products/a.yaml"),
            data={"aliases": [{"source": "nvd", "value": "ios software", "confidence": "auto"}]},
            vendor_id="cisco",
        ),
        ProductEntry(
            path=Path("data/vendors/cisco/products/b.yaml"),
            data={"aliases": [{"source": "nvd", "value": "ios  software", "confidence": "auto"}]},
            vendor_id="cisco",
        ),
    ]
    assert validate_alias_uniqueness_normalized([], products) == []


def test_normalized_product_alias_collision_is_scoped_per_vendor() -> None:
    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness_normalized

    products = [
        ProductEntry(
            path=Path("data/vendors/synology/products/chat.yaml"),
            data={"aliases": [{"source": "cisa_kev", "value": "Chat", "confidence": "auto"}]},
            vendor_id="synology",
        ),
        ProductEntry(
            path=Path("data/vendors/zoom/products/chat.yaml"),
            data={"aliases": [{"source": "cisa_kev", "value": "Chat", "confidence": "auto"}]},
            vendor_id="zoom",
        ),
    ]
    assert validate_alias_uniqueness_normalized([], products) == []


def test_normalized_vendor_alias_collision_is_global() -> None:
    from tools._common import VendorEntry
    from tools.validate import validate_alias_uniqueness_normalized

    vendors = [
        VendorEntry(
            path=Path("data/vendors/acme-one/vendor.yaml"),
            data={"aliases": [{"source": "cisa_kev", "value": "Acme Corp", "confidence": "auto"}]},
        ),
        VendorEntry(
            path=Path("data/vendors/acme-two/vendor.yaml"),
            data={
                "aliases": [{"source": "cisa_kev", "value": "Acme   Corp", "confidence": "auto"}]
            },
        ),
    ]
    errors = validate_alias_uniqueness_normalized(vendors, [])
    assert len(errors) == 1
    assert "acme-one" in errors[0]
    assert "acme-two" in errors[0]
