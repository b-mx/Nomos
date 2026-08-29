"""Shared helpers for the source import scripts (CISA KEV, endoflife.date, NVD CPE).

Part of the `tools.sources` package. Run the importers with
`uv run python -m tools.sources.pull_cisa_kev` from the repo root.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools._common import (
    REPO_ROOT,
    ProductEntry,
    VendorEntry,
    iter_products,
    iter_vendors,
    load_taxonomy_tags,
)
from tools.suggest_match import (
    DEFAULT_THRESHOLD,
    AliasRecord,
    find_close_matches,
    flatten_alias_index,
)

CACHE_DIR = REPO_ROOT / "tmp" / "cache"
VENDORS_DIR = REPO_ROOT / "data" / "vendors"
CACHE_TTL_SECONDS = 24 * 60 * 60
USER_AGENT = "nomos-data-import/0.1 (local dev script; https://github.com/b-mx/Nomos)"

MULTIPLE_VENDORS_SENTINEL = {"multiple vendors", "n/a", ""}


def fetch_json_cached(
    url: str,
    cache_path: Path,
    *,
    refresh: bool = False,
    ttl_seconds: int = CACHE_TTL_SECONDS,
) -> Any:
    """GET a JSON URL, caching the raw response at cache_path with a TTL."""
    if not refresh and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < ttl_seconds:
            return json.loads(cache_path.read_text())

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.URLError as exc:
        if cache_path.exists():
            print(
                f"  fetch failed ({exc}); using stale cache for {cache_path.name}",
                file=sys.stderr,
            )
            return json.loads(cache_path.read_text())
        raise

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(raw)
    return json.loads(raw)


def split_product_names(raw: str) -> list[str]:
    """Split a comma- and/or "and"-separated CISA product list into individual names.

    "iOS, macOS, watchOS" -> ["iOS", "macOS", "watchOS"]
    "Firefox, Firefox ESR, and Thunderbird" -> ["Firefox", "Firefox ESR", "Thunderbird"]
    "iOS and iPadOS" -> ["iOS", "iPadOS"] (no comma, but a single bare " and " splits too)

    Known limitation, accepted rather than solved here: a shared prefix/suffix
    around " and " (e.g. "Small Business RV320 and RV325 Routers", "Confluence
    Data Center and Server") splits into an incomplete name on one side —
    these need manual cleanup via the review UI, same as the comma-list
    device-model entries already flagged from the CISA import.
    """
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
        if parts and parts[-1].lower().startswith("and "):
            parts[-1] = parts[-1][4:].strip()
        return [p for p in parts if p]
    if raw.count(" and ") == 1:
        return [p.strip() for p in raw.split(" and ") if p.strip()]
    return [raw]


def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")

CPE_PART_TO_TYPE = {"a": "software", "o": "os", "h": "hardware"}

VENDOR_KEY_ORDER = ["id", "name", "cpe", "icon", "aliases"]
PRODUCT_KEY_ORDER = [
    "id",
    "vendor_id",
    "name",
    "type",
    "tags",
    "cpe",
    "purl",
    "icon",
    "aliases",
    "services",
]


ECOSYSTEM_TO_PURL_TYPE = {
    "PyPI": "pypi",
    "npm": "npm",
    "Maven": "maven",
    "Go": "golang",
    "crates.io": "cargo",
    "NuGet": "nuget",
    "RubyGems": "gem",
    "Packagist": "composer",
    "Hex": "hex",
    "Pub": "pub",
}

def split_cpe_criteria(criteria: str) -> list[str]:
    r"""Split a CPE 2.3 formatted string on unescaped colons only.

    CPE 2.3 escapes literal colons inside a component as `\:` (seen in
    e.g. Perl module names like `Data::FormValidator`) — a naive
    `.split(":")` would corrupt those.
    """
    return re.split(r"(?<!\\):", criteria)


def parse_cpe_criteria(criteria: str) -> tuple[str, str, str] | None:
    """Parse a CPE 2.3 criteria string into (part, vendor, product).

    Returns the vendor/product components RAW (still CPE-escaped) — use
    unescape_cpe_component() separately when you need the natural string
    form (e.g. for matching against alias values). Returns None if the
    string isn't a well-formed 13-token CPE 2.3 URI, or if vendor/product
    is itself wildcarded (not useful for identity mapping).
    """
    tokens = split_cpe_criteria(criteria)
    if len(tokens) != 13 or tokens[0] != "cpe" or tokens[1] != "2.3":
        return None
    part, vendor, product = tokens[2], tokens[3], tokens[4]
    if part not in ("a", "o", "h") or vendor in ("*", "-") or product in ("*", "-"):
        return None
    return part, vendor, product


_CPE_SPECIAL_CHARS = set('!"#$%&\'()*+,-./:;<=>?@[]^`{|}~')


def unescape_cpe_component(s: str) -> str:
    """Undo CPE 2.3's backslash-escaping of special characters. Generic
    over the full CPE 2.3 special-character set (not just colons) — the
    real NVD dump has confirmed escaped `+`, `/`, `.`, `&`, and `:`."""
    return re.sub(r"\\(.)", r"\1", s)


def escape_cpe_component(s: str) -> str:
    """Inverse of unescape_cpe_component: backslash-escape every CPE 2.3
    special character in a plain (unescaped) component string, so it can
    be safely embedded in a formatted CPE 2.3 URI."""
    return "".join(f"\\{c}" if c in _CPE_SPECIAL_CHARS else c for c in s)


def format_cpe_prefix(part: str, vendor: str, product: str | None = None) -> str:
    """Build a version-wildcarded CPE 2.3 prefix string.

    `vendor`/`product` are plain (unescaped) strings — e.g. the same form
    stored as an alias value — and are escaped here before being embedded
    in the CPE URI.

    format_cpe_prefix("*", "cisco") -> vendor-only form (part wildcarded too,
    since one vendor can span multiple parts).
    format_cpe_prefix("a", "nmap", "nmap") -> vendor+product form.
    """
    vendor = escape_cpe_component(vendor)
    if product is None:
        return ":".join(["cpe", "2.3", "*", vendor] + ["*"] * 9)
    product = escape_cpe_component(product)
    return ":".join(["cpe", "2.3", part, vendor, product] + ["*"] * 8)


def ecosystem_to_purl(ecosystem: str, value: str) -> str | None:
    """Derive a PURL from an existing osv alias's (ecosystem, value) —
    never a guess, just a reformat of data we already trust. Returns None
    when the ecosystem isn't in the known table, or (for Maven) when
    `value` isn't in the expected groupId:artifactId shape — never
    fabricates a namespace."""
    purl_type = ECOSYSTEM_TO_PURL_TYPE.get(ecosystem)
    if purl_type is None:
        return None
    if purl_type == "maven":
        group_id, sep, artifact_id = value.partition(":")
        if not sep or not group_id or not artifact_id:
            return None
        return f"pkg:maven/{group_id}/{artifact_id}"
    return f"pkg:{purl_type}/{value}"



@dataclass(frozen=True)
class ExistingData:
    vendors: list[VendorEntry]
    products: list[ProductEntry]
    vendor_alias_index: list[AliasRecord]
    product_alias_index: list[AliasRecord]
    known_tags: set[str]

    def vendor_by_id(self, vendor_id: str) -> VendorEntry | None:
        return next((v for v in self.vendors if v.data.get("id") == vendor_id), None)

    def products_for_vendor(self, vendor_id: str) -> list[ProductEntry]:
        return [p for p in self.products if p.vendor_id == vendor_id]

    def vendor_entries_with_aliases(self) -> list[tuple[str, list[dict[str, Any]]]]:
        return [(v.data.get("id", ""), v.data.get("aliases", [])) for v in self.vendors]

    def product_entries_with_aliases(
        self, vendor_id: str
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        return [
            (f"{vendor_id}/{p.data.get('id', '')}", p.data.get("aliases", []))
            for p in self.products_for_vendor(vendor_id)
        ]

    def product_alias_index_for_vendor(self, vendor_id: str) -> list[AliasRecord]:
        prefix = f"{vendor_id}/"
        return [r for r in self.product_alias_index if r.canonical_id.startswith(prefix)]


def has_alias(aliases: list[dict[str, Any]], source: str, value: str) -> bool:
    lowered = value.strip().lower()
    return any(
        a.get("source") == source and a.get("value", "").strip().lower() == lowered for a in aliases
    )


def load_existing() -> ExistingData:
    vendors = iter_vendors()
    products = iter_products()
    full_index = flatten_alias_index()
    return ExistingData(
        vendors=vendors,
        products=products,
        vendor_alias_index=[r for r in full_index if "/" not in r.canonical_id],
        product_alias_index=[r for r in full_index if "/" in r.canonical_id],
        known_tags=load_taxonomy_tags(),
    )


def exact_alias_match(index: list[AliasRecord], value: str) -> AliasRecord | None:
    lowered = value.strip().lower()
    for record in index:
        if record.value.strip().lower() == lowered:
            return record
    return None


def best_fuzzy_match(
    value: str,
    canonical_id_to_exclude: str,
    index: list[AliasRecord],
    threshold: int = DEFAULT_THRESHOLD,
) -> tuple[AliasRecord, float] | None:
    matches = find_close_matches(value, canonical_id_to_exclude, index, threshold)
    return matches[0] if matches else None


@dataclass(frozen=True)
class Resolution:
    status: str  # "mapped" | "review" | "new"
    canonical_id: str | None = None
    note: str = ""


def resolve_against_index(
    value: str,
    source: str,
    entries_with_aliases: list[tuple[str, list[dict[str, Any]]]],
    alias_index: list[AliasRecord],
    threshold: int,
    candidate_label: str,
) -> Resolution:
    """Three-tier identity resolution shared by vendor and product matching.

    1. an entry already carries this exact (source, value) alias -> "mapped", nothing to do.
    2. `value` exactly matches some OTHER existing alias (any source) -> "review": almost
       certainly the same entity, but don't silently attach the new alias to someone else's file.
    3. `value` is only a fuzzy match -> "review": too uncertain to act on automatically.
    4. no match at all -> "new".
    """
    for canonical_id, aliases in entries_with_aliases:
        if has_alias(aliases, source, value):
            return Resolution("mapped", canonical_id)

    exact = exact_alias_match(alias_index, value)
    if exact:
        return Resolution(
            "review",
            exact.canonical_id,
            f"'{candidate_label}' == existing alias '{exact.value}' on '{exact.canonical_id}' "
            f"(different source) — add a {source} alias to it manually",
        )

    fuzzy = best_fuzzy_match(value, "", alias_index, threshold)
    if fuzzy:
        record, score = fuzzy
        return Resolution(
            "review",
            record.canonical_id,
            f"'{candidate_label}' ~ existing '{record.canonical_id}' "
            f"(alias '{record.value}', {score:.0f}%) — verify and add manually if it's the same",
        )

    return Resolution("new")


@dataclass
class NewVendor:
    id: str
    name: str
    aliases: list[dict[str, str]]
    cpe: str | None = None


@dataclass
class NewProduct:
    id: str
    vendor_id: str
    name: str
    type: str
    tags: list[str]
    aliases: list[dict[str, str]]
    cpe: str | None = None
    purl: str | None = None


@dataclass
class RunStats:
    total_candidates: int = 0
    already_mapped: int = 0
    review_vendor: list[str] = field(default_factory=list)
    review_product: list[str] = field(default_factory=list)
    skipped: int = 0
    new_vendors: list[NewVendor] = field(default_factory=list)
    new_products: list[NewProduct] = field(default_factory=list)

    def report(self, *, source_label: str) -> None:
        print(f"\n=== {source_label} import report ===")
        print(f"candidates considered:      {self.total_candidates}")
        print(f"already mapped (no action): {self.already_mapped}")
        print(f"skipped (sentinel/empty):   {self.skipped}")
        print(f"new vendors to create:      {len(self.new_vendors)}")
        print(f"new products to create:     {len(self.new_products)}")
        print(f"vendor matches needing manual review: {len(self.review_vendor)}")
        for line in self.review_vendor[:25]:
            print(f"  - {line}")
        if len(self.review_vendor) > 25:
            print(f"  ... and {len(self.review_vendor) - 25} more")
        print(f"product matches needing manual review: {len(self.review_product)}")
        for line in self.review_product[:25]:
            print(f"  - {line}")
        if len(self.review_product) > 25:
            print(f"  ... and {len(self.review_product) - 25} more")

        if self.new_vendors:
            print("\nNew vendors:")
            for v in self.new_vendors:
                print(f"  data/vendors/{v.id}/vendor.yaml  ({v.name})")
        if self.new_products:
            print("\nNew products:")
            for p in self.new_products:
                print(
                    f"  data/vendors/{p.vendor_id}/products/{p.id}.yaml  "
                    f"({p.name}, type={p.type})"
                )


def write_new_vendor(v: NewVendor) -> Path:
    import yaml

    vendor_dir = VENDORS_DIR / v.id
    path = vendor_dir / "vendor.yaml"
    if path.exists():
        raise FileExistsError(
            f"resolver said '{v.id}' was a new vendor, but {path} already exists — "
            "not overwriting it. This means an existing file's aliases no longer "
            "match what the resolver looked for (e.g. a manual edit removed the "
            "alias value it was matching on)."
        )
    vendor_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"id": v.id, "name": v.name}
    if v.cpe:
        data["cpe"] = v.cpe
    data["aliases"] = v.aliases
    path.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True))
    return path


def write_new_product(p: NewProduct) -> Path:
    import yaml

    products_dir = VENDORS_DIR / p.vendor_id / "products"
    path = products_dir / f"{p.id}.yaml"
    if path.exists():
        raise FileExistsError(
            f"resolver said '{p.vendor_id}/{p.id}' was a new product, but {path} "
            "already exists — not overwriting it. This means an existing file's "
            "aliases no longer match what the resolver looked for (e.g. a manual "
            "edit removed the alias value it was matching on)."
        )
    products_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "id": p.id,
        "vendor_id": p.vendor_id,
        "name": p.name,
        "type": p.type,
        "tags": p.tags,
    }
    if p.cpe:
        data["cpe"] = p.cpe
    if p.purl:
        data["purl"] = p.purl
    data["aliases"] = p.aliases
    path.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True))
    return path
