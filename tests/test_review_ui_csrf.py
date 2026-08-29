"""Integration tests for the CSRF defence in tools/review_ui/server.py.

The server has no auth by design (127.0.0.1-only maintainer tooling), which
means every mutation endpoint was previously reachable by a cross-origin
`text/plain` form POST -- a "simple request" that never triggers a CORS
preflight. These tests start the *real* HTTPServer on an ephemeral port and
drive it with http.client, exercising the exact code path a browser (or an
attacking page) would hit, rather than calling handler methods directly.

Critical: REPO_ROOT / VENDORS_DIR / WRITABLE_ROOTS are monkeypatched to a
throwaway tmp_path tree for every test here. None of these tests may ever
write to, delete from, or commit anything in the real repository.

Run directly: uv run pytest tests/test_review_ui_csrf.py -v
"""

from __future__ import annotations

import http.client
import json
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import tools.review_ui.server as server

VALID_TOKEN = server.CSRF_TOKEN


@pytest.fixture()
def live_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, int]]:
    """A real, running instance of the review UI server bound to an
    ephemeral port, pointed at a throwaway data/vendors/ tree."""
    repo_root = tmp_path / "repo"
    vendors_dir = repo_root / "data" / "vendors"
    vendor_dir = vendors_dir / "acme"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "vendor.yaml").write_text(
        "id: acme\n"
        "name: Acme\n"
        "aliases:\n"
        "  - source: nvd\n"
        "    value: acme\n"
        "    confidence: auto\n"
    )
    monkeypatch.setattr(server, "REPO_ROOT", repo_root)
    monkeypatch.setattr(server, "VENDORS_DIR", vendors_dir)
    monkeypatch.setattr(server, "WRITABLE_ROOTS", (vendors_dir,))

    httpd = server.HTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_port
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", port
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def _post(
    host: str,
    port: int,
    path: str,
    payload: dict[str, object],
    *,
    content_type: str = "application/json",
    token: str | None = VALID_TOKEN,
    origin: str | None = None,
    host_header: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """A raw HTTP POST giving full control over Content-Type, the CSRF
    token header, Origin, and Host -- exactly the four things a cross-origin
    attacker either cannot set (custom header, Content-Type without a
    preflight) or can only spoof to a value that must be rejected (Origin,
    and, in a DNS-rebinding attack, Host)."""
    body = json.dumps(payload).encode()
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.connect()
        conn.putrequest("POST", path, skip_host=True)
        conn.putheader("Host", host_header if host_header is not None else f"{host}:{port}")
        conn.putheader("Content-Type", content_type)
        if token is not None:
            conn.putheader("X-CSRF-Token", token)
        if origin is not None:
            conn.putheader("Origin", origin)
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read()
    finally:
        conn.close()
    data = json.loads(raw) if raw else {}
    return status, data


def _approve_acme(
    live_server: tuple[str, int], **overrides: Any
) -> tuple[int, dict[str, Any]]:
    host, port = live_server
    return _post(
        host, port, "/api/approve", {"path": "data/vendors/acme/vendor.yaml"}, **overrides
    )


def _get(
    host: str,
    port: int,
    path: str,
    *,
    host_header: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """A raw HTTP GET giving full control over the Host header -- the one
    thing a DNS-rebinding attacker controls on an otherwise-legitimate
    same-origin GET. Returns (status, headers, body)."""
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.connect()
        conn.putrequest("GET", path, skip_host=True)
        conn.putheader("Host", host_header if host_header is not None else f"{host}:{port}")
        conn.endheaders()
        resp = conn.getresponse()
        status = resp.status
        headers = dict(resp.getheaders())
        body = resp.read()
    finally:
        conn.close()
    return status, headers, body


# --- Item 1: the three independent CSRF barriers -----------------------


def test_cross_origin_text_plain_post_is_rejected(live_server: tuple[str, int]) -> None:
    # This is the exact CSRF shape: a cross-origin <form> POST with a
    # text/plain body is a "simple request" and never triggers a preflight.
    status, data = _approve_acme(live_server, content_type="text/plain")
    assert status == 403
    assert "error" in data


def test_content_type_with_form_urlencoded_is_rejected(live_server: tuple[str, int]) -> None:
    status, data = _approve_acme(
        live_server, content_type="application/x-www-form-urlencoded"
    )
    assert status == 403
    assert "error" in data


def test_missing_token_is_rejected(live_server: tuple[str, int]) -> None:
    status, data = _approve_acme(live_server, token=None)
    assert status == 403
    assert "error" in data


def test_invalid_token_is_rejected(live_server: tuple[str, int]) -> None:
    status, data = _approve_acme(live_server, token="not-the-real-token")
    assert status == 403
    assert "error" in data


def test_wrong_origin_is_rejected(live_server: tuple[str, int]) -> None:
    status, data = _approve_acme(live_server, origin="https://evil.example")
    assert status == 403
    assert "error" in data


def test_null_origin_is_rejected(live_server: tuple[str, int]) -> None:
    # A sandboxed cross-origin iframe sends Origin: null -- must not be
    # treated as "no Origin header".
    status, data = _approve_acme(live_server, origin="null")
    assert status == 403
    assert "error" in data


def test_bad_host_is_rejected(live_server: tuple[str, int]) -> None:
    status, data = _approve_acme(live_server, host_header="evil.example:1234")
    assert status == 403
    assert "error" in data


def test_dns_rebinding_style_host_is_rejected(live_server: tuple[str, int]) -> None:
    # DNS rebinding: a hostile domain resolves to 127.0.0.1, so the browser
    # connects to the real socket but sends its own (attacker) Host header.
    status, data = _approve_acme(live_server, host_header="attacker.example:80")
    assert status == 403
    assert "error" in data


def test_correct_request_succeeds(live_server: tuple[str, int]) -> None:
    host, port = live_server
    status, data = _approve_acme(live_server, origin=f"http://{host}:{port}")
    assert status == 200
    assert data == {"ok": True}


def test_correct_request_without_origin_header_still_succeeds(
    live_server: tuple[str, int],
) -> None:
    # Same-origin fetches may omit Origin entirely; only a *present but
    # wrong* Origin must be rejected.
    status, data = _approve_acme(live_server, origin=None)
    assert status == 200
    assert data == {"ok": True}


def test_get_root_injects_the_real_token_and_leaves_no_placeholder(
    live_server: tuple[str, int],
) -> None:
    host, port = live_server
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", "/")
    resp = conn.getresponse()
    html = resp.read().decode()
    conn.close()
    assert server.CSRF_TOKEN in html
    assert "__CSRF_TOKEN__" not in html


# --- Item 1: do_GET must apply the same Host check as do_POST -----------


def test_get_root_with_foreign_host_is_rejected(live_server: tuple[str, int]) -> None:
    # This is the concrete finding: GET / with a rebound Host used to return
    # 200 with the real CSRF token in the body.
    host, port = live_server
    status, _headers, body = _get(host, port, "/", host_header="evil.test:80")
    assert status == 403
    data = json.loads(body)
    assert "error" in data
    assert server.CSRF_TOKEN not in body.decode()


def test_get_root_with_dns_rebinding_style_host_is_rejected(
    live_server: tuple[str, int],
) -> None:
    host, port = live_server
    status, _headers, body = _get(host, port, "/", host_header="attacker.example:80")
    assert status == 403
    assert server.CSRF_TOKEN not in body.decode()


def test_get_pending_with_foreign_host_is_rejected(live_server: tuple[str, int]) -> None:
    host, port = live_server
    status, _headers, body = _get(host, port, "/api/pending", host_header="evil.test:80")
    assert status == 403
    assert "error" in json.loads(body)


def test_get_git_status_with_foreign_host_is_rejected(live_server: tuple[str, int]) -> None:
    # /api/git_status shells out to git/gh and leaks branch names -- it must
    # not be readable by a rebound origin either.
    host, port = live_server
    status, _headers, body = _get(host, port, "/api/git_status", host_header="evil.test:80")
    assert status == 403
    assert "error" in json.loads(body)


def test_get_root_with_correct_host_still_succeeds(live_server: tuple[str, int]) -> None:
    host, port = live_server
    status, _headers, body = _get(host, port, "/")
    assert status == 200
    assert server.CSRF_TOKEN in body.decode()


def test_get_root_carries_cache_control_no_store(live_server: tuple[str, int]) -> None:
    # The served HTML embeds the live, secret CSRF token -- it must not be
    # disk-cacheable.
    host, port = live_server
    status, headers, _body = _get(host, port, "/")
    assert status == 200
    assert headers.get("Cache-Control") == "no-store"


# --- Item 4: a negative Content-Length must not hang the server ---------


def test_negative_content_length_is_rejected(live_server: tuple[str, int]) -> None:
    host, port = live_server
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.connect()
        conn.putrequest("POST", "/api/approve", skip_host=True)
        conn.putheader("Host", f"{host}:{port}")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("X-CSRF-Token", VALID_TOKEN)
        # A negative Content-Length: int() accepts it with no error, and
        # rfile.read(-1) would then block until EOF on this
        # single-threaded server if this weren't rejected up front.
        conn.putheader("Content-Length", "-1")
        conn.endheaders(b"")
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read()
    finally:
        conn.close()
    assert status == 400
    assert "error" in json.loads(raw)


def test_non_integer_content_length_is_rejected(live_server: tuple[str, int]) -> None:
    host, port = live_server
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.connect()
        conn.putrequest("POST", "/api/approve", skip_host=True)
        conn.putheader("Host", f"{host}:{port}")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("X-CSRF-Token", VALID_TOKEN)
        conn.putheader("Content-Length", "not-a-number")
        conn.endheaders(b"")
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read()
    finally:
        conn.close()
    assert status == 400
    assert "error" in json.loads(raw)


# --- Item 2: fresh server-side confirmation for publication -------------


def test_commit_pr_rejects_push_without_nonce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "data" / "vendors").mkdir(parents=True)
    monkeypatch.setattr(server, "REPO_ROOT", repo_root)

    with pytest.raises(ValueError, match="publish confirmation"):
        server.do_commit_pr({"branch": "main", "message": "msg", "push": True})


def test_commit_pr_rejects_push_with_an_invalid_nonce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "data" / "vendors").mkdir(parents=True)
    monkeypatch.setattr(server, "REPO_ROOT", repo_root)

    with pytest.raises(ValueError, match="publish confirmation"):
        server.do_commit_pr(
            {
                "branch": "main",
                "message": "msg",
                "push": True,
                "publish_nonce": "not-a-real-nonce",
            }
        )


