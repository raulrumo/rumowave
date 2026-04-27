# RumoWave — Architecture Specification

## Overview

A low-latency, thread-safe gateway that receives OSC messages over UDP, validates them
cryptographically, and forwards translated MIDI events to Windows MIDI Services (WMS).

Target latency budget: < 2 ms end-to-end (UDP receive → MIDI output) on loopback.

---

## System Architecture

```
[OSC Client]
     │  UDP datagrams
     ▼
┌─────────────────────────────────────────────────────────┐
│                  Gateway Process                         │
│                                                         │
│  ┌──────────────────┐     Queue      ┌───────────────┐  │
│  │  UDP Receiver    │ ─────────────► │  MIDI Writer  │  │
│  │  Thread          │  (asyncio or   │  Thread       │  │
│  │  (socket native) │   queue.Queue) │  (WMS / rtmidi│  │
│  └────────┬─────────┘                └───────────────┘  │
│           │                                             │
│  ┌────────▼─────────┐                                   │
│  │  Security Layer  │                                   │
│  │  HMAC-SHA256     │                                   │
│  │  IP allowlist    │                                   │
│  └──────────────────┘                                   │
└─────────────────────────────────────────────────────────┘
```

---

## Thread Model

### Thread 1 — UDP Receiver (`src/receiver.py`)

- Uses a **raw `socket.socket(AF_INET, SOCK_DGRAM)`** bound to the configured port.
- Sets `SO_RCVBUF` to 4 MB to absorb bursts without kernel drops.
- Tight `recvfrom` loop; no async overhead in the hot path.
- On each datagram: calls `security.validate_request(ip, token)` before parsing.
- Valid packets are pushed onto a `queue.Queue(maxsize=1024)` (bounded to apply
  back-pressure instead of unbounded memory growth).
- Invalid packets increment a counter and are silently dropped (no response to
  prevent amplification attacks).

### Thread 2 — MIDI Writer (`src/midi_writer.py`)

- Blocks on `queue.Queue.get()`.
- Translates OSC address + arguments to MIDI messages via `src/translator.py`.
- Sends via **Windows MIDI Services** (WMS) when available, falling back to
  `python-rtmidi` for compatibility.
- WMS integration path: `winrt` + `Windows.Devices.Midi2` namespace (requires
  Windows 11 22H2+ and the WMS SDK).

### Inter-thread Communication

- `queue.Queue` is the only shared state; no locks needed elsewhere.
- Main thread owns startup, config loading, and graceful shutdown via
  `threading.Event`.

---

## Security Layer (`src/security.py`)

### HMAC-SHA256 Validation

Every OSC datagram carries an HMAC-SHA256 token computed over:

```
HMAC-SHA256(secret_key, sender_ip + ":" + osc_address + ":" + str(timestamp_ms))
```

The receiver recomputes the digest and compares with `hmac.compare_digest` to
prevent timing attacks.

### Replay Protection

- Datagrams include a millisecond-precision Unix timestamp.
- Packets older than `security.replay_window_ms` (default 5000 ms) are rejected.
- A sliding window `set` of seen timestamps per IP evicts entries older than the
  window on each check.

### IP Allowlist

- Configured in `config/settings.yaml` under `security.allowed_ips`.
- Checked before HMAC to short-circuit unknown sources cheaply.

---

## OSC → MIDI Translation (`src/translator.py`)

| OSC Address Pattern         | MIDI Message          | Argument Mapping                  |
|-----------------------------|-----------------------|-----------------------------------|
| `/midi/note_on/{ch}`        | Note On               | args[0]=note, args[1]=velocity    |
| `/midi/note_off/{ch}`       | Note Off              | args[0]=note, args[1]=velocity    |
| `/midi/cc/{ch}`             | Control Change        | args[0]=controller, args[1]=value |
| `/midi/pitchbend/{ch}`      | Pitch Bend            | args[0]=value (−8192…8191)        |
| `/midi/program/{ch}`        | Program Change        | args[0]=program                   |

Channel `{ch}` is extracted from the OSC address path (1-indexed, clamped to 1–16).

---

## Configuration (`config/settings.yaml`)

All tuneable parameters live in a single YAML file loaded at startup.
See `config/settings.yaml` for annotated defaults.

---

## Dependency Analysis & Build Notes

### python-osc 1.10.2
Pure Python — installs from wheel on any CPython version. No issues.

### mido 1.3.3
Pure Python — installs from wheel. Optional `python-rtmidi` backend auto-detected.

### python-rtmidi 1.5.8 — **requires source compilation on Python 3.14**
PyPI ships no pre-built wheels for CPython 3.14 (as of 2026-04). The package uses
Meson as its build system and wraps the RtMidi C++ library.

**Prerequisites before `pip install python-rtmidi`:**

1. Install **Visual Studio 2022 Build Tools** (C++ workload) — provides `cl.exe`.
   Alternatively install **LLVM/Clang** and add it to PATH.
2. Confirm `meson` and `ninja` are available (`pip install meson ninja`).
3. Run the install from a **Developer Command Prompt** (or activate the VS
   environment with `vcvarsall.bat x64`) so Meson can locate the compiler.

```powershell
# From a VS Developer PowerShell / Developer Command Prompt:
pip install meson ninja
pip install --no-binary python-rtmidi python-rtmidi==1.5.8
```

**Alternative — Windows MIDI Services (WMS) native path:**
WMS (`Windows.Devices.Midi2`) is available in-box on Windows 11 22H2+ and
exposes a WinRT API. Use `pip install winrt-Windows.Devices.Midi2` to bind it
from Python. This path has zero C++ build requirements and may achieve lower
latency than RtMidi by bypassing the legacy WinMM stack entirely.

---

## Directory Layout

```
midi-osc-gateway/
├── config/
│   └── settings.yaml          # All runtime configuration
├── docs/
│   └── (architecture diagrams, API reference)
├── logs/                      # Rotating log files (gitignored)
├── src/
│   ├── __init__.py
│   ├── main.py                # Entry point; thread orchestration
│   ├── receiver.py            # UDP receiver thread
│   ├── security.py            # HMAC-SHA256 validation layer
│   ├── translator.py          # OSC → MIDI message mapping
│   └── midi_writer.py         # MIDI output thread (WMS / rtmidi)
├── tests/
│   ├── test_security.py
│   ├── test_translator.py
│   └── test_receiver.py
├── requirements.txt
└── SPEC.md
```

---

## Performance Targets

| Metric                        | Target          |
|-------------------------------|-----------------|
| UDP → MIDI latency (loopback) | < 2 ms p99      |
| Max sustained throughput      | 10 000 msg/s    |
| HMAC validation overhead      | < 50 µs/msg     |
| Memory footprint              | < 50 MB RSS     |
