import itertools
import json
from pathlib import Path

import pytest

from tools import vers

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "vers_generic_vectors.json").read_text())


@pytest.mark.parametrize("chain", VECTORS["ascending"], ids=" < ".join)
def test_ascending_chains(chain):
    for a, b in itertools.combinations(chain, 2):
        assert vers.compare(a, b) < 0, (a, b)
        assert vers.compare(b, a) > 0, (b, a)


@pytest.mark.parametrize("pair", VECTORS["equal"], ids=" = ".join)
def test_equal_versions(pair):
    assert vers.compare(*pair) == 0


def test_one_dot_zero_orders_like_one_against_post_release():
    assert vers.compare("1", "1p1") == vers.compare("1.0", "1p1") < 0


@pytest.mark.parametrize("text", VECTORS["notVersions"])
def test_not_versions(text):
    assert vers.parse_version(text) is None


@pytest.mark.parametrize(
    "text", ["1.0 ", "1.*", "1.x", "1.0|2", "1.0=", "1.0>2", "1.0%20", "1.0,2", "1.0\n"]
)
def test_other_characters_are_not_versions(text):
    assert vers.parse_version(text) is None


@pytest.mark.parametrize("case", VECTORS["ranges"], ids=lambda c: c["range"])
def test_range_membership(case):
    for v in case["contains"]:
        assert vers.contains(case["range"], v), v
    for v in case["excludes"]:
        assert not vers.contains(case["range"], v), v


@pytest.mark.parametrize("text", VECTORS["invalidRanges"])
def test_invalid_ranges(text):
    with pytest.raises(ValueError):
        vers.parse_range(text)


@pytest.mark.parametrize(
    "text",
    [
        "vers:pypi/<2.0",
        "vers:generic/2.0|1.0",  # not ascending
        "vers:generic/1.0|1.0.0",  # duplicate version
        "vers:generic/>=1.0|>=2.0",  # no alternation
        "vers:generic/<1.0|<2.0",
        "vers:generic/1.0|<2.0",  # equality cannot be followed by <
        "vers:generic/>=1.0|2.0",  # >= cannot be followed by equality
        "vers:generic/*",
        "vers:generic/<1.0|",
    ],
)
def test_vers_structure_rules(text):
    with pytest.raises(ValueError):
        vers.parse_range(text)


@pytest.mark.parametrize(
    "text",
    ["vers:generic/<7.4.0", "vers:generic/>=7.4.0|<8.0.0|>=9.0", "vers:generic/1.0|2.0|>=3.0"],
)
def test_valid_structures(text):
    vers.parse_range(text)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("vers:generic/<7.4.0", "vers:generic/>=7.4.0|<8.0.0", False),
        ("vers:generic/>=7.4.0|<8.0.0", "vers:generic/>=8.0.0", False),
        ("vers:generic/<7.4.0", "vers:generic/>=8.0.0", False),
        ("vers:generic/<2.0", "vers:generic/>=1.5", True),
        ("vers:generic/<=2.0", "vers:generic/>=2.0", True),
        ("vers:generic/<2.0", "vers:generic/>=2.0", False),
        ("vers:generic/<=2.0", "vers:generic/>2.0", False),
        ("vers:generic/8.0", "vers:generic/>=8.0.0", True),
        ("vers:generic/8.0", "vers:generic/>8.0", False),
        ("vers:generic/8.0", "vers:generic/9.0", False),
        ("vers:generic/>1.0|<2.0", "vers:generic/>1.0|<2.0", True),
        # a version strictly between 1.0 and 1.0.1 exists (e.g. 1.0.0.1)
        ("vers:generic/>1.0", "vers:generic/<1.0.1", True),
        ("vers:generic/>=1.0|!=1.5|<2.0", "vers:generic/1.5", False),
        ("vers:generic/>=1.0|!=1.5|<2.0", "vers:generic/1.4", True),
    ],
)
def test_overlaps(a, b, expected):
    assert vers.overlaps(a, b) is expected
    assert vers.overlaps(b, a) is expected
