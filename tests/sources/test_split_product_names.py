"""Run directly: uv run pytest tests/sources/test_split_product_names.py -v"""

from tools.sources._lib import split_product_names


def test_splits_on_comma() -> None:
    assert split_product_names("iOS, macOS, watchOS") == ["iOS", "macOS", "watchOS"]


def test_splits_on_comma_with_trailing_and() -> None:
    assert split_product_names("Firefox, Firefox ESR, and Thunderbird") == [
        "Firefox",
        "Firefox ESR",
        "Thunderbird",
    ]


def test_splits_on_bare_and_with_no_comma() -> None:
    assert split_product_names("iOS and iPadOS") == ["iOS", "iPadOS"]
    assert split_product_names("Firefox and Thunderbird") == ["Firefox", "Thunderbird"]


def test_leaves_single_name_with_no_comma_or_and_untouched() -> None:
    assert split_product_names("vCenter Server") == ["vCenter Server"]


def test_leaves_multiple_bare_ands_untouched() -> None:
    # No comma, more than one " and " — genuinely ambiguous how to split, so
    # left as one name rather than guessing.
    assert split_product_names("A and B and C") == ["A and B and C"]
