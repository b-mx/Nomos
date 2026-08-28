"""Fuzzy-match a new alias value against the existing alias index.

Used as a non-blocking PR comment check — never a merge gate, since
legitimately similar vendor/product names exist.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz

from tools._common import REPO_ROOT, iter_products, iter_vendors

DEFAULT_THRESHOLD = 85


@dataclass(frozen=True)
class AliasRecord:
    value: str
    canonical_id: str


def flatten_alias_index(vendors_dir: Path = REPO_ROOT / "data" / "vendors") -> list[AliasRecord]:
    records: list[AliasRecord] = []
    for vendor in iter_vendors(vendors_dir):
        for alias in vendor.data.get("aliases", []):
            records.append(AliasRecord(value=alias["value"], canonical_id=vendor.data["id"]))
    for product in iter_products(vendors_dir):
        canonical_id = f"{product.vendor_id}/{product.data['id']}"
        for alias in product.data.get("aliases", []):
            records.append(AliasRecord(value=alias["value"], canonical_id=canonical_id))
    return records


def find_close_matches(
    candidate_value: str,
    candidate_canonical_id: str,
    index: list[AliasRecord],
    threshold: int = DEFAULT_THRESHOLD,
) -> list[tuple[AliasRecord, float]]:
    matches: list[tuple[AliasRecord, float]] = []
    for record in index:
        if record.canonical_id == candidate_canonical_id:
            continue
        score = fuzz.ratio(candidate_value.lower(), record.value.lower())
        if score >= threshold:
            matches.append((record, score))
    return sorted(matches, key=lambda pair: pair[1], reverse=True)


def format_comment(candidate_value: str, matches: list[tuple[AliasRecord, float]]) -> str:
    lines = [f"Alias `{candidate_value}` looks similar to existing entries:"]
    for record, score in matches:
        pct = f"{score:.0f}"
        lines.append(f"- `{record.canonical_id}` (alias `{record.value}`, {pct}% match)")
    lines.append(
        "\nThis alias looks similar to an existing canonical id — please confirm this is a "
        "distinct vendor/product, not a duplicate."
    )
    return "\n".join(lines)


def changed_vendor_files(base_ref: str, head_ref: str) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACM",
            f"{base_ref}...{head_ref}",
            "--",
            "data/vendors/",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return [line for line in result.stdout.splitlines() if line.endswith(".yaml")]


def load_yaml_at_ref(ref: str, path: str) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    data: dict[str, Any] = yaml.safe_load(result.stdout) or {}
    return data


def run_diff_mode(base_ref: str, head_ref: str, threshold: int) -> str:
    full_index = flatten_alias_index()
    comments: list[str] = []
    for path in changed_vendor_files(base_ref, head_ref):
        data = load_yaml_at_ref(head_ref, path)
        if not isinstance(data, dict):
            continue
        canonical_id = data.get("id", "")
        if "vendor_id" in data:
            canonical_id = f"{data['vendor_id']}/{canonical_id}"
        for alias in data.get("aliases", []):
            if not isinstance(alias, dict) or "value" not in alias:
                continue
            matches = find_close_matches(alias["value"], canonical_id, full_index, threshold)
            if matches:
                comments.append(format_comment(alias["value"], matches))
    return "\n\n---\n\n".join(comments) if comments else "NO_MATCH"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref")
    parser.add_argument("--value")
    parser.add_argument("--canonical-id")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()

    if args.base_ref and args.head_ref:
        print(run_diff_mode(args.base_ref, args.head_ref, args.threshold))
        return 0

    if not args.value or not args.canonical_id:
        msg = (
            "either --base-ref and --head-ref, or both --value and "
            "--canonical-id, are required"
        )
        parser.error(msg)

    index = flatten_alias_index()
    matches = find_close_matches(args.value, args.canonical_id, index, args.threshold)
    print(format_comment(args.value, matches) if matches else "NO_MATCH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
