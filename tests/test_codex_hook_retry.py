"""Unit tests for Codex hook selective API retry (parity with mcp.js)."""

from __future__ import annotations

import importlib.util
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

_CODEX_HOOKS = (
    Path(__file__).resolve().parents[3]
    / "turingmind-codex-plugin"
    / "hooks"
    / "scripts"
    / "_hook_common.py"
)

_spec = importlib.util.spec_from_file_location("codex_hook_common", _CODEX_HOOKS)
assert _spec and _spec.loader
_hook_common = importlib.util.module_from_spec(_spec)
sys.modules["codex_hook_common"] = _hook_common
_spec.loader.exec_module(_hook_common)


def _http_error(status: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://127.0.0.1:8477/api/v2/test",
        code=status,
        msg="error",
        hdrs=None,
        fp=BytesIO(body.encode()),
    )


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (404, "Session not found", False),
        (400, "bad request", False),
        (503, "", True),
        (429, "", True),
        (500, "database is locked", True),
        (500, "internal error", False),
    ],
)
def test_is_retryable_api_error_http(status: int, body: str, expected: bool) -> None:
    err = _http_error(status, body)
    assert _hook_common.is_retryable_api_error(err) is expected


def test_is_retryable_api_error_network() -> None:
    assert _hook_common.is_retryable_api_error(urllib.error.URLError("connection refused")) is True
    assert _hook_common.is_retryable_api_error(ConnectionError("reset")) is True


def test_api_post_json_does_not_retry_404() -> None:
    err404 = _http_error(404, "not found")
    call_count = {"n": 0}

    def fake_urlopen(*_args, **_kwargs):
        call_count["n"] += 1
        raise err404

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with patch.object(_hook_common, "hook_log"):
            result = _hook_common.api_post_json("/api/v2/session/end", {"repo": "org/r"})

    assert result is None
    assert call_count["n"] == 1


def test_api_post_json_retries_503() -> None:
    from unittest.mock import MagicMock

    call_count = {"n": 0}

    def fake_urlopen(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise _http_error(503, "unavailable")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status":"ok"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with patch.object(_hook_common, "hook_log"):
            with patch("time.sleep"):
                result = _hook_common.api_post_json("/api/v2/sync", {"repo": "org/r", "files": []})

    assert result == {"status": "ok"}
    assert call_count["n"] == 2
