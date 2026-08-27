import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
VALID_TREE = FIXTURES / "valid_tree" / "vendors"


def test_build_index_matches_expected_snapshot():
    from tools.build_index import build_index

    index = build_index(generated_at="2026-01-01T00:00:00Z", vendors_dir=VALID_TREE)
    expected = json.loads((FIXTURES / "expected_aliases.json").read_text())
    assert index == expected


def test_build_by_source_splits_correctly():
    from tools.build_index import build_by_source, build_entries

    entries = build_entries(VALID_TREE)
    by_source = build_by_source(entries)
    assert set(by_source.keys()) == {"nvd_cpe"}
    assert len(by_source["nvd_cpe"]) == 2  # vendor acme + product widget
