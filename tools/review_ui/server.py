#!/usr/bin/env python3
"""Local-only review UI for confidence:auto vendor/product entries.

Maintainer tooling: no auth, binds to 127.0.0.1 only, meant to run on a
maintainer's own machine. Not part of the published site.

Usage:
    uv run python -m tools.review_ui.server [--port 8765]

Then open http://127.0.0.1:8765/
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import jsonschema
import yaml

from tools._common import KEBAB_CASE_RE

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VENDORS_DIR = REPO_ROOT / "data" / "vendors"
TAXONOMY_FILE = REPO_ROOT / "data" / "taxonomy" / "tags.yaml"
SCHEMA_DIR = REPO_ROOT / "data" / "schema"
STATIC_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8765

# CSRF defence
# ------------
# This server has no auth by design (maintainer-only, 127.0.0.1-only), but
# that means every mutation endpoint is reachable by any website the
# maintainer's browser visits: a cross-origin `<form>` POST with a
# `text/plain` body is a "simple request" (no CORS preflight), and
# /api/commit_pr can `git push` and open a GitHub PR, reaching outside the
# machine. Three independent barriers close this off — an attacker must
# defeat all three:
#   1. Content-Type must be exactly application/json (rejects simple-request
#      forms, which cannot set this header without triggering a preflight).
#   2. A per-process secret token in a custom header (also unsettable
#      cross-origin without a preflight).
#   3. Strict Origin/Host checks against the port actually bound (blocks
#      DNS-rebinding, where a hostile domain resolves to 127.0.0.1).
CSRF_TOKEN = secrets.token_urlsafe(32)
CSRF_TOKEN_PLACEHOLDER = "__CSRF_TOKEN__"
CSRF_HEADER = "X-CSRF-Token"
_JSON_CONTENT_TYPE_RE = re.compile(r"^application/json(\s*;\s*charset=utf-8)?$", re.IGNORECASE)

# Fresh, server-side, single-use confirmation for /api/commit_pr's
# push/create_pr flow (see do_commit_pr). Stored server-side rather than
# trusting the client with anything more than an opaque nonce.
PUBLISH_NONCE_TTL_SECONDS = 120.0
_publish_nonces: dict[str, float] = {}
_publish_nonces_lock = threading.Lock()

PRODUCT_TYPES = ["hardware", "appliance", "firmware", "os", "software", "library"]

VENDOR_SCHEMA = json.loads((SCHEMA_DIR / "vendor.schema.json").read_text())
PRODUCT_SCHEMA = json.loads((SCHEMA_DIR / "product.schema.json").read_text())

# Reused from the product schema rather than re-declared here, so the two
# never drift apart (the vendor schema's icon pattern is identical).
_ICON_PATTERN_RE = re.compile(PRODUCT_SCHEMA["properties"]["icon"]["pattern"])
_ALIAS_SOURCE_ENUM = frozenset(PRODUCT_SCHEMA["$defs"]["alias"]["properties"]["source"]["enum"])

# The only two canonical on-disk shapes build_groups() ever produces a `path`
# for. `path` values end up interpolated into the UI's onclick handlers /
# data attributes (see index.html); rejecting anything that doesn't match one
# of these two shapes closes that off at the boundary rather than trusting
# every field in a repo-controlled YAML file to already be safe.
_VENDOR_PATH_RE = re.compile(r"^data/vendors/[a-z0-9]+(?:-[a-z0-9]+)*/vendor\.yaml$")
_PRODUCT_PATH_RE = re.compile(
    r"^data/vendors/[a-z0-9]+(?:-[a-z0-9]+)*/products/[a-z0-9]+(?:-[a-z0-9]+)*\.yaml$"
)


class InvalidRecordError(ValueError):
    """A vendor/product record failed server-side validation before being
    handed to the review UI. build_groups() catches this and skips the
    record (with a stderr warning) rather than rendering half-validated
    data -- see the module docstring reasoning in build_groups()."""


def _require_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidRecordError(f"{field} must be a string, got {type(value).__name__}")
    return value


def validate_path_shape(path: str, *, is_vendor: bool) -> None:
    pattern = _VENDOR_PATH_RE if is_vendor else _PRODUCT_PATH_RE
    if not pattern.match(path):
        raise InvalidRecordError(f"path {path!r} does not match the canonical on-disk shape")


def _validate_aliases(data: dict[str, Any]) -> None:
    aliases = data.get("aliases") or []
    if not isinstance(aliases, list):
        raise InvalidRecordError("aliases must be a list")
    for i, alias in enumerate(aliases):
        if not isinstance(alias, dict):
            raise InvalidRecordError(f"aliases[{i}] must be an object")
        source = alias.get("source")
        if source not in _ALIAS_SOURCE_ENUM:
            raise InvalidRecordError(f"aliases[{i}].source {source!r} is not a recognised source")
        _require_str(alias.get("value"), f"aliases[{i}].value")


def _validate_id(data: dict[str, Any], field: str) -> None:
    value = _require_str(data.get(field), field)
    if not KEBAB_CASE_RE.match(value):
        raise InvalidRecordError(f"{field} {value!r} is not kebab-case")


def _validate_icon(data: dict[str, Any]) -> None:
    icon = data.get("icon")
    if icon is None:
        return
    icon = _require_str(icon, "icon")
    if not _ICON_PATTERN_RE.match(icon):
        raise InvalidRecordError(f"icon {icon!r} does not match the iconify id pattern")


def validate_vendor_record(path: str, data: dict[str, Any]) -> None:
    """Raise InvalidRecordError if `data` (loaded from `path`) is not safe to
    hand to the review UI. Checks the fields the UI actually renders into
    HTML-adjacent contexts (onclick handlers, attributes): path shape, id,
    name, icon, and alias source/value -- not full schema conformance, which
    is tools/validate.py's job and would also reject records that are merely
    incomplete rather than hostile."""
    validate_path_shape(path, is_vendor=True)
    _validate_id(data, "id")
    _require_str(data.get("name"), "name")
    _validate_icon(data)
    _validate_aliases(data)


def validate_product_record(path: str, data: dict[str, Any]) -> None:
    validate_path_shape(path, is_vendor=False)
    _validate_id(data, "id")
    _validate_id(data, "vendor_id")
    _require_str(data.get("name"), "name")
    _validate_icon(data)
    _validate_aliases(data)


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text()) or {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True))


def rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


# Every path the UI may write to. Checked with Path.is_relative_to rather
# than str.startswith: 'data/vendors-evil' shares a string prefix with
# 'data/vendors' but is a different directory.
WRITABLE_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "data" / "vendors",
)


def resolve(rel_path: str) -> Path:
    path = (REPO_ROOT / rel_path).resolve()
    if not any(path.is_relative_to(root.resolve()) for root in WRITABLE_ROOTS):
        raise ValueError(f"path {rel_path!r} is outside the writable allowlist")
    return path


def create_publish_nonce() -> str:
    """Mint a fresh, single-use, short-lived confirmation for the
    push/create_pr path of /api/commit_pr (see ITEM 2 of the CSRF hardening:
    those two actions have effects outside the machine and must not be
    reachable from a single request)."""
    _prune_expired_nonces()
    nonce = secrets.token_urlsafe(32)
    with _publish_nonces_lock:
        _publish_nonces[nonce] = time.monotonic() + PUBLISH_NONCE_TTL_SECONDS
    return nonce


def consume_publish_nonce(nonce: str) -> bool:
    """Validate and consume a publish nonce in one step. Popping it under the
    lock even on failure (expired) makes the nonce single-use regardless of
    outcome, so a stale nonce can't be probed repeatedly."""
    now = time.monotonic()
    with _publish_nonces_lock:
        expiry = _publish_nonces.pop(nonce, None)
    return expiry is not None and now <= expiry


