"""
Dynamic OSC → MIDI mapping engine.

Reads config/mapping.yaml at startup (cached). Supports exact patterns and
fnmatch-style wildcards (e.g. /pad/*/hit). Returns a typed MidiCommand named-
tuple so downstream code never hard-codes message shapes.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Literal

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "mapping.yaml"

MidiType = Literal["cc", "note_on", "note_off", "pitchbend", "program"]


@dataclass(slots=True, frozen=True)
class MidiCommand:
    type: MidiType
    channel: int       # 1-16
    number: int        # CC controller / MIDI note (0 for pitchbend/program)
    value: int         # 0-127, or -8192…8191 for pitchbend


@dataclass(slots=True, frozen=True)
class _Rule:
    pattern: str
    type: MidiType
    channel: int
    number: int
    value_scale: tuple[float, float, int, int] | None  # osc_min, osc_max, midi_min, midi_max


_rules: list[_Rule] = []
_rules_lock = Lock()
_loaded = False


def _load_rules() -> None:
    global _loaded
    with _rules_lock:
        if _loaded:
            return
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        for entry in raw.get("mappings", []):
            vs_raw = entry.get("value_scale")
            vs = tuple(vs_raw) if vs_raw else None
            _rules.append(
                _Rule(
                    pattern=entry["osc_pattern"],
                    type=entry["type"],
                    channel=int(entry["channel"]),
                    number=int(entry.get("number", 0)),
                    value_scale=vs,
                )
            )
        _loaded = True
        logger.info("Loaded %d mapping rules from %s", len(_rules), _CONFIG_PATH)


def _scale(raw: float, vs: tuple[float, float, int, int]) -> int:
    osc_min, osc_max, midi_min, midi_max = vs
    if osc_max == osc_min:
        return midi_min
    ratio = (raw - osc_min) / (osc_max - osc_min)
    scaled = midi_min + ratio * (midi_max - midi_min)
    return int(max(min(scaled, midi_max), midi_min))


def resolve(osc_address: str, osc_args: list) -> MidiCommand | None:
    """
    Match osc_address against the rule list (first-match wins) and return a
    MidiCommand with the value derived from osc_args[0].  Returns None when no
    rule matches.
    """
    if not _loaded:
        _load_rules()

    raw_value: float = float(osc_args[0]) if osc_args else 0.0

    for rule in _rules:
        if osc_address == rule.pattern or fnmatch.fnmatch(osc_address, rule.pattern):
            value = _scale(raw_value, rule.value_scale) if rule.value_scale else int(raw_value)
            return MidiCommand(
                type=rule.type,
                channel=rule.channel,
                number=rule.number,
                value=value,
            )

    logger.debug("No mapping rule for OSC address: %s", osc_address)
    return None
