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
| `pi/server/camera.py` | MJPEG capture from the USB webcam: one thread, many subscribers    |
| `pi/server/app.py`    | FastAPI app exposing the pan, home, stream and snapshot endpoints  |
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
| `GET /api/stream` | –                | MJPEG (`multipart/x-mixed-replace`) |
| `GET /api/snapshot` | –              | a single `image/jpeg` |

A `502` means the Pico answered with something other than `OK`.

## Calibration

`pico/main.py` sets the pulse range with `PAN_MIN_US` / `PAN_MAX_US` (550–2450 µs).
If your servo doesn't reach the full sweep, or strains at the ends, use
`Servo.write_us()` from the Pico REPL to find the real limits and update those
constants.

## Camera

An Anker PowerConf C200 on USB, at 1280x720. The camera encodes MJPEG onboard,
so frames are passed through to the browser without ever being decoded — the Pi
spends almost no CPU on video. It only offers 30 fps, so `camera.py` drops
frames in software to reach `FPS`; at ~130 KB a frame, 12 fps is about
12 Mbit/s.

The device is opened once, by a single capture thread, because a V4L2 node
can't be opened twice. Viewers subscribe to the latest frame and a slow one
skips frames rather than accumulating a backlog.

Your user must be in the `video` group (`sudo usermod -aG video $USER`, then log
out and back in). Under systemd, also set `SupplementaryGroups=video` and
`PrivateDevices=no` — the latter hides `/dev/video*` outright.

Autofocus is pinned off in `CONTROLS`, since it hunts on every pan. Set
`power_line_frequency` to `2` if you're on 60 Hz mains.

## Not done yet

Tilt is unimplemented; only pan exists. The C200 does expose `tilt_absolute`
over UVC (±10°, digital), which would give a limited tilt with no second servo.
