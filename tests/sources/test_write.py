"""Run directly: uv run pytest tests/sources/test_write.py -v"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from tools.sources import _lib


@pytest.fixture
def isolated_vendors_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    vendors_dir = tmp_path / "vendors"
    vendors_dir.mkdir()
    monkeypatch.setattr(_lib, "VENDORS_DIR", vendors_dir)
    yield vendors_dir
    shutil.rmtree(vendors_dir, ignore_errors=True)


def test_write_new_vendor_includes_cpe_when_set(isolated_vendors_dir: Path) -> None:
    v = _lib.NewVendor(
        id="acme",
        name="Acme Corp",
        aliases=[{"source": "nvd", "value": "acme", "confidence": "auto"}],
        cpe="cpe:2.3:*:acme:*:*:*:*:*:*:*:*:*",
    )
    path = _lib.write_new_vendor(v)
    data = yaml.safe_load(path.read_text())
    assert data["cpe"] == "cpe:2.3:*:acme:*:*:*:*:*:*:*:*:*"
    assert list(data.keys()) == ["id", "name", "cpe", "aliases"]


def test_write_new_vendor_omits_cpe_when_none(isolated_vendors_dir: Path) -> None:
    v = _lib.NewVendor(
        id="acme",
        name="Acme Corp",
        aliases=[{"source": "nvd", "value": "acme", "confidence": "auto"}],
    )
    path = _lib.write_new_vendor(v)
    data = yaml.safe_load(path.read_text())
    assert "cpe" not in data
    assert list(data.keys()) == ["id", "name", "aliases"]


def test_write_new_product_includes_cpe_and_purl(isolated_vendors_dir: Path) -> None:
    p = _lib.NewProduct(
        id="widget",
        vendor_id="acme",
        name="Widget",
        type="software",
        tags=[],
        aliases=[{"source": "nvd", "value": "widget", "confidence": "auto"}],
        cpe="cpe:2.3:a:acme:widget:*:*:*:*:*:*:*:*",
        purl="pkg:generic/acme/widget",
    )
    path = _lib.write_new_product(p)
    data = yaml.safe_load(path.read_text())
    assert data["cpe"] == "cpe:2.3:a:acme:widget:*:*:*:*:*:*:*:*"
    assert data["purl"] == "pkg:generic/acme/widget"
    assert list(data.keys()) == [
        "id",
        "vendor_id",
        "name",
        "type",
        "tags",
        "cpe",
        "purl",
        "aliases",
    ]


def test_write_new_product_omits_cpe_and_purl_when_none(isolated_vendors_dir: Path) -> None:
    p = _lib.NewProduct(
        id="widget",
        vendor_id="acme",
        name="Widget",
        type="software",
        tags=[],
        aliases=[{"source": "nvd", "value": "widget", "confidence": "auto"}],
    )
    path = _lib.write_new_product(p)
    data = yaml.safe_load(path.read_text())
    assert "cpe" not in data
    assert "purl" not in data