def _prune_expired_nonces() -> None:
    now = time.monotonic()
    with _publish_nonces_lock:
        expired = [n for n, expiry in _publish_nonces.items() if expiry < now]
        for n in expired:
            del _publish_nonces[n]


def has_auto(data: dict[str, Any]) -> bool:
    return any(a.get("confidence") == "auto" for a in data.get("aliases", []))


def load_taxonomy() -> list[str]:
    data = load_yaml(TAXONOMY_FILE)
    return [t["id"] for t in data.get("tags", [])]


def build_groups(show_all: bool) -> list[dict[str, Any]]:
    """Build the vendor/product tree the review UI renders.

    Every record is validated (validate_vendor_record / validate_product_record)
    before it's added to the result. Records under data/vendors/ are
    repo-controlled: a hostile pull request is the injection vector, and this
    server (with git push / PR-creation powers) plus the maintainer's browser
    are the target. An invalid record is *skipped* -- logged to stderr and
    left out of the listing entirely -- rather than surfaced as a flagged
    entry: half-rendering untrusted data (even behind a "this looked
    suspicious" banner) is exactly the kind of inconsistent handling that
    lets a sink slip through, and a skipped vendor/product is simply invisible
    to review until its YAML is fixed, which is a safe failure mode for
    maintainer tooling that never crashes the rest of the listing.
    """
    groups: list[dict[str, Any]] = []
    for vendor_dir in sorted(VENDORS_DIR.iterdir()):
        if not vendor_dir.is_dir():
            continue
        vfile = vendor_dir / "vendor.yaml"
        if not vfile.exists():
            continue
        vdata = load_yaml(vfile)
        vpath = rel(vfile)
        try:
            validate_vendor_record(vpath, vdata)
        except InvalidRecordError as exc:
            print(f"review_ui: skipping invalid vendor record {vpath}: {exc}", file=sys.stderr)
            continue
        vendor_pending = has_auto(vdata)

        products: list[dict[str, Any]] = []
        pdir = vendor_dir / "products"
        if pdir.exists():
            for pfile in sorted(pdir.glob("*.yaml")):
                pdata = load_yaml(pfile)
                ppath = rel(pfile)
                try:
                    validate_product_record(ppath, pdata)
                except InvalidRecordError as exc:
                    print(
                        f"review_ui: skipping invalid product record {ppath}: {exc}",
                        file=sys.stderr,
                    )
                    continue
                product_pending = has_auto(pdata)
                if show_all or product_pending:
                    products.append(
                        {
                            "path": ppath,
                            "id": pdata.get("id"),
                            "name": pdata.get("name"),
                            "type": pdata.get("type"),
                            "tags": pdata.get("tags", []),
                            "icon": pdata.get("icon"),
                            "aliases": pdata.get("aliases", []),
                            "services": pdata.get("services", []),
                            "pending": product_pending,
                            "raw": pfile.read_text(),
                        }
                    )

        if show_all or vendor_pending or products:
            groups.append(
                {
                    "vendor": {
                        "path": vpath,
                        "id": vdata.get("id"),
                        "name": vdata.get("name"),
                        "icon": vdata.get("icon"),
                        "aliases": vdata.get("aliases", []),
                        "pending": vendor_pending,
                        "raw": vfile.read_text(),
                    },
                    "products": products,
                }
            )
    return groups