def test_commit_pr_accepts_a_fresh_nonce_and_it_is_single_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "data" / "vendors").mkdir(parents=True)
    monkeypatch.setattr(server, "REPO_ROOT", repo_root)

    # Stub every git/subprocess touchpoint do_commit_pr uses past the nonce
    # gate. This test is about the nonce gate, not about actually running
    # git -- it must never invoke a real `git push` or `gh pr create`.
    monkeypatch.setattr(server, "run_validate", lambda: None)
    monkeypatch.setattr(server, "rebuild_examples", lambda: None)
    monkeypatch.setattr(
        server,
        "git_status_for_vendors",
        lambda: [{"status": "M", "path": "data/vendors/acme/vendor.yaml"}],
    )
    monkeypatch.setattr(server, "git_current_branch", lambda: "main")
    monkeypatch.setattr(server, "git_branch_exists", lambda branch: False)
    monkeypatch.setattr(server, "run_git", lambda *args: "stub-output\n")
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, stdout="", stderr=""),
    )

    publish_calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_publish(branch: str, message: str, body: dict[str, Any]) -> dict[str, Any]:
        publish_calls.append((branch, message, body))
        return {"pushed": True, "pr_url": "https://example.invalid/pr/1"}

    monkeypatch.setattr(server, "_publish_branch", fake_publish)

    nonce = server.create_publish_nonce()
    result = server.do_commit_pr(
        {"branch": "main", "message": "msg", "push": True, "publish_nonce": nonce}
    )

    assert result["pushed"] is True
    assert result["pr_url"] == "https://example.invalid/pr/1"
    assert len(publish_calls) == 1  # only the stub ran -- no real push/gh call

    # Single-use: the same nonce must be rejected the second time.
    with pytest.raises(ValueError, match="publish confirmation"):
        server.do_commit_pr(
            {"branch": "main", "message": "msg", "push": True, "publish_nonce": nonce}
        )
