"""
Thread A — UDP Receiver (Producer).

Opens a raw UDP socket, receives OSC datagrams, validates them through the
security layer, and pushes (osc_address, osc_args, receive_ns) tuples onto the
shared queue.  Never blocks the queue put; drops the packet and increments a
counter if the queue is full (back-pressure signal).
"""

from __future__ import annotations

import logging
import queue
import socket
import time
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pythonosc import osc_message

from .security import validate_request

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

# Shared drop counter — read from the main thread for monitoring.
packets_dropped = 0
packets_received = 0
packets_rejected = 0


def _load_server_cfg() -> dict:
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))["server"]


class UDPReceiver(threading.Thread):
    """
    Binds a UDP socket and pushes validated OSC packets onto *out_queue*.

    Queue items: tuple[str, list, int]  →  (osc_address, osc_args, receive_ns)
    """

    def __init__(self, out_queue: queue.Queue, stop_event: threading.Event) -> None:
        super().__init__(name="udp-receiver", daemon=True)
        self._queue = out_queue
        self._stop = stop_event
        cfg = _load_server_cfg()
        self._host: str = cfg["host"]
        self._port: int = cfg["port"]
        self._recv_buf: int = cfg.get("recv_buffer_bytes", 4_194_304)

    def run(self) -> None:
        global packets_dropped, packets_received, packets_rejected

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self._recv_buf)
        sock.settimeout(0.5)  # unblock every 500 ms to check stop_event
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

            # Parse OSC envelope — reject malformed datagrams before HMAC.
            try:
                msg = osc_message.OscMessage(data)
            except Exception:
                logger.debug("Malformed OSC datagram from %s", sender_ip)
                packets_rejected += 1
                continue

            # Security metadata appended by the client as the last 3 args:
            #   args[-3] = ts_sec      (int32) — Unix seconds
            #   args[-2] = ts_ms_frac  (int32) — milliseconds remainder (0-999)
            #   args[-1] = hmac_token  (str)   — HMAC-SHA256 hex digest
            args = list(msg.params)
            if (
                len(args) < 3
                or not isinstance(args[-1], str)
                or not isinstance(args[-2], int)
                or not isinstance(args[-3], int)
            ):
                logger.debug("Missing/malformed security args from %s", sender_ip)
                packets_rejected += 1
                continue

            timestamp_ms: int = args[-3] * 1000 + args[-2]
            token: str = args[-1]
            payload_args = args[:-3]

            if not validate_request(sender_ip, token, msg.address, timestamp_ms):
                packets_rejected += 1
                continue

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
