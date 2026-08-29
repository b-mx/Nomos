"""Run directly: uv run pytest tests/sources/test_pull_nvd_cpe.py -v

Uses a small synthetic tarball, never the real ~765MB NVD dump.
"""

import json
import re
import tarfile
from pathlib import Path
from typing import Any

import yaml

from tools._common import ProductEntry, VendorEntry
from tools.sources._lib import ExistingData
from tools.sources.pull_nvd_cpe import (
    _reorder,
    backfill_cpe_fields,
    backfill_purl_fields,
    build_reduced_cpe_map,
    create_new_coverage,
    select_top_vendors,
)


def _load_product_cpe_pattern() -> re.Pattern[str]:
    # NOTE: relative to the current working directory, not Path(__file__) —
    # this suite is always invoked as `uv run pytest ...` from the repo root
    # (see the module docstring above), so cwd reliably points there; no
    # need to walk up from this file's own location to find it.
    schema_path = Path("data/schema/product.schema.json")
    schema = json.loads(schema_path.read_text())
    return re.compile(schema["properties"]["cpe"]["pattern"])


PRODUCT_CPE_PATTERN = _load_product_cpe_pattern()


def _confirmed_match_string(criteria: str) -> dict[str, Any]:
    """A real NVD match string that resolved to a concrete CPE Dictionary
    entry — the shape build_reduced_cpe_map now requires to include a
    criteria's vendor/product in the reduced map."""
    return {
        "criteria": criteria,
        "status": "Active",
        "matches": [{"cpeName": criteria, "cpeNameId": "TEST-CONFIRMED-0000"}],
    }


def _unconfirmed_match_string(criteria: str, *, status: str = "Active") -> dict[str, str]:
    """A real NVD match string that never resolved to any CPE Dictionary
    entry (no `matches` array) — confirmed via the real dump to be a
    genuine, common source of spurious vendor/product noise (e.g.
    "zteusa:zxdsl_831", "dell:elite_slice") that must NOT be imported."""
    return {"criteria": criteria, "status": status}


def _make_chunk(match_strings: list[str | dict[str, Any]]) -> dict[str, Any]:
    # Each entry is either a plain criteria string (wrapped as a confirmed,
    # Active match — the common case for tests exercising parsing/dedup
    # logic, not the confirmation filter itself) or a dict already shaped
    # like a matchString body (from _confirmed_match_string /
    # _unconfirmed_match_string, for tests that need to control that).
    entries = [
        {"matchString": m if isinstance(m, dict) else _confirmed_match_string(m)}
        for m in match_strings
    ]
    return {
        "resultsPerPage": len(entries),
        "startIndex": 0,
        "totalResults": len(entries),
        "format": "NVD_CPEMatchString",
        "version": "2.0",
        "timestamp": "2026-08-26T00:00:00.000",
        "matchStrings": entries,
    }


def _make_tarball(tmp_path: Path, chunks: list[dict[str, Any]]) -> Path:
    tarball = tmp_path / "nvdcpematch-2.0.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        for i, chunk in enumerate(chunks, start=1):
            chunk_path = tmp_path / f"chunk-{i:05d}.json"
            chunk_path.write_text(json.dumps(chunk))
            arcname = f"nvdcpematch-2.0-chunks/nvdcpematch-2.0-chunk-{i:05d}.json"
            tar.add(chunk_path, arcname=arcname)
    return tarball


def test_build_reduced_map_dedupes_versions_across_chunks(tmp_path: Path) -> None:
    chunk1 = _make_chunk(
        [
            "cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*",
            "cpe:2.3:a:nmap:nmap:3.30:*:*:*:*:*:*:*",
            "cpe:2.3:h:cisco:asa:9.1:*:*:*:*:*:*:*",
        ]
    )
    chunk2 = _make_chunk(
        [
            "cpe:2.3:a:nmap:nmap:3.31:*:*:*:*:*:*:*",  # same product, another version
            "cpe:2.3:a:nmap:ncat:7.9:*:*:*:*:*:*:*",  # same vendor, different product
        ]
    )
    tarball = _make_tarball(tmp_path, [chunk1, chunk2])
    cache_path = tmp_path / "reduced.json"

    result = build_reduced_cpe_map(tarball, cache_path)

    assert result == {
        "nmap": {"nmap": "a", "ncat": "a"},
        "cisco": {"asa": "h"},
    }


