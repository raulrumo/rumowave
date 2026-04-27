"""
Thread B — MIDI Writer (Consumer).

Drains the shared queue, translates each OSC message via mapper.py, sends it
through Windows MIDI Services (winrt-Windows.Devices.Midi), and records
latency via telemetry.py.

WinRT MIDI is fully async under the hood; we call it synchronously from this
dedicated thread to keep the hot path simple and avoid GIL contention with the
receiver thread.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time

from pathlib import Path

import winrt.windows.devices.midi as midi
import yaml

from .mapper import MidiCommand, resolve
from .telemetry import Telemetry

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

logger = logging.getLogger(__name__)


def _build_midi_message(cmd: MidiCommand):
    """Construct the appropriate WinRT MIDI message object."""
    ch = cmd.channel - 1  # WinRT uses 0-based channels

    match cmd.type:
        case "note_on":
            return midi.MidiNoteOnMessage(ch, cmd.number, cmd.value)
        case "note_off":
            return midi.MidiNoteOffMessage(ch, cmd.number, cmd.value)
        case "cc":
            return midi.MidiControlChangeMessage(ch, cmd.number, cmd.value)
        case "pitchbend":
            # WinRT expects 0-16383; our mapper gives -8192…8191 → shift by +8192
            return midi.MidiPitchBendChangeMessage(ch, cmd.value + 8192)
        case "program":
            return midi.MidiProgramChangeMessage(ch, cmd.value)
        case _:
            raise ValueError(f"Unknown MIDI type: {cmd.type}")


_GS_WAVETABLE_KEYWORDS = ("gs", "wavetable", "sintetizador", "microsoft", "sw synth")

async def _open_output_port(device_name_hint: str = "") -> midi.MidiOutPort:
    """
    Open a MIDI output port via WinRT Windows.Devices.Midi.

    Priority order:
      1. Port whose name contains device_name_hint (from settings.yaml).
      2. If hint is empty or not found: any port that is NOT the GS Wavetable
         synth (prefer real/virtual ports over the built-in software synth).
      3. Last resort fallback: Microsoft GS Wavetable — present on every
         Windows 10/11 machine with no extra software needed.
    """
    from winrt.windows.devices.enumeration import DeviceInformation

    selector = midi.MidiOutPort.get_device_selector()
    devices = await type(DeviceInformation).find_all_async_aqs_filter(
        DeviceInformation, selector
    )

    if not devices or len(devices) == 0:
        raise RuntimeError("No MIDI output ports found via Windows MIDI Services.")

    port_names = [d.name for d in devices]

    def _is_gs(name: str) -> bool:
        n = name.lower()
        return any(kw in n for kw in _GS_WAVETABLE_KEYWORDS)

    chosen = None

    # 1 — explicit hint from config
    if device_name_hint:
        for d in devices:
            if device_name_hint.lower() in d.name.lower():
                chosen = d
                break
        if chosen is None:
            logger.warning(
                "device_name '%s' not found. Available: %s",
                device_name_hint, ", ".join(port_names),
            )

    # 2 — first non-GS port (loopMIDI, hardware, DAW virtual port…)
    if chosen is None:
        for d in devices:
            if not _is_gs(d.name):
                chosen = d
                break

    # 3 — GS Wavetable fallback (always present on Windows 10/11)
    if chosen is None:
        chosen = devices[0]
        logger.warning(
            "No preferred MIDI port found. Falling back to: %s", chosen.name
        )

    logger.info(
        "MIDI output -> %s  |  all ports: [%s]",
        chosen.name,
        ", ".join(port_names),
    )
    port = await midi.MidiOutPort.from_id_async(chosen.id)
    return port


class MidiWriter(threading.Thread):
    """
    Consumes (osc_address, osc_args, receive_ns) tuples from *in_queue*,
    maps them to MIDI commands, sends them via WinRT, and records telemetry.
    """

    def __init__(
        self,
        in_queue: queue.Queue,
        stop_event: threading.Event,
        telemetry: Telemetry,
    ) -> None:
        super().__init__(name="midi-writer", daemon=True)
        self._queue = in_queue
        self._stop = stop_event
        self._telemetry = telemetry
        self._port: midi.MidiOutPort | None = None

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        # Each thread needs its own asyncio event loop for WinRT coroutines.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        device_hint = cfg.get("midi", {}).get("device_name", "")
        try:
            self._port = loop.run_until_complete(_open_output_port(device_hint))
        except Exception as exc:
            logger.error("Failed to open MIDI output port: %s", exc)
            return

        logger.info("MIDI writer ready.")

        while not self._stop.is_set():
            try:
                osc_address, osc_args, receive_ns = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            cmd = resolve(osc_address, osc_args)
            if cmd is None:
                self._queue.task_done()
                continue

            try:
                msg = _build_midi_message(cmd)
                self._port.send_message(msg)
                send_ns = time.perf_counter_ns()
                self._telemetry.record(receive_ns, send_ns, osc_address)
            except Exception as exc:
                logger.error("MIDI send error for %s: %s", osc_address, exc)
            finally:
                self._queue.task_done()

        if self._port:
            self._port.close()
        loop.close()
        logger.info("MIDI writer stopped.")
