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
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VENDORS_DIR = REPO_ROOT / "data" / "vendors"
TAXONOMY_FILE = REPO_ROOT / "data" / "taxonomy" / "tags.yaml"
SCHEMA_DIR = REPO_ROOT / "data" / "schema"
STATIC_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 8765

PRODUCT_TYPES = ["hardware", "appliance", "firmware", "os", "software", "library"]

VENDOR_SCHEMA = json.loads((SCHEMA_DIR / "vendor.schema.json").read_text())
PRODUCT_SCHEMA = json.loads((SCHEMA_DIR / "product.schema.json").read_text())


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


def has_auto(data: dict[str, Any]) -> bool:
    return any(a.get("confidence") == "auto" for a in data.get("aliases", []))


def load_taxonomy() -> list[str]:
    data = load_yaml(TAXONOMY_FILE)
    return [t["id"] for t in data.get("tags", [])]


def build_groups(show_all: bool) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for vendor_dir in sorted(VENDORS_DIR.iterdir()):
        if not vendor_dir.is_dir():
            continue
        vfile = vendor_dir / "vendor.yaml"
        if not vfile.exists():
            continue
        vdata = load_yaml(vfile)
        vendor_pending = has_auto(vdata)

        products: list[dict[str, Any]] = []
        pdir = vendor_dir / "products"
        if pdir.exists():
            for pfile in sorted(pdir.glob("*.yaml")):
                pdata = load_yaml(pfile)
                product_pending = has_auto(pdata)
                if show_all or product_pending:
                    products.append(
                        {
                            "path": rel(pfile),
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
                        "path": rel(vfile),
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
    if path.name == "vendor.yaml":
        target = path.parent
        # `path` already passed resolve()'s containment check, but that only
        # guarantees target is INSIDE a writable root, not that it isn't the
        # root itself. `path = "data/vendors/vendor.yaml"` would otherwise
        # satisfy `path.name == "vendor.yaml"` and rmtree the whole dataset:
        # shutil.rmtree() doesn't require the named file to exist, only the
        # parent directory. Refuse to remove a writable root outright.
        if any(target == root.resolve() for root in WRITABLE_ROOTS):
            raise ValueError(f"refusing to remove writable root {target}")
        shutil.rmtree(target)
    else:
        path.unlink()


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
        run_git("push", "-u", "origin", branch)
        result["pushed"] = True

        if body.get("create_pr"):
            if not shutil.which("gh"):
                msg = "commit and push succeeded, but the `gh` CLI is not installed/found"
                raise ValueError(msg)
            if open_pr_exists_for(branch):
                result["pr_url"] = (
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
                url_lines = [
                    line for line in pr_result.stdout.splitlines() if line.startswith("http")
                ]
                result["pr_url"] = url_lines[-1] if url_lines else pr_result.stdout.strip()

    return result


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

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            html = (STATIC_DIR / "index.html").read_bytes()
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
