"""
OSC Client Simulator — emulates a mobile controller sending signed OSC messages.

Usage:
    python tests/osc_client_sim.py                        # single burst
    python tests/osc_client_sim.py --count 20 --delay 0.1 # 20 msgs, 100 ms apart
    python tests/osc_client_sim.py --continuous            # until Ctrl+C

The script reads hmac_secret, host, and port directly from config/settings.yaml
so it is always in sync with the gateway configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import sys
import time
from pathlib import Path

# Allow running from any working directory.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from pythonosc import udp_client
from pythonosc.osc_message_builder import OscMessageBuilder


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))


def _make_token(secret: str, sender_ip: str, osc_address: str, timestamp_ms: int) -> str:
    message = f"{sender_ip}:{osc_address}:{timestamp_ms}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Core send
# ---------------------------------------------------------------------------

def send_signed_osc(
    client: udp_client.SimpleUDPClient,
    secret: str,
    sender_ip: str,
    osc_address: str,
    value: float,
) -> int:
    """
    Build an OSC message with:
        [value(f), ts_sec(i), ts_ms_frac(i), hmac_token(s)]

    OSC type 'i' is int32, so we split the millisecond timestamp into:
        ts_sec      = Unix seconds     (fits int32 until 2038)
        ts_ms_frac  = remaining ms     (0-999)

    The gateway receiver reconstructs: timestamp_ms = ts_sec*1000 + ts_ms_frac.

    Returns timestamp_ms used.
    """
    timestamp_ms = int(time.time() * 1000)
    ts_sec = timestamp_ms // 1000
    ts_ms_frac = timestamp_ms % 1000
    token = _make_token(secret, sender_ip, osc_address, timestamp_ms)

    builder = OscMessageBuilder(address=osc_address)
    builder.add_arg(value, "f")           # payload
    builder.add_arg(ts_sec, "i")          # security: Unix seconds (int32-safe)
    builder.add_arg(ts_ms_frac, "i")      # security: ms remainder (0-999)
    builder.add_arg(token, "s")           # security: HMAC-SHA256 hex digest
    msg = builder.build()

    client._sock.sendto(msg.dgram, (client._address, client._port))
    return timestamp_ms


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="MIDI-OSC Gateway client simulator")
    parser.add_argument("--address", default="/fader/1", help="OSC address to send")
    parser.add_argument("--value",   type=float, default=0.5, help="Fader value (0.0–1.0)")
    parser.add_argument("--count",   type=int,   default=1,   help="Number of messages")
    parser.add_argument("--delay",   type=float, default=0.05,help="Seconds between messages")
    parser.add_argument("--continuous", action="store_true",  help="Loop until Ctrl+C")
    args = parser.parse_args()

    cfg = _load_cfg()
    host = "127.0.0.1"  # loopback — matches 0.0.0.0 binding on gateway
    port = cfg["server"]["port"]
    secret = cfg["security"]["hmac_secret"]
    sender_ip = "127.0.0.1"

    client = udp_client.SimpleUDPClient(host, port)

    print(f"Simulator -> gateway at {host}:{port}")
    print(f"OSC address : {args.address}")
    print(f"Value       : {args.value}")
    print(f"HMAC secret : {secret[:12]}… (truncated)")
    print("-" * 52)

    sent = 0
    try:
        while args.continuous or sent < args.count:
            ts = send_signed_osc(client, secret, sender_ip, args.address, args.value)
            sent += 1
            print(f"[{sent:>4}] Sent  address={args.address}  value={args.value}  ts={ts}")
            if args.continuous or sent < args.count:
                time.sleep(args.delay)
    except KeyboardInterrupt:
        print(f"\nStopped after {sent} message(s).")

    print(f"Done. {sent} message(s) sent.")


if __name__ == "__main__":
    main()
