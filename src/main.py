"""
Gateway entry point — orchestrates all threads and handles clean shutdown.

Usage:
    python -m src.main
    python src/main.py
"""

from __future__ import annotations

import logging
import logging.handlers
import queue
import signal
import sys
import threading
import time
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Logging setup (must happen before importing project modules so their
# module-level loggers inherit the configuration).
# ---------------------------------------------------------------------------

def _configure_logging(cfg: dict) -> None:
    log_cfg = cfg.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_path = Path(__file__).parent.parent / log_cfg.get("file", "logs/gateway.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(threadName)s] %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # Rotating file handler
    fh = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=log_cfg.get("max_bytes", 10_485_760),
        backupCount=log_cfg.get("backup_count", 5),
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stats printer (runs in main thread)
# ---------------------------------------------------------------------------

def _print_stats(telemetry, stop_event: threading.Event, interval: float = 10.0) -> None:
    from . import receiver as recv_mod

    while not stop_event.wait(timeout=interval):
        stats = telemetry.last_stats()
        logger.info(
            "Stats | received=%d rejected=%d dropped=%d | "
            "latency(µs) min=%.1f avg=%.1f max=%.1f [n=%d]",
            recv_mod.packets_received,
            recv_mod.packets_rejected,
            recv_mod.packets_dropped,
            stats["min_us"],
            stats["avg_us"],
            stats["max_us"],
            stats["count"],
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    _configure_logging(cfg)

    logger.info("=== RumoWave starting ===")

    # Shared primitives
    packet_queue: queue.Queue = queue.Queue(maxsize=1024)
    stop_event = threading.Event()

    # Deferred imports so logging is configured first.
    from .receiver import UDPReceiver
    from .midi_writer import MidiWriter
    from .telemetry import Telemetry

    telemetry = Telemetry()
    receiver = UDPReceiver(out_queue=packet_queue, stop_event=stop_event)
    writer = MidiWriter(in_queue=packet_queue, stop_event=stop_event, telemetry=telemetry)

    # Stats thread
    stats_thread = threading.Thread(
        target=_print_stats,
        args=(telemetry, stop_event),
        name="stats",
        daemon=True,
    )

    # ------------------------------------------------------------------
    # Signal handler for Ctrl+C (SIGINT) and SIGTERM
    # ------------------------------------------------------------------
    def _shutdown(signum, frame):
        logger.info("Shutdown signal received (%s) — stopping threads…", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------
    receiver.start()
    writer.start()
    stats_thread.start()

    logger.info("All threads started. Press Ctrl+C to stop.")

    # Block main thread until stop is requested.
    stop_event.wait()

    logger.info("Waiting for threads to finish…")
    receiver.join(timeout=5)
    writer.join(timeout=5)

    # Flush remaining telemetry before exit.
    telemetry.stop()

    logger.info("=== RumoWave stopped cleanly ===")


if __name__ == "__main__":
    main()
