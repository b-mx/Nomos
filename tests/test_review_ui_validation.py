"""Regression tests for the server-side record validation added to close the
stored-XSS path through the review UI: data/vendors/ is repo-controlled (a
pull request can add or edit any field), the review UI renders those fields
in a maintainer's browser, and that server can `git push` and open GitHub
PRs. validate_vendor_record()/validate_product_record() are the boundary
that stops a hostile record from ever reaching the renderer; build_groups()
skips (rather than crashes on, or flags-but-still-renders) anything that
fails them.

Fixtures below are genuinely hostile values, not just malformed ones:
  - a `name` containing a raw <img onerror=...> tag and a script-tag breakout
  - an `icon` that attempts a quote-breakout out of a src="..." attribute
  - an `icon` using a `javascript:` URL
  - an alias `value` containing a `"><script>...` breakout
  - a `path`-shaped value (here: a vendor directory name) containing a
    single quote, of the kind that would break out of the review UI's old
    onclick="doApprove('${path}')" handlers

For each, the test asserts either (a) validation rejects/skips it, or (b) it
is valid-but-hostile-looking *text* and must survive round-trip as data
(unescaped, unmodified) rather than being silently mangled -- mangling would
be its own bug, and would mask the fact that safety here comes from the
renderer (DOM textContent/property assignment), not from mutating the data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import tools.review_ui.server as server
from tools.review_ui.server import (
    InvalidRecordError,
    build_groups,
    validate_product_record,
    validate_vendor_record,
)

# --- hostile field fixtures ------------------------------------------------

HOSTILE_NAME_IMG = "<img src=x onerror=alert(1)>"
HOSTILE_NAME_SCRIPT_BREAKOUT = "</script><script>alert(1)</script>"
HOSTILE_ICON_ATTR_BREAKOUT = 'x:y" onload="alert(1)'
HOSTILE_ICON_JAVASCRIPT_URL = "javascript:alert(1)"
HOSTILE_ALIAS_VALUE = '"><script>alert(1)</script>'
HOSTILE_ID_QUOTE_BREAKOUT = "a'+alert(1)+'b"


def _vendor(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "acme",
        "name": "Acme",
        "aliases": [],
    }
    data.update(overrides)
    return data


def _product(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "widget",
        "vendor_id": "acme",
        "name": "Widget",
        "type": "software",
        "tags": [],
        "aliases": [],
    }
    data.update(overrides)
    return data


VENDOR_PATH = "data/vendors/acme/vendor.yaml"
PRODUCT_PATH = "data/vendors/acme/products/widget.yaml"


# --- name: hostile-but-valid text must survive as data, not be rejected ----


@pytest.mark.parametrize("hostile_name", [HOSTILE_NAME_IMG, HOSTILE_NAME_SCRIPT_BREAKOUT])
def test_hostile_but_valid_name_passes_validation_unchanged(hostile_name: str) -> None:
    # `name` is only required to be a string. A name that happens to look
    # like markup is legitimate data (e.g. a vendor genuinely named
    # "<Foo>"); rejecting it here would be the wrong layer -- safety comes
    # from the renderer treating it as text (see the static guard test),
    # not from mangling or blocking it at validation.
    data = _vendor(name=hostile_name)
    validate_vendor_record(VENDOR_PATH, data)  # must not raise
    assert data["name"] == hostile_name  # untouched -- no silent escaping either


def test_hostile_name_on_a_product_also_passes_validation() -> None:
    data = _product(name=HOSTILE_NAME_IMG)
    validate_product_record(PRODUCT_PATH, data)
    assert data["name"] == HOSTILE_NAME_IMG


def test_non_string_name_is_rejected() -> None:
    with pytest.raises(InvalidRecordError, match="name"):
        validate_vendor_record(VENDOR_PATH, _vendor(name=123))


# --- icon: attribute/URL breakout attempts are rejected --------------------


def test_icon_attribute_breakout_is_rejected() -> None:
    with pytest.raises(InvalidRecordError, match="icon"):
        validate_vendor_record(VENDOR_PATH, _vendor(icon=HOSTILE_ICON_ATTR_BREAKOUT))


def test_icon_javascript_url_is_rejected() -> None:
    with pytest.raises(InvalidRecordError, match="icon"):
        validate_product_record(PRODUCT_PATH, _product(icon=HOSTILE_ICON_JAVASCRIPT_URL))


def test_well_formed_icon_passes() -> None:
    validate_vendor_record(VENDOR_PATH, _vendor(icon="logos:acme"))  # must not raise


# --- alias.source / alias.value ---------------------------------------------


def test_alias_with_unrecognised_source_is_rejected() -> None:
    data = _vendor(aliases=[{"source": "evil", "value": "x", "confidence": "auto"}])
    with pytest.raises(InvalidRecordError, match="source"):
        validate_vendor_record(VENDOR_PATH, data)


def test_alias_value_must_be_a_string() -> None:
    data = _vendor(aliases=[{"source": "nvd", "value": 123, "confidence": "auto"}])
    with pytest.raises(InvalidRecordError, match="value"):
        validate_vendor_record(VENDOR_PATH, data)


def test_hostile_but_valid_alias_value_passes_validation_unchanged() -> None:
    # Same reasoning as the hostile name above: a script-breakout-shaped
    # *string* is still a valid alias value. It must survive as data.
    data = _vendor(
        aliases=[{"source": "cisa_kev", "value": HOSTILE_ALIAS_VALUE, "confidence": "auto"}]
    )
    validate_vendor_record(VENDOR_PATH, data)
    assert data["aliases"][0]["value"] == HOSTILE_ALIAS_VALUE


# --- id / vendor_id kebab-case, and path shape ------------------------------


def test_hostile_quote_breakout_id_is_rejected() -> None:
    with pytest.raises(InvalidRecordError, match="kebab-case"):
        validate_vendor_record(VENDOR_PATH, _vendor(id=HOSTILE_ID_QUOTE_BREAKOUT))


def test_hostile_vendor_id_on_product_is_rejected() -> None:
    with pytest.raises(InvalidRecordError, match="kebab-case"):
        validate_product_record(PRODUCT_PATH, _product(vendor_id=HOSTILE_ID_QUOTE_BREAKOUT))


def test_vendor_path_not_matching_canonical_shape_is_rejected() -> None:
    hostile_path = f"data/vendors/{HOSTILE_ID_QUOTE_BREAKOUT}/vendor.yaml"
    with pytest.raises(InvalidRecordError, match="canonical"):
        validate_vendor_record(hostile_path, _vendor())


def test_product_path_not_matching_canonical_shape_is_rejected() -> None:
    hostile_path = f"data/vendors/acme/products/{HOSTILE_ID_QUOTE_BREAKOUT}.yaml"
    with pytest.raises(InvalidRecordError, match="canonical"):
        validate_product_record(hostile_path, _product())


# --- build_groups(): skip-with-log-line, not crash, not flag-and-render ----


def _fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo_root = tmp_path / "repo"
    vendors_dir = repo_root / "data" / "vendors"
    vendors_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "REPO_ROOT", repo_root)
    monkeypatch.setattr(server, "VENDORS_DIR", vendors_dir)
    return vendors_dir


def _write_vendor(vendors_dir: Path, dirname: str, body: str) -> None:
    vendor_dir = vendors_dir / dirname
    vendor_dir.mkdir(parents=True, exist_ok=True)
    (vendor_dir / "vendor.yaml").write_text(body)


def test_build_groups_skips_a_hostile_id_and_logs_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vendors_dir = _fake_repo(tmp_path, monkeypatch)
    # A directory name is attacker-chosen in a hostile PR just like any other
    # field -- this is the literal on-disk shape of the "path-shaped value
    # containing a single quote" fixture.
    _write_vendor(
        vendors_dir,
        "a'+alert(1)+'b",
        "id: \"a'+alert(1)+'b\"\nname: Evil\naliases: []\n",
    )
    _write_vendor(vendors_dir, "acme", "id: acme\nname: Acme\naliases: []\n")

    groups = build_groups(show_all=True)

    assert [g["vendor"]["id"] for g in groups] == ["acme"]  # the hostile one is absent
    err = capsys.readouterr().err
    assert "skipping invalid vendor record" in err
    assert "a'+alert(1)+'b" in err  # points at the offending path/dir for the maintainer


def test_build_groups_skips_a_hostile_icon_but_keeps_the_rest_of_the_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vendors_dir = _fake_repo(tmp_path, monkeypatch)
    _write_vendor(
        vendors_dir,
        "acme",
        'id: acme\nname: Acme\nicon: \'x:y" onload="alert(1)\'\naliases: []\n',
    )
    _write_vendor(vendors_dir, "widgetco", "id: widgetco\nname: WidgetCo\naliases: []\n")

    groups = build_groups(show_all=True)

    ids = [g["vendor"]["id"] for g in groups]
    assert "acme" not in ids  # hostile icon -> skipped
    assert "widgetco" in ids  # one bad record does not take down the listing
    assert "skipping invalid vendor record" in capsys.readouterr().err


def test_build_groups_never_raises_out_of_a_mixed_valid_and_hostile_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendors_dir = _fake_repo(tmp_path, monkeypatch)
    _write_vendor(vendors_dir, "acme", "id: acme\nname: Acme\naliases: []\n")
    _write_vendor(
        vendors_dir,
        "bad-icon",
        'id: bad-icon\nname: Bad\nicon: "javascript:alert(1)"\naliases: []\n',
    )
    products_dir = vendors_dir / "acme" / "products"
    products_dir.mkdir()
    (products_dir / "widget.yaml").write_text(
        "id: widget\nvendor_id: acme\nname: Widget\ntype: software\ntags: []\naliases: []\n"
    )
    (products_dir / "evil.yaml").write_text(
        "id: evil\n"
        "vendor_id: \"a'+alert(1)+'b\"\n"
        "name: Evil\n"
        "type: software\n"
        "tags: []\n"
        "aliases: []\n"
    )

    groups = build_groups(show_all=True)  # must not raise

    acme = next(g for g in groups if g["vendor"]["id"] == "acme")
    product_ids = [p["id"] for p in acme["products"]]
    assert "widget" in product_ids
    assert "evil" not in product_ids
    assert not any(g["vendor"]["id"] == "bad-icon" for g in groups)


# --- Item 2: malformed/non-mapping YAML must be skipped, not crash ---------


def test_build_groups_skips_a_vendor_yaml_that_is_a_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vendors_dir = _fake_repo(tmp_path, monkeypatch)
    # A bare YAML list, not a mapping -- the confirmed live crash was
    # AttributeError: 'list' object has no attribute 'get'.
    _write_vendor(vendors_dir, "listvendor", "- a\n- b\n")
    _write_vendor(vendors_dir, "acme", "id: acme\nname: Acme\naliases: []\n")

    groups = build_groups(show_all=True)  # must not raise

    ids = [g["vendor"]["id"] for g in groups]
    assert "acme" in ids
    assert len(groups) == 1
    err = capsys.readouterr().err
    assert "skipping invalid vendor record" in err


def test_build_groups_skips_a_vendor_yaml_that_is_a_scalar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vendors_dir = _fake_repo(tmp_path, monkeypatch)
    _write_vendor(vendors_dir, "scalarvendor", "just a string\n")
    _write_vendor(vendors_dir, "acme", "id: acme\nname: Acme\naliases: []\n")

    groups = build_groups(show_all=True)  # must not raise

    ids = [g["vendor"]["id"] for g in groups]
    assert "acme" in ids
    assert len(groups) == 1
    assert "skipping invalid vendor record" in capsys.readouterr().err


def test_build_groups_skips_malformed_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vendors_dir = _fake_repo(tmp_path, monkeypatch)
    # Unbalanced flow mapping -- yaml.safe_load raises yaml.ParserError.
    _write_vendor(vendors_dir, "brokenvendor", "id: [unterminated\nname: Broken\n")
    _write_vendor(vendors_dir, "acme", "id: acme\nname: Acme\naliases: []\n")

    groups = build_groups(show_all=True)  # must not raise

    ids = [g["vendor"]["id"] for g in groups]
    assert "acme" in ids
    assert len(groups) == 1
    assert "skipping invalid vendor record" in capsys.readouterr().err


def test_build_groups_skips_a_product_yaml_that_is_a_list_but_keeps_the_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vendors_dir = _fake_repo(tmp_path, monkeypatch)
    _write_vendor(vendors_dir, "acme", "id: acme\nname: Acme\naliases: []\n")
    products_dir = vendors_dir / "acme" / "products"
    products_dir.mkdir()
    (products_dir / "widget.yaml").write_text(
        "id: widget\nvendor_id: acme\nname: Widget\ntype: software\ntags: []\naliases: []\n"
    )
    (products_dir / "evil.yaml").write_text("- a\n- b\n")

    groups = build_groups(show_all=True)  # must not raise

    acme = next(g for g in groups if g["vendor"]["id"] == "acme")
    product_ids = [p["id"] for p in acme["products"]]
    assert "widget" in product_ids
    assert "evil" not in product_ids
    assert "skipping invalid product record" in capsys.readouterr().err


# --- Item 2 (client-side twin): `tags` must be a list of strings -----------


def test_tags_as_a_mapping_is_rejected() -> None:
    data = _product(tags={"a": 1})
    with pytest.raises(InvalidRecordError, match="tags"):
        validate_product_record(PRODUCT_PATH, data)


def test_tags_containing_a_non_string_is_rejected() -> None:
    data = _product(tags=["fine", 123])
    with pytest.raises(InvalidRecordError, match="tags"):
        validate_product_record(PRODUCT_PATH, data)


def test_build_groups_skips_a_product_with_tags_as_a_mapping_but_keeps_the_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vendors_dir = _fake_repo(tmp_path, monkeypatch)
    _write_vendor(vendors_dir, "acme", "id: acme\nname: Acme\naliases: []\n")
    products_dir = vendors_dir / "acme" / "products"
    products_dir.mkdir()
    (products_dir / "widget.yaml").write_text(
        "id: widget\nvendor_id: acme\nname: Widget\ntype: software\ntags: []\naliases: []\n"
    )
    (products_dir / "badtags.yaml").write_text(
        "id: badtags\nvendor_id: acme\nname: Bad\ntype: software\ntags: {a: 1}\naliases: []\n"
    )

    groups = build_groups(show_all=True)  # must not raise, and must not reach the client

    acme = next(g for g in groups if g["vendor"]["id"] == "acme")
    product_ids = [p["id"] for p in acme["products"]]
    assert "widget" in product_ids
    assert "badtags" not in product_ids
    assert "skipping invalid product record" in capsys.readouterr().err


# --- Item 3: aliases[].confidence must be validated against the schema -----


def test_alias_with_invalid_confidence_is_rejected() -> None:
    hostile_confidence = 'curated x" onload=alert(1)'
    data = _vendor(aliases=[{"source": "nvd", "value": "x", "confidence": hostile_confidence}])
    with pytest.raises(InvalidRecordError, match="confidence"):
        validate_vendor_record(VENDOR_PATH, data)


def test_alias_with_missing_confidence_is_rejected() -> None:
    data = _vendor(aliases=[{"source": "nvd", "value": "x"}])
    with pytest.raises(InvalidRecordError, match="confidence"):
        validate_vendor_record(VENDOR_PATH, data)


@pytest.mark.parametrize("confidence", ["curated", "auto"])
def test_alias_with_valid_confidence_passes(confidence: str) -> None:
    data = _vendor(aliases=[{"source": "nvd", "value": "x", "confidence": confidence}])
    validate_vendor_record(VENDOR_PATH, data)  # must not raise


def test_build_groups_skips_a_record_with_a_spoofed_confidence_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vendors_dir = _fake_repo(tmp_path, monkeypatch)
    _write_vendor(
        vendors_dir,
        "acme",
        "id: acme\nname: Acme\naliases:\n"
        "  - source: nvd\n"
        "    value: acme\n"
        '    confidence: \'curated x" onload=alert(1)\'\n',
    )
    _write_vendor(vendors_dir, "widgetco", "id: widgetco\nname: WidgetCo\naliases: []\n")

    groups = build_groups(show_all=True)

    ids = [g["vendor"]["id"] for g in groups]
    assert "acme" not in ids
    assert "widgetco" in ids
    assert "skipping invalid vendor record" in capsys.readouterr().err


def test_build_groups_preserves_hostile_but_valid_name_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vendors_dir = _fake_repo(tmp_path, monkeypatch)
    _write_vendor(
        vendors_dir,
        "acme",
        f"id: acme\nname: {HOSTILE_NAME_IMG!r}\naliases: []\n",
    )

    groups = build_groups(show_all=True)

    assert len(groups) == 1
    assert groups[0]["vendor"]["name"] == HOSTILE_NAME_IMG  # round-trips as data, unescaped
