# RumoWave

Low-latency bridge that turns any phone or tablet into a general-purpose MIDI
controller over Wi-Fi. Receives OSC messages over UDP and forwards them as MIDI
events to any Windows application — DAW, synthesizer, virtual instrument, live
performance software, lighting controller, game engine, or anything else that
speaks MIDI. Built with Python 3.11+ and Windows MIDI Services (WinRT).

**Measured latency: ~207 µs average, ~143 µs minimum (Wi-Fi, local network).**

```
[Phone / tablet]  --Wi-Fi/UDP-->  [RumoWave]  --MIDI-->  [Any MIDI-capable app]
                                      |
                                 HMAC-SHA256 auth
                                 IP allowlist
                                 Replay protection
                                 Live latency telemetry
```

**Use cases:** control a DAW (FL Studio, Ableton, Cubase...), play a VST
instrument, automate a mixer, trigger clips, control OBS scenes, drive a
lighting rig (via MIDI-to-DMX bridge), send program changes to a hardware
synth, or automate any parameter in any software that accepts MIDI — all from
your phone, with sub-millisecond latency.

---

## What it does

You move a fader on your phone. A MIDI Control Change message arrives in your
target application in under a millisecond. RumoWave handles authentication,
translation, and telemetry — no plugins, no drivers, no latency-inducing
middleware.

Supports any OSC app (TouchOSC, OSC Controller, GyrOSC, custom apps). The
mapping from OSC addresses to MIDI messages is fully configurable in a YAML file
with no code changes.

---

## Requirements

