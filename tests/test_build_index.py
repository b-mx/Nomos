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


def test_write_index_creates_expected_files(tmp_path):
    from tools.build_index import write_index

    write_index(tmp_path, generated_at="2026-01-01T00:00:00Z", vendors_dir=VALID_TREE)
    assert (tmp_path / "aliases.json").exists()
    assert (tmp_path / "by-source" / "nvd_cpe.json").exists()
    content = json.loads((tmp_path / "aliases.json").read_text())
    assert content["generated_at"] == "2026-01-01T00:00:00Z"
    assert len(content["entries"]) == 2


def test_write_index_writes_one_file_per_entry(tmp_path):
    from tools.build_index import write_index

    write_index(tmp_path, generated_at="2026-01-01T00:00:00Z", vendors_dir=VALID_TREE)
    vendor_file = tmp_path / "entries" / "acme.json"
    product_file = tmp_path / "entries" / "acme--widget.json"
    assert vendor_file.exists()
    assert product_file.exists()
    vendor_entry = json.loads(vendor_file.read_text())
    assert vendor_entry["canonical_type"] == "vendor"
    assert vendor_entry["slug"] == "acme"
    product_entry = json.loads(product_file.read_text())
    assert product_entry["canonical_type"] == "product"
    assert product_entry["slug"] == "acme--widget"


def test_build_stats_counts_correctly():
    from tools.build_index import build_entries, build_stats

    stats = build_stats(build_entries(VALID_TREE))
    assert stats == {"vendor_count": 1, "product_count": 1, "sources": ["nvd_cpe"]}


def test_write_index_writes_stats_file(tmp_path):
    from tools.build_index import write_index

    write_index(tmp_path, generated_at="2026-01-01T00:00:00Z", vendors_dir=VALID_TREE)
    stats = json.loads((tmp_path / "stats.json").read_text())
    assert stats == {"vendor_count": 1, "product_count": 1, "sources": ["nvd_cpe"]}