def approve_file(path: Path) -> None:
    data = load_yaml(path)
    for alias in data.get("aliases", []):
        if alias.get("confidence") == "auto":
            alias["confidence"] = "curated"
    save_yaml(path, data)


def reject_file(path: Path) -> None:
    # `path` already passed resolve()'s containment check, which only
    # guarantees it is INSIDE a writable root — not that it is a real
    # on-disk record, nor that it has the shape of a legitimate vendor or
    # product file. shutil.rmtree() doesn't require the named file to
    # exist, only its parent directory, so a crafted path like
    # 'data/vendors/apple/products/vendor.yaml' would satisfy the old
    # 'path.name == "vendor.yaml"' check and rmtree the *products*
    # directory — deleting every product for that vendor — even though
    # 'vendor.yaml' never legitimately lives there. Require the path to be
    # an existing file and to match one of exactly two legitimate shapes:
    # `<root>/<vendor>/vendor.yaml` (vendor rejection, removes the vendor
    # directory) or `<root>/<vendor>/products/<product>.yaml` (product
    # rejection, unlinks only that file).
    if not path.is_file():
        raise ValueError(f"refusing to reject a path that is not an existing file: {path}")

    roots = tuple(root.resolve() for root in WRITABLE_ROOTS)

    if path.name == "vendor.yaml":
        vendor_dir = path.parent
        if vendor_dir.parent not in roots:
            raise ValueError(f"refusing to remove non-canonical vendor path: {path}")
        if vendor_dir in roots:
            raise ValueError(f"refusing to remove writable root {vendor_dir}")
        try:
            # shutil.rmtree refuses a symlinked target outright (raises
            # OSError rather than following it), so this path isn't subject
            # to the same TOCTOU concern as the unlink() below — but the
            # raised OSError should still surface as the same ValueError
            # every other refusal in this function raises.
            shutil.rmtree(vendor_dir)
        except OSError as exc:
            raise ValueError(f"failed to remove vendor directory {vendor_dir}: {exc}") from exc
        return

    if path.suffix == ".yaml":
        products_dir = path.parent
        vendor_dir = products_dir.parent
        if products_dir.name == "products" and vendor_dir.parent in roots:
            # TOCTOU guard: path.is_file() above (and resolve()'s containment
            # check before that) followed symlinks, so an attacker with
            # concurrent filesystem access could, between that check and this
            # point, replace `products_dir` (or `vendor_dir`) with a symlink
            # pointing outside the writable root; unlink() would then follow
            # it and silently remove an out-of-tree file. Path.is_symlink()
            # uses lstat(), so it does NOT itself follow a symlink, letting us
            # detect a swapped component without falling into the same race.
            #
            # Residual risk: this narrows the window to the gap between this
            # check and the unlink() call two lines below — a component swap
            # landing in that exact gap is not caught. A fully atomic fix
            # would open the parent directory with O_NOFOLLOW and call
            # os.unlink(name, dir_fd=parent_fd); that is meaningfully more
            # code to close a window this small, against a threat model
            # (another local process racing the filesystem) that already
            # implies access this tool does nothing else to defend against.
            if path.is_symlink() or products_dir.is_symlink() or vendor_dir.is_symlink():
                raise ValueError(f"refusing to remove a path with a symlinked component: {path}")
            path.unlink()
            return

    raise ValueError(f"refusing to reject a path that is not a canonical record: {path}")


