from tools.suggest_match import AliasRecord, find_close_matches, format_comment


def test_close_match_fires_above_threshold():
    index = [AliasRecord(value="microsft", canonical_id="microsoft")]
    matches = find_close_matches("microsoft", "some-new-vendor", index, threshold=85)
    assert len(matches) == 1
    assert matches[0][0].canonical_id == "microsoft"


def test_distinct_names_do_not_fire():
    index = [AliasRecord(value="postgresql", canonical_id="postgresql")]
    matches = find_close_matches("mysql", "some-new-vendor", index, threshold=85)
    assert matches == []


def test_self_match_is_excluded():
    index = [AliasRecord(value="nginx", canonical_id="nginx")]
    matches = find_close_matches("nginx", "nginx", index, threshold=85)
    assert matches == []


def test_format_comment_includes_all_matches():
    index = [AliasRecord(value="microsft", canonical_id="microsoft")]
    matches = find_close_matches("microsoft", "new-vendor", index, threshold=85)
    comment = format_comment("microsoft", matches)
    assert "microsoft" in comment
    assert "distinct vendor/product" in comment


def test_run_diff_mode_skips_non_dict_yaml(monkeypatch, tmp_path):
    from tools import suggest_match

    monkeypatch.setattr(
        suggest_match, "changed_vendor_files", lambda base, head: ["vendors/weird/vendor.yaml"]
    )
    monkeypatch.setattr(suggest_match, "load_yaml_at_ref", lambda ref, path: ["not", "a", "dict"])
    result = suggest_match.run_diff_mode("base", "head", 85)
    assert result == "NO_MATCH"


def test_run_diff_mode_skips_alias_missing_value(monkeypatch):
    from tools import suggest_match

    monkeypatch.setattr(
        suggest_match, "changed_vendor_files", lambda base, head: ["vendors/weird/vendor.yaml"]
    )
    monkeypatch.setattr(
        suggest_match, "load_yaml_at_ref",
        lambda ref, path: {"id": "weird", "aliases": [{"source": "nvd_cpe"}]},
    )
    result = suggest_match.run_diff_mode("base", "head", 85)
    assert result == "NO_MATCH"
