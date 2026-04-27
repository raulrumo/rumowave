"""
Latency telemetry: collects (receive_ns, send_ns) pairs, computes latency in
microseconds, and flushes to a rotating CSV in /logs every FLUSH_INTERVAL_S seconds.

All public methods are thread-safe.
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_FLUSH_INTERVAL_S = 5.0
_MAX_CSV_ROWS = 100_000  # rotate after this many rows


@dataclass(slots=True)
class _Sample:
    receive_ns: int
    send_ns: int
    osc_address: str

    @property
    def latency_us(self) -> float:
        return (self.send_ns - self.receive_ns) / 1_000.0


class Telemetry:
    def __init__(self) -> None:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._samples: deque[_Sample] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._row_count = 0
        self._file_index = 0
        self._csv_file: object = None
        self._writer: csv.DictWriter | None = None

        # Running aggregates — survive flush cycles so last_stats() is always
        # meaningful regardless of when the stats thread wakes up relative to
        # the flush thread.
        self._total_count: int = 0
        self._total_sum_us: float = 0.0
        self._total_min_us: float = float("inf")
        self._total_max_us: float = 0.0

        self._open_new_file()

        self._flush_thread = threading.Thread(
            target=self._flush_loop, name="telemetry-flush", daemon=True
        )
        self._flush_thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, receive_ns: int, send_ns: int, osc_address: str) -> None:
        """Called by the MIDI writer thread after each successful send."""
        sample = _Sample(receive_ns, send_ns, osc_address)
        lat = sample.latency_us
        with self._lock:
            self._samples.append(sample)
            self._total_count += 1
            self._total_sum_us += lat
            if lat < self._total_min_us:
                self._total_min_us = lat
            if lat > self._total_max_us:
                self._total_max_us = lat

    def stop(self) -> None:
        """Flush remaining samples and close the CSV file cleanly."""
        self._stop.set()
        self._flush_thread.join(timeout=10)
        self._flush_pending()
        if self._csv_file:
            self._csv_file.close()

    def last_stats(self) -> dict:
        """
        Return lifetime min/avg/max latency (µs) over all messages recorded
        since startup.  Never returns n=0 after the first record() call.
        """
        with self._lock:
            n = self._total_count
            if n == 0:
                return {"count": 0, "min_us": 0.0, "avg_us": 0.0, "max_us": 0.0}
            return {
                "count": n,
                "min_us": self._total_min_us,
                "avg_us": self._total_sum_us / n,
                "max_us": self._total_max_us,
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open_new_file(self) -> None:
        if self._csv_file:
            self._csv_file.close()
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = _LOGS_DIR / f"latency_{ts}_{self._file_index:02d}.csv"
        self._csv_file = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(
            self._csv_file,
            fieldnames=["timestamp_iso", "osc_address", "receive_ns", "send_ns", "latency_us"],
        )
        self._writer.writeheader()
        self._row_count = 0
        self._file_index += 1
        logger.info("Telemetry writing to %s", path)

    def _flush_pending(self) -> None:
        with self._lock:
            batch, self._samples = self._samples, deque()

        if not batch or self._writer is None:
            return

        iso = time.strftime("%Y-%m-%dT%H:%M:%S")
        for s in batch:
            self._writer.writerow(
                {
                    "timestamp_iso": iso,
                    "osc_address": s.osc_address,
                    "receive_ns": s.receive_ns,
                    "send_ns": s.send_ns,
                    "latency_us": f"{s.latency_us:.2f}",
                }
            )
            self._row_count += 1

        self._csv_file.flush()

        if self._row_count >= _MAX_CSV_ROWS:
            self._open_new_file()

    def _flush_loop(self) -> None:
        while not self._stop.wait(timeout=_FLUSH_INTERVAL_S):
            self._flush_pending()