def update_file(path: Path, fields: dict[str, Any]) -> None:
    data = load_yaml(path)
    if "name" in fields:
        data["name"] = fields["name"]
    if "type" in fields and fields["type"]:
        data["type"] = fields["type"]
    if "tags" in fields:
        data["tags"] = fields["tags"]
    if "icon" in fields:
        icon = (fields["icon"] or "").strip()
        if icon:
            data["icon"] = icon
        else:
            data.pop("icon", None)
    save_yaml(path, data)


def save_raw(path: Path, raw: str) -> None:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("YAML must parse to an object (vendor/product record)")
    schema = VENDOR_SCHEMA if path.name == "vendor.yaml" else PRODUCT_SCHEMA
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"schema error at {list(exc.path)}: {exc.message}") from exc
    if not raw.endswith("\n"):
        raw += "\n"
    path.write_text(raw)


def remove_alias(path: Path, index: int) -> None:
    data = load_yaml(path)
    aliases = data.get("aliases", [])
    if 0 <= index < len(aliases):
        aliases.pop(index)
    data["aliases"] = aliases
    save_yaml(path, data)


def run_git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError((result.stderr or result.stdout).strip() or f"git {' '.join(args)} failed")
    return result.stdout


def git_current_branch() -> str:
    return run_git("rev-parse", "--abbrev-ref", "HEAD").strip()