def test_build_reduced_map_handles_escaped_colons(tmp_path: Path) -> None:
    chunk = _make_chunk([r"cpe:2.3:a:mark_stosberg:data\:\:formvalidator:4.49_01:*:*:*:*:*:*:*"])
    tarball = _make_tarball(tmp_path, [chunk])
    cache_path = tmp_path / "reduced.json"

    result = build_reduced_cpe_map(tarball, cache_path)

    assert result == {"mark_stosberg": {"data::formvalidator": "a"}}


def test_build_reduced_map_skips_malformed_criteria(tmp_path: Path) -> None:
    chunk = _make_chunk(["not-a-real-cpe-string", "cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*"])
    tarball = _make_tarball(tmp_path, [chunk])
    cache_path = tmp_path / "reduced.json"

    result = build_reduced_cpe_map(tarball, cache_path)

    assert result == {"nmap": {"nmap": "a"}}


def test_build_reduced_map_skips_unconfirmed_criteria(tmp_path: Path) -> None:
    # Real examples confirmed against NVD's own CPE Dictionary API: neither
    # "zteusa:zxdsl_831" (Active but never resolved to a Dictionary entry)
    # nor "dell:elite_slice" (Inactive, also unresolved — the real criteria
    # is an HP product misattributed to Dell in the raw match feed) should
    # end up in the reduced map, only the confirmed "zte" one.
    chunk = _make_chunk(
        [
            _confirmed_match_string("cpe:2.3:h:zte:zxdsl_831:-:*:*:*:*:*:*:*"),
            _unconfirmed_match_string(
                "cpe:2.3:h:zteusa:zxdsl_831:-:*:*:*:*:*:*:*", status="Active"
            ),
            _unconfirmed_match_string(
                "cpe:2.3:h:dell:elite_slice:-:*:*:*:*:*:*:*", status="Inactive"
            ),
        ]
    )
    tarball = _make_tarball(tmp_path, [chunk])
    cache_path = tmp_path / "reduced.json"

    result = build_reduced_cpe_map(tarball, cache_path)

    assert result == {"zte": {"zxdsl_831": "h"}}


def test_build_reduced_map_uses_cache_when_fresh(tmp_path: Path) -> None:
    chunk = _make_chunk(["cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*"])
    tarball = _make_tarball(tmp_path, [chunk])
    cache_path = tmp_path / "reduced.json"

    first = build_reduced_cpe_map(tarball, cache_path)
    assert cache_path.exists()

    # Overwrite the tarball with different content but an OLDER mtime than
    # the cache — the cache should win.
    import time

    time.sleep(0.01)
    cache_path.write_text(json.dumps({"stale": {"marker": "a"}}))
    cache_path.touch()  # ensure cache mtime > tarball mtime

    second = build_reduced_cpe_map(tarball, cache_path)
    assert second == {"stale": {"marker": "a"}}
    assert second != first


def test_build_reduced_map_raises_clear_error_when_tarball_missing(tmp_path: Path) -> None:
    import pytest

    missing = tmp_path / "does-not-exist.tar.gz"
    with pytest.raises(FileNotFoundError, match="nvd.nist.gov"):
        build_reduced_cpe_map(missing, tmp_path / "reduced.json")


def _write_vendor(vendors_dir: Path, vendor_id: str, nvd_value: str) -> Path:
    vendor_dir = vendors_dir / vendor_id
    vendor_dir.mkdir(parents=True)
    path = vendor_dir / "vendor.yaml"
    path.write_text(
        yaml.dump(
            {
                "id": vendor_id,
                "name": vendor_id.title(),
                "aliases": [{"source": "nvd", "value": nvd_value, "confidence": "curated"}],
            }
        )
    )
    return path


def _write_product(vendors_dir: Path, vendor_id: str, product_id: str, nvd_value: str) -> Path:
    products_dir = vendors_dir / vendor_id / "products"
    products_dir.mkdir(parents=True, exist_ok=True)
    path = products_dir / f"{product_id}.yaml"
    path.write_text(
        yaml.dump(
            {
                "id": product_id,
                "vendor_id": vendor_id,
                "name": product_id.title(),
                "type": "software",
                "tags": [],
                "aliases": [{"source": "nvd", "value": nvd_value, "confidence": "curated"}],
            }
        )
    )
    return path


