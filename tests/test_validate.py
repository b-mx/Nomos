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
        "invalid_domain_and_sources",
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


def _product(**overrides):
    data = {
        "id": "widget",
        "vendor_id": "acme",
        "name": "Acme Widget",
        "type": "software",
        "tags": [],
        "aliases": [{"source": "nvd", "value": "widget", "confidence": "curated"}],
    }
    data.update(overrides)
    return data


def _schema_errors(schema_name, data):
    import jsonschema

    from tools.validate import load_schema

    validator = jsonschema.Draft202012Validator(load_schema(schema_name))
    return list(validator.iter_errors(data))


def test_domain_and_banner_name_sources_valid_fixture():
    from tools.validate import validate_alias_uniqueness, validate_schema_conformance

    vendors_dir = FIXTURES / "valid_domain_and_sources" / "vendors"
    vendors = iter_vendors(vendors_dir)
    products = iter_products(vendors_dir)
    assert validate_schema_conformance(vendors, products) == []
    assert validate_alias_uniqueness(vendors, products) == []
    assert run_all_checks(vendors_dir=vendors_dir) == []


def test_domain_and_banner_name_sources_invalid_fixture():
    from tools.validate import validate_schema_conformance

    vendors_dir = FIXTURES / "invalid_domain_and_sources" / "vendors"
    errors = validate_schema_conformance(iter_vendors(vendors_dir), iter_products(vendors_dir))
    # vendor: bad domain, banner source not allowed on vendors;
    # product: bad domain, versioned banner, badly spaced name.
    assert len(errors) == 5, errors
    assert sum("vendor.yaml" in e for e in errors) == 2
    assert sum("widget.yaml" in e for e in errors) == 3


@pytest.mark.parametrize(
    "domain",
    ["nginx.org", "example.co.uk", "xn--bcher-kva.example", "example.xn--p1ai", "a-b.io"],
)
def test_domain_accepts_registrable_lowercase_domains(domain):
    assert _schema_errors("product.schema.json", _product(domain=domain)) == []
    assert _schema_errors(
        "vendor.schema.json", {"id": "acme", "name": "Acme", "domain": domain, "aliases": []}
    ) == []


@pytest.mark.parametrize(
    "domain",
    [
        "https://nginx.org",
        "nginx.org/",
        "nginx.org/en/",
        "nginx.org:443",
        "NGINX.org",
        "nginx",
        "-nginx.org",
        "nginx-.org",
        "nginx..org",
        "nginx.org.",
        "bücher.example",
        "user@nginx.org",
        " nginx.org",
        "nginx.org\n",
    ],
)
def test_domain_rejects_scheme_path_port_case_and_unicode(domain):
    assert len(_schema_errors("product.schema.json", _product(domain=domain))) == 1
    assert len(
        _schema_errors(
            "vendor.schema.json", {"id": "acme", "name": "Acme", "domain": domain, "aliases": []}
        )
    ) == 1


@pytest.mark.parametrize(
    "value",
    ["openresty/*", "openssh_*", "nginx", "microsoft-iis/*", "vsftpd *", "3proxy", "php/*"],
)
def test_banner_alias_accepts_normalized_values(value):
    alias = {"source": "banner", "value": value, "confidence": "curated"}
    assert _schema_errors("product.schema.json", _product(aliases=[alias])) == []


@pytest.mark.parametrize(
    "value",
    [
        "openresty/1.21.4.1",
        "openssh_9.6p1",
        "vsftpd 3.0.3",
        "OpenSSH_*",
        "nginx/* (ubuntu)",
        "nginx  *",
        " nginx/*",
        "nginx/*\n",
    ],
)
def test_banner_alias_rejects_versions_case_and_extra_text(value):
    alias = {"source": "banner", "value": value, "confidence": "curated"}
    assert len(_schema_errors("product.schema.json", _product(aliases=[alias]))) == 1


@pytest.mark.parametrize("value", ["NGINX", "OpenSSH", "Microsoft Exchange Server", "Node.js"])
def test_name_alias_accepts_names_as_commonly_written(value):
    alias = {"source": "name", "value": value, "confidence": "curated"}
    assert _schema_errors("product.schema.json", _product(aliases=[alias])) == []


@pytest.mark.parametrize(
    "value", [" NGINX", "NGINX ", "NGINX\n", "Microsoft  Exchange", "Open\tSSH"]
)
def test_name_alias_rejects_stray_whitespace(value):
    alias = {"source": "name", "value": value, "confidence": "curated"}
    assert len(_schema_errors("product.schema.json", _product(aliases=[alias]))) == 1


@pytest.mark.parametrize("source", ["banner", "name"])
def test_banner_and_name_sources_are_product_only(source):
    vendor = {
        "id": "acme",
        "name": "Acme",
        "aliases": [{"source": source, "value": "acme", "confidence": "curated"}],
    }
    assert len(_schema_errors("vendor.schema.json", vendor)) == 1


@pytest.mark.parametrize("source", ["banner", "name"])
def test_banner_and_name_aliases_are_unique_across_vendors(source):
    from tools._common import ProductEntry
    from tools.validate import validate_alias_uniqueness

    alias = {"source": source, "value": "widget", "confidence": "curated"}
    products = [
        ProductEntry(Path("data/vendors/acme/products/widget.yaml"), {"aliases": [alias]}, "acme"),
        ProductEntry(Path("data/vendors/zeta/products/widget.yaml"), {"aliases": [alias]}, "zeta"),
    ]
    errors = validate_alias_uniqueness([], products)
    assert len(errors) == 1
    assert "acme" in errors[0]
    assert "zeta" in errors[0]