def git_branch_exists(branch: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", branch], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return result.returncode == 0


def git_status_for_vendors() -> list[dict[str, str]]:
    # --untracked-files=all: list every new file individually rather than
    # collapsing a whole new vendor directory into one line.
    raw = run_git("status", "--porcelain=v1", "--untracked-files=all", "--", "data/vendors")
    entries: list[dict[str, str]] = []
    for line in raw.splitlines():
        if not line:
            continue
        status, rest = line[:2], line[3:]
        entries.append({"status": status.strip() or "??", "path": rest})
    return entries


def change_counts(entries: list[dict[str, str]]) -> dict[str, int]:
    added = sum(1 for e in entries if e["status"] in ("A", "??"))
    modified = sum(1 for e in entries if "M" in e["status"])
    deleted = sum(1 for e in entries if "D" in e["status"])
    renamed = sum(1 for e in entries if "R" in e["status"])
    return {"added": added, "modified": modified, "deleted": deleted, "renamed": renamed}


def suggest_message(counts: dict[str, int]) -> str:
    parts = []
    if counts["added"]:
        parts.append(f"{counts['added']} added")
    if counts["modified"] or counts["renamed"]:
        parts.append(f"{counts['modified'] + counts['renamed']} modified")
    if counts["deleted"]:
        parts.append(f"{counts['deleted']} removed")
    summary = ", ".join(parts) if parts else "no changes"
    return (
        "data: review CISA KEV / endoflife.date vendor and product mappings\n\n"
        f"{summary} under data/vendors/, reviewed and curated via the local review UI."
    )


def open_pr_exists_for(branch: str) -> bool:
    if not shutil.which("gh"):
        return False
    result = subprocess.run(
        ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    try:
        return len(json.loads(result.stdout)) > 0
    except (json.JSONDecodeError, TypeError):
        return False


def git_status_payload() -> dict[str, Any]:
    branch = git_current_branch()
    entries = git_status_for_vendors()
    counts = change_counts(entries)
    fresh_branch = f"data/vendor-review-{datetime.now():%Y%m%d-%H%M}"
    # If the current branch already has an open PR, default to staying on it
    # (adds another commit to the same PR); otherwise suggest a fresh branch.
    has_open_pr = open_pr_exists_for(branch)
    suggested_branch = branch if has_open_pr else fresh_branch
    message = suggest_message(counts)
    return {
        "branch": branch,
        "entries": entries,
        "counts": counts,
        "has_changes": bool(entries),
        "has_open_pr": has_open_pr,
        "suggested_branch": suggested_branch,
        "suggested_message": message,
        "suggested_pr_title": message.splitlines()[0],
        "suggested_pr_body": message,
        "gh_available": shutil.which("gh") is not None,
    }


def run_validate() -> None:
    result = subprocess.run(
        ["uv", "run", "tools/validate.py"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise ValueError("tools/validate.py failed:\n" + (result.stdout + result.stderr).strip())


def rebuild_examples() -> None:
    """Regenerate data/examples/ from data/vendors/ so it never goes stale
    relative to review-UI edits (the CI freshness check diffs it against a
    fresh build_index.py run)."""
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = subprocess.run(
        [
            "uv",
            "run",
            "tools/build_index.py",
            "--output-dir",
            "data/examples",
            "--generated-at",
            generated_at,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = "failed to regenerate data/examples/:\n" + (result.stdout + result.stderr).strip()
        raise ValueError(msg)


def do_commit_pr(body: dict[str, Any]) -> dict[str, Any]:
    branch = (body.get("branch") or "").strip()
    message = (body.get("message") or "").strip()
    if not branch:
        raise ValueError("branch name is required")
    if not message:
        raise ValueError("commit message is required")

    # ITEM 2: push and create_pr have effects outside this machine (git push,
    # opening a GitHub PR) and must not be reachable from a single CSRF-armored
    # request alone. Require a fresh, server-side, single-use confirmation
    # obtained from a prior call to /api/publish_intent. A commit-only call
    # (both false) doesn't touch anything outside the repo, so it needs none.
    # Checked before any git/subprocess work below so a missing/invalid/
    # reused nonce fails fast without side effects.
    if body.get("push") or body.get("create_pr"):
        nonce = body.get("publish_nonce")
        if not isinstance(nonce, str) or not nonce or not consume_publish_nonce(nonce):
            raise ValueError(
                "push/create_pr requires a fresh publish confirmation: "
                "call /api/publish_intent first and pass its nonce as publish_nonce"
            )

    run_validate()

    entries = git_status_for_vendors()
    if not entries:
        raise ValueError("nothing to commit under data/vendors/")

    starting_branch = git_current_branch()
    if branch != starting_branch:
        if git_branch_exists(branch):
            run_git("checkout", branch)
        else:
            run_git("checkout", "-b", branch)

    # Regenerate data/examples/ from the current data/vendors/ state so the
    # committed snapshot never goes stale relative to this commit — the CI
    # freshness check diffs it against a fresh build_index.py run.
    rebuild_examples()

    run_git("add", "data/vendors", "data/examples")
    commit_result = subprocess.run(
        ["git", "commit", "-m", message, "--", "data/vendors", "data/examples"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    already_committed = "nothing to commit" in (commit_result.stdout + commit_result.stderr).lower()
    if commit_result.returncode != 0 and not already_committed:
        raise ValueError((commit_result.stderr or commit_result.stdout).strip())
    commit_sha = run_git("rev-parse", "--short", "HEAD").strip()

    result: dict[str, Any] = {
        "branch": branch,
        "commit": commit_sha,
        "pushed": False,
        "pr_url": None,
    }

    if body.get("push"):
        result.update(_publish_branch(branch, message, body))

    return result


def _publish_branch(branch: str, message: str, body: dict[str, Any]) -> dict[str, Any]:
    """Push `branch` and, if requested, open a PR for it. Split out from
    do_commit_pr so it's a single seam tests can stub: the nonce gate above
    is what must be verified, not that this function actually reaches
    origin/GitHub."""
    run_git("push", "-u", "origin", branch)
    publish_result: dict[str, Any] = {"pushed": True}

    if body.get("create_pr"):
        if not shutil.which("gh"):
            msg = "commit and push succeeded, but the `gh` CLI is not installed/found"
            raise ValueError(msg)
        if open_pr_exists_for(branch):
            publish_result["pr_url"] = (
                "(already had an open PR — pushed a new commit to it, no PR created)"
            )
        else:
            pr_title = (body.get("pr_title") or message.splitlines()[0]).strip()
            pr_body = body.get("pr_body") or message
            base = (body.get("base") or "main").strip()
            pr_result = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--base", base,
                    "--head", branch,
                    "--title", pr_title,
                    "--body", pr_body,
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            if pr_result.returncode != 0:
                raise ValueError(
                    "commit and push succeeded, but `gh pr create` failed:\n"
                    + (pr_result.stderr or pr_result.stdout).strip()
                )
            url_lines = [line for line in pr_result.stdout.splitlines() if line.startswith("http")]
            publish_result["pr_url"] = url_lines[-1] if url_lines else pr_result.stdout.strip()

    return publish_result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")  # type: ignore[no-any-return]

    def _bound_port(self) -> int:
        # The port actually bound, not DEFAULT_PORT or a hardcoded value:
        # main() may have been given --port, and tests bind port 0 and let
        # the OS assign one. HTTPServer.server_bind() sets this from the
        # real listening socket, so it always reflects reality.
        server: HTTPServer = self.server  # type: ignore[assignment]
        return server.server_port

    def _csrf_error(self) -> str | None:
        """The three independent barriers from ITEM 1, checked cheapest
        first. Returns an error message, or None if the request may proceed.
        Applies to every mutation endpoint handled by do_POST below,
        including /api/commit_pr and /api/publish_intent -- no exemptions."""
        content_type = self.headers.get("Content-Type", "")
        if not _JSON_CONTENT_TYPE_RE.match(content_type.strip()):
            return (
                f"Content-Type must be application/json (got {content_type!r}); "
                "this alone defeats simple-request CSRF since a cross-origin "
                "form cannot set it without triggering a CORS preflight"
            )

        token = self.headers.get(CSRF_HEADER, "")
        if not token or not secrets.compare_digest(token, CSRF_TOKEN):
            return f"missing or invalid {CSRF_HEADER} header"

        port = self._bound_port()
        own_origin = f"http://127.0.0.1:{port}"
        origin = self.headers.get("Origin")
        if origin is not None and origin != own_origin:
            return f"Origin {origin!r} does not match this server ({own_origin!r})"

        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return f"Host {host!r} does not match this server (expected port {port})"

        return None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            html = (STATIC_DIR / "index.html").read_bytes()
            html = html.replace(CSRF_TOKEN_PLACEHOLDER.encode(), CSRF_TOKEN.encode())
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif parsed.path == "/api/pending":
            show_all = parse_qs(parsed.query).get("all", ["0"])[0] == "1"
            self._json(
                {
                    "groups": build_groups(show_all),
                    "taxonomy": load_taxonomy(),
                    "types": PRODUCT_TYPES,
                }
            )
        elif parsed.path == "/api/git_status":
            try:
                self._json(git_status_payload())
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 400)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        csrf_error = self._csrf_error()
        if csrf_error is not None:
            self._json({"error": csrf_error}, 403)
            return
        try:
            body = self._read_json()
            if parsed.path == "/api/approve":
                approve_file(resolve(body["path"]))
            elif parsed.path == "/api/approve_group":
                vpath = resolve(body["vendor_path"])
                vdata = load_yaml(vpath)
                if has_auto(vdata):
                    approve_file(vpath)
                pdir = vpath.parent / "products"
                if pdir.exists():
                    for pfile in pdir.glob("*.yaml"):
                        pdata = load_yaml(pfile)
                        if has_auto(pdata):
                            approve_file(pfile)
            elif parsed.path == "/api/reject":
                reject_file(resolve(body["path"]))
            elif parsed.path == "/api/update":
                update_file(resolve(body["path"]), body.get("fields", {}))
            elif parsed.path == "/api/remove_alias":
                remove_alias(resolve(body["path"]), int(body["index"]))
            elif parsed.path == "/api/save_raw":
                save_raw(resolve(body["path"]), body["raw"])
            elif parsed.path == "/api/publish_intent":
                # ITEM 2: mints the fresh, single-use confirmation do_commit_pr
                # requires whenever push or create_pr is set. This is a
                # mutation endpoint (it creates server-side state) so it goes
                # through the same CSRF gate as everything else above.
                self._json(
                    {"nonce": create_publish_nonce(), "expires_in": PUBLISH_NONCE_TTL_SECONDS}
                )
                return
            elif parsed.path == "/api/commit_pr":
                self._json(do_commit_pr(body))
                return
            else:
                self._json({"error": "not found"}, 404)
                return
            self._json({"ok": True})
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 400)


def main() -> int:
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"Nomos review UI: http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