def _load_existing_data(vendors_dir: Path) -> ExistingData:
    vendors = [
        VendorEntry(path=p, data=yaml.safe_load(p.read_text()))
        for p in sorted(vendors_dir.glob("*/vendor.yaml"))
    ]
    products = []
    for p in sorted(vendors_dir.glob("*/products/*.yaml")):
        data = yaml.safe_load(p.read_text())
        products.append(ProductEntry(path=p, data=data, vendor_id=data["vendor_id"]))
    return ExistingData(
        vendors=vendors,
        products=products,
        vendor_alias_index=[],
        product_alias_index=[],
        known_tags=set(),
    )


def test_backfill_sets_cpe_on_matching_vendor_and_product(tmp_path: Path) -> None:
    vendors_dir = tmp_path / "vendors"
    _write_vendor(vendors_dir, "cisco", "cisco")
    _write_product(vendors_dir, "cisco", "asa", "asa")
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"cisco": {"asa": "h"}}

    v_count, p_count = backfill_cpe_fields(existing, reduced_map, apply=True)

    assert (v_count, p_count) == (1, 1)
    vendor_data = yaml.safe_load((vendors_dir / "cisco" / "vendor.yaml").read_text())
    assert vendor_data["cpe"] == "cpe:2.3:*:cisco:*:*:*:*:*:*:*:*:*"
    product_data = yaml.safe_load((vendors_dir / "cisco" / "products" / "asa.yaml").read_text())
    assert product_data["cpe"] == "cpe:2.3:h:cisco:asa:*:*:*:*:*:*:*:*"


def test_backfill_skips_entries_with_no_nvd_match(tmp_path: Path) -> None:
    vendors_dir = tmp_path / "vendors"
    _write_vendor(vendors_dir, "acme", "acme")
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"cisco": {"asa": "h"}}  # no "acme" in here

    v_count, p_count = backfill_cpe_fields(existing, reduced_map, apply=True)

    assert (v_count, p_count) == (0, 0)
    vendor_data = yaml.safe_load((vendors_dir / "acme" / "vendor.yaml").read_text())
    assert "cpe" not in vendor_data


def test_backfill_does_not_write_when_apply_false(tmp_path: Path) -> None:
    vendors_dir = tmp_path / "vendors"
    _write_vendor(vendors_dir, "cisco", "cisco")
    existing = _load_existing_data(vendors_dir)
    reduced_map: dict[str, dict[str, str]] = {"cisco": {}}

    v_count, p_count = backfill_cpe_fields(existing, reduced_map, apply=False)

    assert v_count == 1
    vendor_data = yaml.safe_load((vendors_dir / "cisco" / "vendor.yaml").read_text())
    assert "cpe" not in vendor_data  # counted the match, but did not write


def test_backfill_never_overwrites_an_existing_cpe(tmp_path: Path) -> None:
    vendors_dir = tmp_path / "vendors"
    path = _write_vendor(vendors_dir, "cisco", "cisco")
    data = yaml.safe_load(path.read_text())
    data["cpe"] = "cpe:2.3:*:cisco:*:*:*:*:*:*:*:*:*"
    path.write_text(yaml.dump(data))
    existing = _load_existing_data(vendors_dir)
    reduced_map: dict[str, dict[str, str]] = {"cisco": {}}

    v_count, _ = backfill_cpe_fields(existing, reduced_map, apply=True)

    assert v_count == 0  # already had cpe, not counted as a new match


def test_select_top_vendors_ranks_by_distinct_product_count() -> None:
    reduced_map = {
        "cisco": {"asa": "h", "ios": "o", "ftd": "h"},
        "nmap": {"nmap": "a"},
        "microsoft": {"windows": "o", "office": "a"},
    }
    assert select_top_vendors(reduced_map, top_n=2) == ["cisco", "microsoft"]


