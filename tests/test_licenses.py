from pathlib import Path

import jsonschema
import pytest

from tools._common import iter_products, iter_vendors
from tools.validate import (
    load_schema,
    run_all_checks,
    spdx_errors,
    validate_licenses,
    validate_schema_conformance,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _tree(name):
    vendors_dir = FIXTURES / name / "vendors"
    return vendors_dir, iter_vendors(vendors_dir), iter_products(vendors_dir)


def test_valid_licenses_fixture():
    vendors_dir, vendors, products = _tree("valid_licenses")
    assert len(products[0].data["licenses"]) == 3
    assert validate_schema_conformance(vendors, products) == []
    assert validate_licenses(products) == []
    assert run_all_checks(vendors_dir=vendors_dir) == []


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("invalid_licenses_bare_spdx", "RSALv2"),
        ("invalid_licenses_overlap", "overlap"),
        ("invalid_licenses_http_evidence", "evidence"),
        ("invalid_licenses_vers_pypi", "vers:generic"),
    ],
)
def test_invalid_licenses_fixtures(fixture, expected):
    vendors_dir, _, _ = _tree(fixture)
    errors = run_all_checks(vendors_dir=vendors_dir)
    assert len(errors) >= 1
    assert all("widget.yaml" in e for e in errors), errors
    assert any(expected in e for e in errors), errors


@pytest.mark.parametrize(
    "expression",
    [
        "MIT",
        "LicenseRef-RSALv2 OR SSPL-1.0",
        "AGPL-3.0-only OR LicenseRef-RSALv2 OR SSPL-1.0",
        "(MIT AND (GPL-2.0-only OR BSD-3-Clause))",
        "GPL-2.0-only WITH Classpath-exception-2.0",
        "Apache-2.0 WITH LLVM-exception AND LicenseRef-a.b-1",
    ],
)
def test_valid_spdx(expression):
    assert spdx_errors(expression) == []


@pytest.mark.parametrize(
    "expression",
    [
        "",
        " MIT",
        "GPL-2.0+",  # write GPL-2.0-or-later
        "RSALv2",  # bare: not on the SPDX list
        "mit",  # id as listed is MIT
        "MIT or Apache-2.0",  # operators are uppercase
        "MIT AND",
        "MIT OR",
        "(MIT",
        "MIT Apache-2.0",
        "MIT AND NOPE",
        "GPL-2.0-only WITH Foo-exception",
        "MIT WITH LicenseRef-x",  # a LicenseRef is never an exception
        "Classpath-exception-2.0",  # an exception on its own
        "LicenseRef-",
        "LicenseRef-a_b",
        "DocumentRef-x:LicenseRef-y",
        "LicenseRef-x+",
    ],
)
def test_invalid_spdx(expression):
    assert spdx_errors(expression), expression


def _product(licenses):
    return {
        "id": "widget",
        "vendor_id": "acme",
        "name": "Acme Widget",
        "type": "software",
        "tags": [],
        "aliases": [{"source": "nvd", "value": "widget", "confidence": "curated"}],
        "licenses": licenses,
    }


def _schema_errors(licenses):
    validator = jsonschema.Draft202012Validator(load_schema("product.schema.json"))
    return list(validator.iter_errors(_product(licenses)))


def test_schema_accepts_minimal_and_full_license():
    ok = {"spdx": "MIT", "evidence": "https://example.com/COPYING"}
    assert _schema_errors([ok]) == []
    assert _schema_errors([{**ok, "versions": "vers:generic/<1", "confidence": "auto"}]) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"spdx": "MIT"},
        {"evidence": "https://example.com/x"},
        {"spdx": "MIT", "evidence": "http://example.com/x"},
        {"spdx": "MIT", "evidence": "https://"},
        {"spdx": "MIT", "evidence": "https://example.com/x\n"},
        {"spdx": "MIT", "evidence": "https://example.com/x", "versions": "vers:pypi/<1"},
        {"spdx": "MIT", "evidence": "https://example.com/x", "confidence": "guess"},
        {"spdx": "MIT", "evidence": "https://example.com/x", "note": "x"},
        {"spdx": "", "evidence": "https://example.com/x"},
    ],
)
def test_schema_rejects_malformed_license(bad):
    assert _schema_errors([bad])


def test_schema_rejects_empty_licenses_array():
    assert _schema_errors([])


def _entries(licenses):
    from tools._common import ProductEntry

    return [ProductEntry(path=Path("data/vendors/acme/products/widget.yaml"),
                         data=_product(licenses), vendor_id="acme")]


def test_period_without_versions_only_when_alone():
    base = {"spdx": "MIT", "evidence": "https://example.com/x"}
    assert validate_licenses(_entries([base])) == []
    errors = validate_licenses(_entries([base, {**base, "versions": "vers:generic/>=2"}]))
    assert len(errors) == 1 and "no versions" in errors[0]


def test_adjacent_periods_do_not_overlap():
    base = {"spdx": "MIT", "evidence": "https://example.com/x"}
    periods = [
        {**base, "versions": "vers:generic/<2.0"},
        {**base, "versions": "vers:generic/>=2.0"},
    ]
    assert validate_licenses(_entries(periods)) == []