- Windows 10 / 11 (x64)
- Python 3.11 or newer — [python.org](https://www.python.org/downloads/)
- No C++ compiler needed

**Optional but recommended: [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)**
(free, by Tobias Erichsen)

loopMIDI creates a virtual MIDI cable in Windows. Without it RumoWave uses
the built-in Microsoft GS Wavetable Synth — you will hear sound but cannot route
MIDI to a DAW. With loopMIDI, any app (FL Studio, Ableton, OBS, VMPK...) can
receive the MIDI from your phone.

---

## Quick start — test in 3 steps without a phone

> **Recommended:** run `install.ps1` as Administrator — it installs dependencies,
> detects your MIDI port, generates the HMAC secret, and registers the Windows
> service automatically. The manual steps below are for testing without the service.

```powershell
# 1 — Clone and install
git clone https://github.com/raulrumo/rumowave.git
cd rumowave
pip install -r requirements.txt

# 2 — Start RumoWave (Terminal 1)
python -m src.main

# 3 — Send test messages (Terminal 2)
python tests/osc_client_sim.py --count 10 --delay 0.2
```

Expected output in Terminal 1:
```
[udp-receiver] INFO  UDP receiver listening on 0.0.0.0:9000
[midi-writer]  INFO  MIDI output -> Microsoft GS Wavetable Synth
[stats]        INFO  received=10 rejected=0 dropped=0 | latency(µs) min=142 avg=207 max=890 [n=10]
```

---

## Connect your phone

### Step 1 — Find your PC's IP address

```powershell
ipconfig
```

Look for **"Dirección IPv4"** (or "IPv4 Address") under your **Wi-Fi adapter**.
It will look like `192.168.1.XX`. Write it down.

> Your phone and PC must be on the **same Wi-Fi network**. If your phone shows
> an IP starting with a completely different range (e.g. PC is `192.168.1.x`
> and phone is `10.x.x.x`), they are on different networks and packets will not
> reach RumoWave. Connect both to the same router.

### Step 2 — Find your phone's IP address

- **Android:** Settings → Wi-Fi → tap your network name → IP address
- **iPhone:** Settings → Wi-Fi → tap the (i) icon next to your network → IP address

It will look like `192.168.1.YY`.

### Step 3 — Add your phone's IP to RumoWave

Edit `config/settings.yaml`:

```yaml
security:
  require_hmac: false    # for standard OSC apps that don't sign messages
  allowed_ips:
    - "127.0.0.1"
    - "192.168.1.YY"     # your phone's IP
```

### Step 4 — Configure your OSC app

In your OSC app settings (TouchOSC, OSC Controller, etc.):

```
Remote IP:    192.168.1.XX   ← your PC's IP from Step 1
Remote Port:  9000            ← must match RumoWave port
Local Port:   any             ← RumoWave does not send responses
```

### Step 5 — Start RumoWave and move a fader

```powershell
python -m src.main
```

You should immediately see `received=1 rejected=0` in the logs when you move
a control in the app. If you see `rejected=1`, the phone's IP in `allowed_ips`
does not match. Enable DEBUG logging to see the real IP arriving:

```yaml
logging:
  level: "DEBUG"
```

The log will show: `Rejected packet from unlisted IP: 192.168.1.ZZ` — use that
exact IP in `allowed_ips`.

---

## Connect to a DAW (FL Studio, Ableton, Cubase...)

### loopMIDI setup (one-time)

1. Download and install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
2. Open loopMIDI → click **+** → a new port named "loopMIDI Port" appears
3. Edit `config/settings.yaml` → set `device_name: "loopMIDI Port"`
4. Restart RumoWave

### FL Studio

1. Options → MIDI Settings → **Input** tab
2. Find `loopMIDI Port` → click **Enable** → click **Remote control**
3. Close settings
4. Right-click any knob or fader in FL Studio → **"Link to controller"**
5. Move the fader on your phone — FL Studio assigns it automatically

### Ableton Live

1. Preferences → Link / MIDI
2. Under MIDI Ports, find `loopMIDI Port` in the **Input** column
3. Set **Remote** to ON
4. Click the **MIDI** button (top right) to enter MIDI map mode
5. Click any parameter → move the fader on your phone

### OBS Studio

Install the [obs-midi-mg](https://github.com/nhielost/obs-midi-mg) plugin:

1. Tools → midi-mg → Input: `loopMIDI Port`
2. Assign controls to scene switches, volume faders, mute buttons

---

## OSC address mapping

RumoWave only forwards OSC addresses that have a rule in `config/mapping.yaml`.
If your app sends an address that is not listed there, it will be silently ignored.
Adding new controls requires editing that file — no Python changes needed, just YAML.

### How to add a new control

**Step 1 — Find the OSC address your app sends.** Enable DEBUG logging in
`config/settings.yaml`, move the control, and look for a line like:
```
DEBUG  No mapping rule for OSC address: /1/fader1
```

**Step 2 — Add a rule to `config/mapping.yaml`:**
```yaml
- osc_pattern: "/1/fader1"
  type: cc
  channel: 1
  number: 10              # any CC number 0-127
  value_scale: [0.0, 1.0, 0, 127]
```

**Step 3 — Restart RumoWave** (or `nssm restart RumoWave` if running as a service).

### Default rules included

```yaml
- osc_pattern: "/fader/1"
  type: cc
  channel: 1
  number: 7                         # CC #7 = Volume (MIDI standard)
  value_scale: [0.0, 1.0, 0, 127]  # rescales 0.0-1.0 float to 0-127 int

- osc_pattern: "/control/*"         # wildcard: /control/1, /control/2 ...
  type: cc
  channel: 1
  number: 20
  value_scale: [0.0, 1.0, 0, 127]

- osc_pattern: "/pad/*/hit"
  type: note_on
  channel: 10                       # channel 10 = drums (MIDI convention)
  number: 36                        # note 36 = bass drum
  value_scale: [0.0, 1.0, 0, 127]
```

**Supported types:** `cc`, `note_on`, `note_off`, `pitchbend`, `program`

---

## Security

Two modes, controlled by `security.require_hmac` in settings.yaml:

| Mode | require_hmac | Compatible with |
|---|---|---|
| Full | `true` | `osc_client_sim.py`, custom apps with signing |
| Plain OSC | `false` | TouchOSC, OSC Controller, any standard OSC app |

In both modes the **IP allowlist** is always enforced.

Full mode adds timestamp freshness check, replay protection, and HMAC-SHA256
verification using `hmac.compare_digest` (timing-safe — immune to timing attacks).

---

## Run as a Windows service (NSSM)

Install [NSSM](https://nssm.cc):
```powershell
winget install NSSM.NSSM
```

Then run the one-shot installer as Administrator:
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

RumoWave will start automatically on boot, restart on crash, and write logs
to `logs/service_stdout.log`. Monitor in real time:
```powershell
Get-Content logs\service_stdout.log -Wait -Tail 20
```

Verify everything works:
```powershell
.\tests\verify_nssm.bat
```

Service management:
```powershell
nssm start   RumoWave
nssm stop    RumoWave
nssm restart RumoWave   # after editing settings.yaml
nssm status  RumoWave
```

---

## Latency analysis

After sending messages, generate a stability report:

```powershell
python src/analyzer.py --show     # interactive chart
python src/analyzer.py            # saves PNG to logs/
```

---

## Project structure

```
rumowave/
├── config/
│   ├── settings.yaml        # server, MIDI port, security, logging
│   └── mapping.yaml         # OSC address -> MIDI translation rules
├── src/
│   ├── main.py              # entry point, thread orchestration, shutdown
│   ├── receiver.py          # Thread A: UDP socket -> validated queue
│   ├── security.py          # HMAC-SHA256, IP allowlist, replay protection
│   ├── mapper.py            # OSC address -> MidiCommand (wildcard engine)
│   ├── midi_writer.py       # Thread B: queue -> WinRT MIDI output
│   ├── telemetry.py         # nanosecond latency measurement -> CSV
│   └── analyzer.py          # CSV -> latency chart (matplotlib + pandas)
├── tests/
│   ├── osc_client_sim.py    # signed OSC client simulator
│   ├── verify_nssm.bat      # double-click service health check
│   ├── test_security.py     # HMAC validation unit tests
│   └── test_mapper.py       # OSC->MIDI mapping unit tests
├── install.ps1              # one-shot installer + NSSM registration
└── requirements.txt
```

---

## Run tests

```powershell
pip install pytest
python -m pytest tests/test_security.py tests/test_mapper.py -v
```

10 tests, ~100 ms.

---

## Tech stack

| Layer | Technology |
|---|---|
| OSC receive | Raw `socket.SOCK_DGRAM` — no abstraction overhead |
| Security | `hmac` stdlib — HMAC-SHA256, timing-safe comparison |
| Mapping | Custom wildcard engine (`fnmatch`) — zero dependencies |
| MIDI output | WinRT `Windows.Devices.Midi` via `winrt` Python binding |
| Concurrency | Producer-consumer: `threading` + bounded `queue.Queue` |
| Telemetry | `time.perf_counter_ns()` — nanosecond hardware counter |
| Analysis | `pandas` + `matplotlib` |
| Service | NSSM — Windows service wrapper |
| Python | 3.11+ (`match/case`, `slots=True`, `X | Y` union types) |
