# High-Performance Secure MIDI-OSC Gateway

A low-latency bridge that receives OSC messages over UDP, authenticates them
with HMAC-SHA256, and forwards translated MIDI events to any Windows MIDI port.
Built with Python 3.11+ and Windows MIDI Services (WinRT).

**Measured latency: ~300 µs end-to-end on loopback.**

```
[TouchOSC / custom app]  →  UDP/OSC  →  [Gateway]  →  MIDI  →  [DAW / Synth]
                                            │
                                     HMAC-SHA256 auth
                                     IP allowlist
                                     Replay protection
                                     Live telemetry CSV
```

---

## Requirements

- Windows 10 / 11 (x64)
- Python 3.11 or newer — [python.org](https://www.python.org/downloads/)
- No C++ compiler needed

> **Optional but recommended:** [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
> (free virtual MIDI cable). Without it the gateway falls back to the Microsoft GS
> Wavetable Synth that ships with every Windows install — you will hear the notes
> but cannot route MIDI to a DAW. loopMIDI creates a virtual cable that any app
> can read.

---

## Quick start (3 steps, no NSSM needed)

```powershell
# 1 — Clone and install dependencies
git clone https://github.com/YOUR_USERNAME/midi-osc-gateway.git
cd midi-osc-gateway
pip install -r requirements.txt

# 2 — Start the gateway (Terminal 1)
python -m src.main

# 3 — Send a test OSC message (Terminal 2)
python tests/osc_client_sim.py --count 10 --delay 0.2 --address /fader/1 --value 0.8
```

You will see in Terminal 1:
```
[midi-writer] INFO  MIDI output -> Microsoft GS Wavetable Synth
[stats]       INFO  received=10 rejected=0 dropped=0 | latency(µs) min=280 avg=340 max=890 [n=10]
```

No configuration needed. The installer auto-generates the HMAC secret and picks
the best available MIDI port.

---

## One-shot installer (includes NSSM Windows service)

Run once from an **Administrator PowerShell**:

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser   # one-time
.\install.ps1
```

This script:
1. Installs all Python dependencies.
2. Generates a cryptographically strong HMAC secret automatically.
3. Detects your MIDI ports and configures the best one.
4. Registers and starts a Windows service via NSSM (if NSSM is in PATH).

To install NSSM first: `winget install NSSM.NSSM`

---

## Verify the service is working

```powershell
.\tests\verify_nssm.ps1
```

Runs 5 automated checks: service status, UDP port open, log file activity,
end-to-end message processing, and telemetry CSV data.

---

## Latency report

After sending some messages, generate a chart:

```powershell
python src/analyzer.py --show     # interactive window
python src/analyzer.py            # saves PNG next to the CSV in /logs
```

---

## MIDI port selection

Edit `config/settings.yaml`:

```yaml
midi:
  device_name: "loopMIDI Port"   # substring match, leave empty for auto
```

| Scenario | Recommended port |
|---|---|
| Just testing, no DAW | Leave empty — uses Microsoft GS Wavetable |
| Route to Ableton / FL / Cubase | loopMIDI Port (install loopMIDI first) |
| Hardware synth via USB-MIDI | The name shown by your MIDI interface |

---

## Security model

Every OSC datagram must carry three extra arguments at the end:

```
/fader/1  [value: 0.8,  ts_sec: 1777298222,  ts_ms: 173,  token: "a3f9..."]
```

The gateway validates in order (cheapest first):

1. **IP allowlist** — configure `security.allowed_ips` in settings.yaml.
2. **Timestamp freshness** — rejects packets older than 5 seconds.
3. **Replay protection** — the same timestamp is never accepted twice.
4. **HMAC-SHA256** — compared with `hmac.compare_digest` (timing-safe).

To add your mobile device's IP: edit `config/settings.yaml` → `allowed_ips`.

---

## OSC → MIDI mapping

Edit `config/mapping.yaml` to define your own controls. No code changes needed.

```yaml
- osc_pattern: "/fader/1"
  type: cc
  channel: 1
  number: 7                        # CC #7 = Volume
  value_scale: [0.0, 1.0, 0, 127] # float 0.0–1.0 → MIDI 0–127

- osc_pattern: "/pad/*/hit"        # wildcard: /pad/1/hit, /pad/2/hit …
  type: note_on
  channel: 10
  number: 36
```

Supported types: `cc`, `note_on`, `note_off`, `pitchbend`, `program`.

---

## Project structure

```
midi-osc-gateway/
├── config/
│   ├── settings.yaml   # Server, MIDI, security, logging config
│   └── mapping.yaml    # OSC address → MIDI translation rules
├── src/
│   ├── main.py         # Entry point — thread orchestration + shutdown
│   ├── receiver.py     # Thread A: UDP socket → validated queue entries
│   ├── security.py     # HMAC-SHA256 + IP allowlist + replay protection
│   ├── mapper.py       # OSC address → MidiCommand (wildcard rules)
│   ├── midi_writer.py  # Thread B: queue → WinRT MIDI output
│   ├── telemetry.py    # µs latency measurement → rotating CSV
│   └── analyzer.py     # CSV → latency stability chart (matplotlib)
├── tests/
│   ├── osc_client_sim.py    # Simulates a mobile OSC controller
│   ├── verify_nssm.ps1      # 5-point automated service health check
│   ├── test_security.py     # Unit tests for HMAC validation
│   └── test_mapper.py       # Unit tests for OSC→MIDI mapping
├── logs/               # gateway.log + latency CSVs (auto-created)
├── install.ps1         # One-shot installer + NSSM service registration
└── requirements.txt
```

---

## Running tests

```powershell
pip install pytest
python -m pytest tests/test_security.py tests/test_mapper.py -v
```

10 tests, ~100 ms.

---

## Tech stack

| Layer | Technology |
|---|---|
| OSC receive | Raw `socket.SOCK_DGRAM` + `python-osc` |
| Security | `hmac` (stdlib) + HMAC-SHA256 |
| OSC→MIDI mapping | Custom engine with `fnmatch` wildcards |
| MIDI output | WinRT `Windows.Devices.Midi` via `winrt` |
| Concurrency | `threading` + `queue.Queue` (producer-consumer) |
| Telemetry | `time.perf_counter_ns()` + rotating CSV |
| Analysis | `pandas` + `matplotlib` |
| Service | NSSM (Non-Sucking Service Manager) |
| Python | 3.11+ (uses `match/case`, `slots=True`, `|` union types) |
