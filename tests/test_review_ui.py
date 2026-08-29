"""Covers the path guard that gates every write the review UI performs."""

from pathlib import Path

import pytest

import tools.review_ui.server as server
from tools._common import REPO_ROOT
from tools.review_ui.server import reject_file, resolve


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


def _fake_writable_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point REPO_ROOT/WRITABLE_ROOTS at a throwaway tree under tmp_path so
    these tests can never touch the real data/ tree, even if the guard under
    test regresses."""
    repo_root = tmp_path / "repo"
    vendors_dir = repo_root / "data" / "vendors"
    vendors_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "REPO_ROOT", repo_root)
    monkeypatch.setattr(server, "WRITABLE_ROOTS", (vendors_dir,))
    return vendors_dir


def test_resolve_rejects_a_symlink_escaping_the_writable_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # resolve() calls .resolve() before the containment check, so a symlink
    # under the writable root that points outside the repo must still be
    # dereferenced and rejected, not accepted because its literal path looks
    # like it's inside data/vendors.
    vendors_dir = _fake_writable_root(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (vendors_dir / "escape").symlink_to(outside)

    with pytest.raises(ValueError):
        server.resolve("data/vendors/escape/vendor.yaml")


def test_reject_file_cannot_remove_a_writable_root_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 'data/vendors/vendor.yaml' passes resolve()'s containment check (it
    # genuinely is under data/vendors) and satisfies reject_file's
    # 'path.name == "vendor.yaml"' branch, which would otherwise
    # shutil.rmtree(path.parent) == shutil.rmtree(data/vendors) — deleting
    # the whole dataset. shutil.rmtree doesn't require the named file to
    # exist, only the parent directory.
    vendors_dir = _fake_writable_root(tmp_path, monkeypatch)
    (vendors_dir / "acme").mkdir()

    path = server.resolve("data/vendors/vendor.yaml")
    with pytest.raises(ValueError):
        reject_file(path)

    assert vendors_dir.exists()
    assert (vendors_dir / "acme").exists()


def test_reject_file_still_removes_a_legitimate_vendor_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendors_dir = _fake_writable_root(tmp_path, monkeypatch)
    vendor_dir = vendors_dir / "acme"
    vendor_dir.mkdir()
    (vendor_dir / "vendor.yaml").write_text("id: acme\n")

    path = server.resolve("data/vendors/acme/vendor.yaml")
    reject_file(path)

    assert not vendor_dir.exists()
    assert vendors_dir.exists()
