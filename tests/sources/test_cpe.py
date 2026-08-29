"""Tests for the CPE parsing/formatting helpers in _lib.py.

Run directly: uv run pytest tests/sources/test_cpe.py -v
"""

from tools.sources._lib import (
    CPE_PART_TO_TYPE,
    escape_cpe_component,
    format_cpe_prefix,
    parse_cpe_criteria,
    split_cpe_criteria,
    unescape_cpe_component,
)


def test_split_respects_escaped_colons() -> None:
    criteria = r"cpe:2.3:a:mark_stosberg:data\:\:formvalidator:4.49_01:*:*:*:*:*:*:*"
    tokens = split_cpe_criteria(criteria)
    assert len(tokens) == 13
    assert tokens[4] == r"data\:\:formvalidator"


def test_split_plain_criteria() -> None:
    tokens = split_cpe_criteria("cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*")
    assert tokens == ["cpe", "2.3", "a", "nmap", "nmap", "3.27", "*", "*", "*", "*", "*", "*", "*"]


def test_parse_returns_part_vendor_product() -> None:
    result = parse_cpe_criteria("cpe:2.3:a:nmap:nmap:3.27:*:*:*:*:*:*:*")
    assert result == ("a", "nmap", "nmap")


def test_parse_unescapes_nothing_itself() -> None:
    # parse_cpe_criteria returns raw (still-escaped) components; unescaping
    # is a separate, explicit step so callers choose when they need it.
    result = parse_cpe_criteria(r"cpe:2.3:a:mark_stosberg:data\:\:formvalidator:4.49_01:*:*:*:*:*:*:*")  # noqa: E501
    assert result == ("a", "mark_stosberg", r"data\:\:formvalidator")


def test_parse_rejects_wrong_token_count() -> None:
    assert parse_cpe_criteria("cpe:2.3:a:nmap:nmap") is None


def test_parse_rejects_non_cpe_prefix() -> None:
    assert parse_cpe_criteria("not:a:cpe:string:at:all:*:*:*:*:*:*:*") is None


def test_parse_rejects_wildcard_vendor_or_product() -> None:
    assert parse_cpe_criteria("cpe:2.3:a:*:nmap:3.27:*:*:*:*:*:*:*") is None
    assert parse_cpe_criteria("cpe:2.3:a:nmap:*:3.27:*:*:*:*:*:*:*") is None


def test_unescape_cpe_component() -> None:
    assert unescape_cpe_component(r"data\:\:formvalidator") == "data::formvalidator"
    assert unescape_cpe_component("nmap") == "nmap"


def test_format_cpe_prefix_vendor_only() -> None:
    assert format_cpe_prefix("*", "cisco") == "cpe:2.3:*:cisco:*:*:*:*:*:*:*:*:*"


def test_format_cpe_prefix_vendor_and_product() -> None:
    assert (
        format_cpe_prefix("a", "nmap", "nmap")
        == "cpe:2.3:a:nmap:nmap:*:*:*:*:*:*:*:*"
    )


def test_cpe_part_to_type_mapping() -> None:
    assert CPE_PART_TO_TYPE == {"a": "software", "o": "os", "h": "hardware"}


def test_escape_cpe_component_round_trips_colon_and_plus() -> None:
    assert (
        escape_cpe_component(unescape_cpe_component(r"data\:\:formvalidator"))
        == r"data\:\:formvalidator"
    )
    assert escape_cpe_component(unescape_cpe_component(r"laserjet_m725z\+")) == r"laserjet_m725z\+"


def test_format_cpe_prefix_escapes_special_chars_in_product() -> None:
    assert (
        format_cpe_prefix("a", "ibm", "sterling_connect:direct")
        == r"cpe:2.3:a:ibm:sterling_connect\:direct:*:*:*:*:*:*:*:*"
    )
