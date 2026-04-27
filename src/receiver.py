"""
Thread A — UDP Receiver (Producer).

Opens a raw UDP socket, receives OSC datagrams, validates them through the
security layer, and pushes (osc_address, osc_args, receive_ns) tuples onto the
shared queue.  Never blocks the queue put; drops the packet and increments a
counter if the queue is full (back-pressure signal).

Two security modes (controlled by security.require_hmac in settings.yaml):

  require_hmac: true  — full validation: IP allowlist + timestamp freshness +
                        replay protection + HMAC-SHA256. The client must append
                        [ts_sec(i), ts_ms_frac(i), token(s)] as the last 3 args.
                        Use this with the bundled osc_client_sim.py.

  require_hmac: false — IP allowlist only. Accepts standard OSC messages with
                        no extra arguments. Compatible with TouchOSC, OSC/PILOT,
                        and any app that sends plain OSC. Only use on a trusted
                        local network.
"""

from __future__ import annotations

import logging
import queue
import socket
import time
import threading
from pathlib import Path

import yaml
from pythonosc import osc_message

from .security import validate_request

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

packets_dropped  = 0
packets_received = 0
packets_rejected = 0


def _load_cfg() -> dict:
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


class UDPReceiver(threading.Thread):
    """
    Binds a UDP socket and pushes validated OSC packets onto *out_queue*.

    Queue items: tuple[str, list, int]  ->  (osc_address, osc_args, receive_ns)
    """

    def __init__(self, out_queue: queue.Queue, stop_event: threading.Event) -> None:
        super().__init__(name="udp-receiver", daemon=True)
        self._queue = out_queue
        self._stop  = stop_event
        cfg = _load_cfg()
        self._host:         str  = cfg["server"]["host"]
        self._port:         int  = cfg["server"]["port"]
        self._recv_buf:     int  = cfg["server"].get("recv_buffer_bytes", 4_194_304)
        self._require_hmac: bool = cfg["security"].get("require_hmac", True)
        self._allowed_ips:  list = cfg["security"].get("allowed_ips", [])

        mode = "HMAC-SHA256" if self._require_hmac else "IP-only (no HMAC)"
        logger.info("Security mode: %s", mode)

    # ------------------------------------------------------------------

    def _ip_allowed(self, sender_ip: str) -> bool:
        return "0.0.0.0" in self._allowed_ips or sender_ip in self._allowed_ips

    def run(self) -> None:
        global packets_dropped, packets_received, packets_rejected

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self._recv_buf)
        sock.settimeout(0.5)
        sock.bind((self._host, self._port))
        logger.info("UDP receiver listening on %s:%d", self._host, self._port)

        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(65_535)
            except TimeoutError:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    logger.error("Socket error: %s", exc)
                break

            receive_ns = time.perf_counter_ns()
            packets_received += 1
            sender_ip = addr[0]

            # IP check is always first — cheapest filter regardless of mode.
            if not self._ip_allowed(sender_ip):
                logger.warning("Rejected packet from unlisted IP: %s", sender_ip)
                packets_rejected += 1
                continue

            # Parse OSC — reject malformed datagrams before any further work.
            try:
                msg = osc_message.OscMessage(data)
            except Exception:
                logger.debug("Malformed OSC datagram from %s", sender_ip)
                packets_rejected += 1
                continue

            args = list(msg.params)

            if self._require_hmac:
                # Signed mode: last 3 args are [ts_sec(i), ts_ms_frac(i), token(s)]
                if (
                    len(args) < 3
                    or not isinstance(args[-1], str)
                    or not isinstance(args[-2], int)
                    or not isinstance(args[-3], int)
                ):
                    logger.debug("Missing HMAC security args from %s", sender_ip)
                    packets_rejected += 1
                    continue

                timestamp_ms: int = args[-3] * 1000 + args[-2]
                token: str        = args[-1]
                payload_args      = args[:-3]

                if not validate_request(sender_ip, token, msg.address, timestamp_ms):
                    packets_rejected += 1
                    continue
            else:
                # Plain OSC mode: accept as-is, no timestamp or token expected.
                payload_args = args

            try:
                self._queue.put_nowait((msg.address, payload_args, receive_ns))
            except queue.Full:
                packets_dropped += 1
                logger.warning("Queue full — dropping packet from %s", sender_ip)

        sock.close()
        logger.info(
            "UDP receiver stopped. received=%d rejected=%d dropped=%d",
            packets_received, packets_rejected, packets_dropped,
        )
