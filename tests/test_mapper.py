"""Tests for the dynamic OSC → MIDI mapping engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.mapper as mapper_mod
from src.mapper import MidiCommand, resolve


def setup_function():
    # Reset module state between tests
    mapper_mod._rules.clear()
    mapper_mod._loaded = False


def test_exact_fader_maps_to_cc():
    cmd = resolve("/fader/1", [0.5])
    assert cmd is not None
    assert cmd.type == "cc"
    assert cmd.channel == 1
    assert cmd.number == 7
    assert cmd.value == 63  # 0.5 scaled to 0-127


def test_wildcard_fader_fallback():
    cmd = resolve("/fader/99", [1.0])
    assert cmd is not None
    assert cmd.type == "cc"
    assert cmd.number == 20


def test_pitch_bend_scaling():
    cmd = resolve("/pitch", [1.0])
    assert cmd is not None
    assert cmd.type == "pitchbend"
    assert cmd.value == 8191


def test_unknown_address_returns_none():
    cmd = resolve("/unknown/address", [42])
    assert cmd is None


def test_pad_hit():
    cmd = resolve("/pad/3/hit", [0.8])
    assert cmd is not None
    assert cmd.type == "note_on"
    assert cmd.channel == 10
