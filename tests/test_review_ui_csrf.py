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
