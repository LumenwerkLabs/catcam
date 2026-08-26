# catcam

A pan-controlled camera rig. A Raspberry Pi serves a web UI with a dial; dragging
the dial sends the target angle over USB serial to a Raspberry Pi Pico, which
drives a hobby servo.

```
browser  ──HTTP──▶  Pi (FastAPI)  ──USB serial──▶  Pico (MicroPython)  ──PWM──▶  servo
```

## Layout

| Path                  | What it is                                                        |
| --------------------- | ----------------------------------------------------------------- |
| `pico/servo.py`       | Servo driver: angle → pulse width, plus detach so it stops buzzing |
| `pico/main.py`        | Serial command loop on the Pico (`PAN <n>`, `HOME`)                |
| `pi/server/pico_link.py` | Serial link to the Pico: port discovery, thread-safe send/reply |
| `pi/server/app.py`    | FastAPI app exposing `/api/pan` and `/api/home`                    |
| `pi/static/index.html`| Single-page UI: draggable SVG dial with FOV cone and keyboard support |

## Wiring

The servo signal line goes to **GP15** on the Pico. Power the servo from its own
5 V supply, with ground tied to the Pico's ground.

## Running

Flash the Pico with MicroPython, then copy `pico/servo.py` and `pico/main.py` to
its filesystem (e.g. with `mpremote` or Thonny). It starts the command loop on boot.

On the Pi:

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd pi/server
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open `http://<pi-address>:8000/`. The server finds the Pico automatically by
scanning `/dev/ttyACM*` and `/dev/cu.usbmodem*`.

To test the serial link without the web server:

```sh
python pi/server/pico_link.py     # sweeps 90 → 150 → 90
```

## Serial protocol

Line-oriented, 115200 baud. The Pico replies to every line.

| Sent        | Reply      | Meaning                          |
| ----------- | ---------- | -------------------------------- |
| `PAN <0-180>` | `OK <angle>` | Move to angle (clamped)        |
| `HOME`      | `OK 90`    | Return to center                 |
| anything else | `ERR`    | Unparseable command              |

The Pico only acts on the newest command in its buffer, so a fast drag on the
dial doesn't queue up a backlog of stale moves. After 400 ms without a move it
cuts PWM to the servo to stop the idle jitter.

## HTTP API

| Endpoint     | Body                 | Response          |
| ------------ | -------------------- | ----------------- |
| `POST /api/pan` | `{"angle": 0-180}` | `{"angle": 120}`  |
| `POST /api/home` | –                 | `{"angle": 90}`   |

A `502` means the Pico answered with something other than `OK`.

## Calibration

`pico/main.py` sets the pulse range with `PAN_MIN_US` / `PAN_MAX_US` (550–2450 µs).
If your servo doesn't reach the full sweep, or strains at the ends, use
`Servo.write_us()` from the Pico REPL to find the real limits and update those
constants.

## Not done yet

The video pane in the UI is a placeholder — no camera stream is wired up. Tilt
is unimplemented; only pan exists.
