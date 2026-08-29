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


def test_reject_file_refuses_a_nested_vendor_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 'data/vendors/apple/products/vendor.yaml' passes resolve()'s
    # containment check and satisfies 'path.name == "vendor.yaml"', which
    # would otherwise shutil.rmtree(path.parent) ==
    # shutil.rmtree(data/vendors/apple/products) — deleting every product
    # belonging to that vendor even though 'vendor.yaml' never legitimately
    # lives at that depth.
    vendors_dir = _fake_writable_root(tmp_path, monkeypatch)
    vendor_dir = vendors_dir / "apple"
    products_dir = vendor_dir / "products"
    products_dir.mkdir(parents=True)
    (products_dir / "vendor.yaml").write_text("id: apple\n")
    (products_dir / "ios.yaml").write_text("id: ios\n")

    path = server.resolve("data/vendors/apple/products/vendor.yaml")
    with pytest.raises(ValueError):
        reject_file(path)

    assert products_dir.exists()
    assert (products_dir / "vendor.yaml").exists()
    assert (products_dir / "ios.yaml").exists()


def test_reject_file_refuses_a_nonexistent_vendor_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendors_dir = _fake_writable_root(tmp_path, monkeypatch)
    # No 'acme' directory created at all — the path is well-formed but
    # nothing exists on disk.
    path = vendors_dir / "acme" / "vendor.yaml"

    with pytest.raises(ValueError):
        reject_file(path)

    assert not vendors_dir.joinpath("acme").exists()


def test_reject_file_refuses_a_path_of_illegitimate_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendors_dir = _fake_writable_root(tmp_path, monkeypatch)
    vendor_dir = vendors_dir / "acme"
    vendor_dir.mkdir()
    # Neither '<root>/<vendor>/vendor.yaml' nor
    # '<root>/<vendor>/products/<product>.yaml' — a stray file directly
    # under the vendor directory.
    stray = vendor_dir / "notes.yaml"
    stray.write_text("id: acme\n")

    with pytest.raises(ValueError):
        reject_file(stray)

    assert stray.exists()


def test_reject_file_wraps_an_rmtree_oserror_as_valueerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # shutil.rmtree refuses to operate on a symlinked target (raises OSError
    # rather than following it), which is good -- but a raw OSError
    # propagating out of reject_file would be inconsistent with every other
    # refusal in this function, which raises ValueError. `path` itself is
    # deliberately NOT passed through resolve() here: resolve() would follow
    # the symlink all the way to its target and reject it for escaping the
    # writable root before reject_file ever saw it, which is a different
    # (already-covered) guard. This test is specifically about
    # reject_file's own handling of the OSError shutil.rmtree raises.
    vendors_dir = _fake_writable_root(tmp_path, monkeypatch)
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    (real_dir / "vendor.yaml").write_text("id: acme\n")
    vendor_link = vendors_dir / "acme"
    vendor_link.symlink_to(real_dir)

    path = vendor_link / "vendor.yaml"
    assert path.is_file()  # sanity: is_file() follows the symlink

    with pytest.raises(ValueError, match="failed to remove vendor directory"):
        reject_file(path)

    assert real_dir.exists()  # rmtree refused the symlink -- the real target survives
    assert (real_dir / "vendor.yaml").exists()


def test_reject_file_refuses_a_symlinked_products_directory_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # TOCTOU regression: between path.is_file() and path.unlink(), an
    # attacker with concurrent filesystem access could replace the
    # 'products' directory component with a symlink pointing outside the
    # writable root. unlink() follows symlinks with no error, so it would
    # silently delete an out-of-tree file. `path` is deliberately NOT passed
    # through resolve() here, for the same reason as above: resolve() would
    # already catch this by fully dereferencing and checking containment,
    # which masks the specific guard this test targets -- the re-check
    # reject_file itself performs immediately before unlink().
    vendors_dir = _fake_writable_root(tmp_path, monkeypatch)
    vendor_dir = vendors_dir / "acme"
    vendor_dir.mkdir()
    (vendor_dir / "vendor.yaml").write_text("id: acme\n")

    outside = tmp_path / "outside"
    outside.mkdir()
    target_file = outside / "widget.yaml"
    target_file.write_text("do not delete me\n")

    products_link = vendor_dir / "products"
    products_link.symlink_to(outside)

    path = products_link / "widget.yaml"
    assert path.is_file()  # sanity: resolves through the symlink to a real file

    with pytest.raises(ValueError, match="symlinked component"):
        reject_file(path)

    assert target_file.exists()  # must survive -- the symlinked component was rejected


def test_reject_file_unlinks_exactly_one_legitimate_product_and_keeps_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendors_dir = _fake_writable_root(tmp_path, monkeypatch)
    vendor_dir = vendors_dir / "acme"
    products_dir = vendor_dir / "products"
    products_dir.mkdir(parents=True)
    (vendor_dir / "vendor.yaml").write_text("id: acme\n")
    target = products_dir / "widget.yaml"
    sibling = products_dir / "gadget.yaml"
    target.write_text("id: widget\n")
    sibling.write_text("id: gadget\n")

    path = server.resolve("data/vendors/acme/products/widget.yaml")
    reject_file(path)

    assert not target.exists()
    assert sibling.exists()
    assert (vendor_dir / "vendor.yaml").exists()
