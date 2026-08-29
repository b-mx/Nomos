"""Covers the path guard that gates every write the review UI performs."""

import pytest

from tools._common import REPO_ROOT
from tools.review_ui.server import resolve


def test_resolve_accepts_a_path_inside_data_vendors() -> None:
    path = resolve("data/vendors/apple/vendor.yaml")
    assert path == (REPO_ROOT / "data" / "vendors" / "apple" / "vendor.yaml").resolve()


def test_resolve_rejects_a_sibling_prefix_escape() -> None:
    # 'data/vendors-evil' shares a string prefix with 'data/vendors' but is a
    # different directory. A str.startswith guard lets it through.
    with pytest.raises(ValueError):
        resolve("data/vendors-evil/x.yaml")


def test_resolve_rejects_a_dotdot_traversal() -> None:
    with pytest.raises(ValueError):
        resolve("data/vendors/../../etc/passwd")


def test_resolve_rejects_a_path_outside_the_repo() -> None:
    with pytest.raises(ValueError):
        resolve("/etc/passwd")
