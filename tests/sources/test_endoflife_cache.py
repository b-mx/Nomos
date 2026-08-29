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


def test_very_long_name_is_truncated_with_a_stable_collision_resistant_hash(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "endoflife" / "products"
    cache_dir.mkdir(parents=True)

    # A heavily non-ASCII name: every character percent-encodes to a 3-byte
    # (or more) escape sequence, so a name well under the 255-byte filesystem
    # limit in characters still blows past it once encoded.
    long_name = "日本語" * 200
    other_long_name = long_name + "x"  # shares a long common prefix

    path = product_detail_cache_path(long_name, cache_dir)

    assert path.parent == cache_dir.resolve()
    assert len(path.name) <= 255
    # Stable: the same input always maps to the same filename.
    assert product_detail_cache_path(long_name, cache_dir) == path
    # Collision-resistant: two names sharing a long common prefix (so a
    # naive truncation-only scheme would produce the same filename for
    # both) must still map to different filenames.
    other_path = product_detail_cache_path(other_long_name, cache_dir)
    assert other_path != path
    assert other_path.is_relative_to(cache_dir.resolve())
