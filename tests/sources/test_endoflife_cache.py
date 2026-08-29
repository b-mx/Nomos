"""Covers the path guard for the endoflife.date per-product detail cache.

`summary["name"]` in pull_endoflife.py comes straight from the upstream
endoflife.date API response. product_detail_cache_path() must turn it into
a filename that can never escape the cache directory, regardless of what
the API sends back.

Run directly: uv run pytest tests/sources/test_endoflife_cache.py -v
"""

from pathlib import Path

import pytest

from tools.sources.pull_endoflife import product_detail_cache_path


def test_dotdot_traversal_is_contained(tmp_path: Path) -> None:
    cache_dir = tmp_path / "endoflife" / "products"
    cache_dir.mkdir(parents=True)

    path = product_detail_cache_path("../../etc/passwd", cache_dir)

    assert path.is_relative_to(cache_dir.resolve())
    assert path.parent == cache_dir.resolve()


def test_absolute_path_is_contained(tmp_path: Path) -> None:
    cache_dir = tmp_path / "endoflife" / "products"
    cache_dir.mkdir(parents=True)

    path = product_detail_cache_path("/etc/passwd", cache_dir)

    assert path.is_relative_to(cache_dir.resolve())
    assert path.parent == cache_dir.resolve()


def test_embedded_separators_are_contained(tmp_path: Path) -> None:
    cache_dir = tmp_path / "endoflife" / "products"
    cache_dir.mkdir(parents=True)

    path = product_detail_cache_path("foo/bar/baz", cache_dir)

    assert path.is_relative_to(cache_dir.resolve())
    assert path.parent == cache_dir.resolve()


def test_bare_dot_and_dotdot_names_are_rejected(tmp_path: Path) -> None:
    cache_dir = tmp_path / "endoflife" / "products"
    cache_dir.mkdir(parents=True)

    with pytest.raises(ValueError):
        product_detail_cache_path(".", cache_dir)
    with pytest.raises(ValueError):
        product_detail_cache_path("..", cache_dir)


def test_empty_name_is_rejected(tmp_path: Path) -> None:
    cache_dir = tmp_path / "endoflife" / "products"
    cache_dir.mkdir(parents=True)

    with pytest.raises(ValueError):
        product_detail_cache_path("", cache_dir)


def test_unicode_name_round_trips_stably_and_is_contained(tmp_path: Path) -> None:
    cache_dir = tmp_path / "endoflife" / "products"
    cache_dir.mkdir(parents=True)

    name = "café-日本語-🚀"
    first = product_detail_cache_path(name, cache_dir)
    second = product_detail_cache_path(name, cache_dir)

    assert first == second  # stable across runs
    assert first.is_relative_to(cache_dir.resolve())


def test_normal_name_produces_a_stable_reasonable_filename(tmp_path: Path) -> None:
    cache_dir = tmp_path / "endoflife" / "products"
    cache_dir.mkdir(parents=True)

    path = product_detail_cache_path("ubuntu", cache_dir)

    assert path == (cache_dir / "ubuntu.json").resolve()
    assert product_detail_cache_path("ubuntu", cache_dir) == path