def test_create_new_coverage_creates_vendor_and_products_for_unmapped_vendor(
    tmp_path: Path,
) -> None:
    vendors_dir = tmp_path / "vendors"
    vendors_dir.mkdir()
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"newvendor": {"newproduct": "a"}}

    new_vendors, new_products = create_new_coverage(existing, reduced_map, top_n=10, threshold=85)

    assert len(new_vendors) == 1
    assert new_vendors[0].id == "newvendor"
    assert new_vendors[0].cpe == "cpe:2.3:*:newvendor:*:*:*:*:*:*:*:*:*"
    assert len(new_products) == 1
    assert new_products[0].id == "newproduct"
    assert new_products[0].vendor_id == "newvendor"
    assert new_products[0].type == "software"
    assert new_products[0].cpe == "cpe:2.3:a:newvendor:newproduct:*:*:*:*:*:*:*:*"


def test_create_new_coverage_only_creates_missing_products_for_known_vendor(tmp_path: Path) -> None:
    vendors_dir = tmp_path / "vendors"
    _write_vendor(vendors_dir, "cisco", "cisco")
    _write_product(vendors_dir, "cisco", "asa", "asa")
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"cisco": {"asa": "h", "ios": "o"}}  # asa already exists, ios doesn't

    new_vendors, new_products = create_new_coverage(existing, reduced_map, top_n=10, threshold=85)

    assert new_vendors == []
    assert len(new_products) == 1
    assert new_products[0].id == "ios"
    assert new_products[0].type == "os"


def test_create_new_coverage_dedupes_products_that_slugify_to_the_same_id(tmp_path: Path) -> None:
    # Two distinct reduced-map product strings for the same new vendor can
    # slugify to the same product id (differing only in case/punctuation
    # that slugify() normalizes away) — without pending-dedup within this
    # batch, both would be staged as separate NewProduct entries and the
    # second write would collide with the first (FileExistsError) instead
    # of being resolved as "already covered" up front.
    vendors_dir = tmp_path / "vendors"
    vendors_dir.mkdir()
    existing = _load_existing_data(vendors_dir)
    reduced_map = {"newvendor": {"Foo Bar": "a", "foo-bar": "a"}}

    new_vendors, new_products = create_new_coverage(existing, reduced_map, top_n=10, threshold=85)

    assert len(new_vendors) == 1
    assert len(new_products) == 1
    assert new_products[0].id == "foo-bar"


def test_create_new_coverage_respects_top_n(tmp_path: Path) -> None:
    vendors_dir = tmp_path / "vendors"
    vendors_dir.mkdir()
    existing = _load_existing_data(vendors_dir)
    reduced_map = {
        "big-vendor": {"p1": "a", "p2": "a"},
        "small-vendor": {"p1": "a"},
    }

    new_vendors, _ = create_new_coverage(existing, reduced_map, top_n=1, threshold=85)

    assert [v.id for v in new_vendors] == ["big-vendor"]


def _write_product_with_osv(
    vendors_dir: Path, vendor_id: str, product_id: str, ecosystem: str, value: str
) -> Path:
    products_dir = vendors_dir / vendor_id / "products"
    products_dir.mkdir(parents=True, exist_ok=True)
    path = products_dir / f"{product_id}.yaml"
    path.write_text(
        yaml.dump(
            {
                "id": product_id,
                "vendor_id": vendor_id,
                "name": product_id.title(),
                "type": "library",
                "tags": [],
                "aliases": [
                    {
                        "source": "osv",
                        "value": value,
                        "ecosystem": ecosystem,
                        "confidence": "curated",
                    }
                ],
            }
        )
    )
    return path


def test_backfill_purl_sets_purl_from_osv_alias(tmp_path: Path) -> None:
    vendors_dir = tmp_path / "vendors"
    _write_product_with_osv(vendors_dir, "pytorch", "pytorch", "PyPI", "torch")
    existing = _load_existing_data(vendors_dir)

    count = backfill_purl_fields(existing, apply=True)

    assert count == 1
    data = yaml.safe_load((vendors_dir / "pytorch" / "products" / "pytorch.yaml").read_text())
    assert data["purl"] == "pkg:pypi/torch"


def test_backfill_purl_skips_products_without_osv_alias(tmp_path: Path) -> None:
    vendors_dir = tmp_path / "vendors"
    _write_product(vendors_dir, "cisco", "asa", "asa")  # nvd alias only, no osv
    existing = _load_existing_data(vendors_dir)

    count = backfill_purl_fields(existing, apply=True)

    assert count == 0


