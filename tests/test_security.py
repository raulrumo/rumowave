"""Tests for the HMAC-SHA256 security layer."""

import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.security import generate_token, validate_request

_SECRET = "test-secret-key-for-unit-tests-only"
_IP = "127.0.0.1"
_ADDR = "/fader/1"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _patched_cfg():
    return {
        "security": {
            "hmac_secret": _SECRET,
            "replay_window_ms": 5000,
            "allowed_ips": [_IP],
        }
    }


def test_valid_request_accepted():
    ts = _now_ms()
    token = generate_token(_SECRET, _IP, _ADDR, ts)
    with patch("src.security._load_config", return_value=_patched_cfg()):
        assert validate_request(_IP, token, _ADDR, ts) is True


def test_wrong_token_rejected():
    ts = _now_ms()
    with patch("src.security._load_config", return_value=_patched_cfg()):
        assert validate_request(_IP, "deadbeef", _ADDR, ts) is False


def test_stale_timestamp_rejected():
    ts = _now_ms() - 10_000  # 10 seconds ago
    token = generate_token(_SECRET, _IP, _ADDR, ts)
    with patch("src.security._load_config", return_value=_patched_cfg()):
        assert validate_request(_IP, token, _ADDR, ts) is False


def test_ip_not_in_allowlist_rejected():
    ts = _now_ms()
    token = generate_token(_SECRET, "10.0.0.1", _ADDR, ts)
    with patch("src.security._load_config", return_value=_patched_cfg()):
        assert validate_request("10.0.0.1", token, _ADDR, ts) is False


def test_replay_rejected():
    ts = _now_ms()
    token = generate_token(_SECRET, _IP, _ADDR, ts)
    with patch("src.security._load_config", return_value=_patched_cfg()):
        assert validate_request(_IP, token, _ADDR, ts) is True
        # Same timestamp again → replay
        assert validate_request(_IP, token, _ADDR, ts) is False
