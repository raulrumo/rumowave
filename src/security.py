"""
Security layer: HMAC-SHA256 validation + IP allowlist + replay-attack protection.
"""

import hashlib
import hmac
import logging
import time
from pathlib import Path
from threading import Lock

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

# Module-level cache so config is read once per process.
_config: dict | None = None
_config_lock = Lock()

# Replay-protection: maps ip -> set of (timestamp_ms,) seen within the window.
_seen_timestamps: dict[str, set[int]] = {}
_replay_lock = Lock()


def _load_config() -> dict:
    global _config
    with _config_lock:
        if _config is None:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
                _config = yaml.safe_load(fh)
    return _config


def _evict_stale_timestamps(ip: str, window_ms: int, now_ms: int) -> None:
    """Remove timestamps outside the replay window for a given IP."""
    cutoff = now_ms - window_ms
    _seen_timestamps[ip] = {ts for ts in _seen_timestamps.get(ip, set()) if ts >= cutoff}


def validate_request(sender_ip: str, token: str, osc_address: str, timestamp_ms: int) -> bool:
    """
    Return True only when ALL of the following hold:
      1. sender_ip is in the configured allowlist.
      2. timestamp_ms is within the configured replay window.
      3. timestamp_ms has not been seen before from this IP (replay protection).
      4. The HMAC-SHA256 digest of (sender_ip:osc_address:timestamp_ms) matches token.

    Uses hmac.compare_digest throughout to prevent timing attacks.

    Args:
        sender_ip:    IPv4 address string of the packet sender.
        token:        Hex-encoded HMAC-SHA256 digest supplied by the client.
        osc_address:  The OSC address path from the datagram (e.g. "/midi/note_on/1").
        timestamp_ms: Unix timestamp in milliseconds embedded in the datagram.

    Returns:
        True if the request is authentic and fresh, False otherwise.
    """
    cfg = _load_config()
    sec = cfg["security"]

    allowed_ips: list[str] = sec["allowed_ips"]
    secret: str = sec["hmac_secret"]
    replay_window_ms: int = sec["replay_window_ms"]

    # 1 — IP allowlist (cheap, done first)
    if "0.0.0.0" not in allowed_ips and sender_ip not in allowed_ips:
        logger.warning("Rejected packet from unlisted IP: %s", sender_ip)
        return False

    # 2 — Timestamp freshness
    now_ms = int(time.time() * 1000)
    age_ms = now_ms - timestamp_ms
    if age_ms < 0 or age_ms > replay_window_ms:
        logger.warning("Rejected stale/future packet from %s (age=%d ms)", sender_ip, age_ms)
        return False

    # 3 — Replay check
    with _replay_lock:
        _evict_stale_timestamps(sender_ip, replay_window_ms, now_ms)
        if timestamp_ms in _seen_timestamps.get(sender_ip, set()):
            logger.warning("Rejected replayed packet from %s (ts=%d)", sender_ip, timestamp_ms)
            return False

    # 4 — HMAC-SHA256 verification
    message = f"{sender_ip}:{osc_address}:{timestamp_ms}".encode()
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, token):
        logger.warning("HMAC mismatch from %s", sender_ip)
        return False

    # Commit the timestamp only after successful validation.
    with _replay_lock:
        _seen_timestamps.setdefault(sender_ip, set()).add(timestamp_ms)

    return True


def generate_token(secret: str, sender_ip: str, osc_address: str, timestamp_ms: int) -> str:
    """
    Helper for clients / tests: produce a valid HMAC token for a given message.
    Not used in the gateway hot path.
    """
    message = f"{sender_ip}:{osc_address}:{timestamp_ms}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