def test_backfill_purl_skips_unknown_ecosystem(tmp_path: Path) -> None:
    vendors_dir = tmp_path / "vendors"
    _write_product_with_osv(vendors_dir, "acme", "widget", "SomeNewEcosystem", "widget")
    existing = _load_existing_data(vendors_dir)

    count = backfill_purl_fields(existing, apply=True)

    assert count == 0
    data = yaml.safe_load((vendors_dir / "acme" / "products" / "widget.yaml").read_text())
    assert "purl" not in data


def test_backfill_purl_does_not_write_when_apply_false(tmp_path: Path) -> None:
    vendors_dir = tmp_path / "vendors"
    _write_product_with_osv(vendors_dir, "pytorch", "pytorch", "PyPI", "torch")
    existing = _load_existing_data(vendors_dir)

    count = backfill_purl_fields(existing, apply=False)

    assert count == 1
    data = yaml.safe_load((vendors_dir / "pytorch" / "products" / "pytorch.yaml").read_text())
    assert "purl" not in data


def test_reorder_preserves_keys_not_in_order() -> None:
    data = {"id": "acme", "cpe": "cpe:2.3:*:acme:*:*:*:*:*:*:*:*:*", "future_field": "keep-me"}
    result = _reorder(data, ["id", "name", "cpe"])
    assert result == {
        "id": "acme",
        "cpe": "cpe:2.3:*:acme:*:*:*:*:*:*:*:*:*",
        "future_field": "keep-me",
    }
    assert "future_field" in result


def test_create_new_coverage_produces_schema_valid_cpe_for_escaped_special_char(
    tmp_path: Path,
) -> None:
    r"""End-to-end: a raw NVD criteria with an escaped `+` (confirmed real, e.g. HP's
    "laserjet_m725z\+") flows through build_reduced_cpe_map (unescape) and
    create_new_coverage (re-escape via format_cpe_prefix), and the resulting
    NewProduct.cpe must satisfy data/schema/product.schema.json's own `cpe` pattern."""
    chunk = _make_chunk([r"cpe:2.3:h:hp:laserjet_m725z\+:1.0:*:*:*:*:*:*:*"])
    tarball = _make_tarball(tmp_path, [chunk])
    cache_path = tmp_path / "reduced.json"
    reduced_map = build_reduced_cpe_map(tarball, cache_path)

    vendors_dir = tmp_path / "vendors"
    vendors_dir.mkdir()
    existing = _load_existing_data(vendors_dir)

    _new_vendors, new_products = create_new_coverage(existing, reduced_map, top_n=10, threshold=85)

    assert len(new_products) == 1
    assert new_products[0].cpe == r"cpe:2.3:h:hp:laserjet_m725z\+:*:*:*:*:*:*:*:*"
    assert PRODUCT_CPE_PATTERN.match(new_products[0].cpe)


def test_create_new_coverage_round_trips_escaped_colon_through_format_cpe_prefix(
    tmp_path: Path,
) -> None:
    """Same end-to-end path as above, but for an escaped colon (e.g. "Data::FormValidator").
    escape_cpe_component/format_cpe_prefix correctly re-escape it into a CPE-2.3-valid
    string (Item 1), and data/schema/product.schema.json's `cpe` pattern was updated
    (post-final-review) to accept a backslash-escaped colon inside a component instead
    of treating any `:` as a delimiter — so this now round-trips end to end, including
    real schema validation."""
    chunk = _make_chunk([r"cpe:2.3:a:mark_stosberg:data\:\:formvalidator:4.49_01:*:*:*:*:*:*:*"])
    tarball = _make_tarball(tmp_path, [chunk])
    cache_path = tmp_path / "reduced.json"
    reduced_map = build_reduced_cpe_map(tarball, cache_path)

    vendors_dir = tmp_path / "vendors"
    vendors_dir.mkdir()
    existing = _load_existing_data(vendors_dir)

    _new_vendors, new_products = create_new_coverage(existing, reduced_map, top_n=10, threshold=85)

    assert len(new_products) == 1
    assert new_products[0].cpe == r"cpe:2.3:a:mark_stosberg:data\:\:formvalidator:*:*:*:*:*:*:*:*"
    assert PRODUCT_CPE_PATTERN.match(new_products[0].cpe)
