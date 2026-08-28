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
| `pi/server/audio.py`  | Microphone capture off the C200, one thread, many subscribers      |
| `pi/catcam.service`   | systemd unit: right cwd, device groups, restarts on its own        |
| `pi/server/bench.py`  | Measures where video and control latency is actually spent         |
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

To keep it running without a terminal, install the unit:

```sh
sudo cp pi/catcam.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now catcam
journalctl -u catcam -f
```

Open `http://<pi-address>:8000/`. The server finds the Pico automatically by
scanning `/dev/ttyACM*` and `/dev/cu.usbmodem*`.

### Keeping it running

`pi/catcam.service` runs the server under systemd, so it comes back after a
reboot and restarts if it falls over. Edit `User`, `WorkingDirectory` and
`ExecStart` if your paths differ, then:

```sh
sudo cp pi/catcam.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now catcam
```

```sh
systemctl status catcam       # ¿está arriba?
journalctl -u catcam -f       # el log en vivo, en lugar de la terminal
sudo systemctl restart catcam # después de tocar el código
```

`WorkingDirectory` has to be `pi/server`: `STATIC_DIR` is `../static` and the
imports are flat, so the cwd is part of the configuration. `SupplementaryGroups`
and `PrivateDevices=no` are what keep `/dev/video*` and `/dev/snd` visible —
without them the camera and the microphone silently fail to open.

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
| `WS /api/audio` | binary PCM up, `{"talk": bool}` | JSON format header, then raw s16le PCM chunks down |
| `GET /api/stats` | –                 | capture counters, frame age, subscriber count |
| `GET /api/controls` | –              | state of every adjustable camera control |
| `POST /api/controls/{name}` | `{"auto": true}` or `{"value": n}` | the control's new state |

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
whole frames to reach `FPS` — meaning the achievable rates are 30/N: 30, 15,
10, 7.5. Asking for 12 silently gives you 10, so the effective rate is printed
at startup.

15 fps is the measured sweet spot, at ~170 KB a frame and ~22 Mbit/s. Going to
30 needs ~40 Mbit/s, which is more than the wifi carries: video drops to 3 fps,
camera controls go from 66 ms to 1.3 s, and the Pi→Pico link starts timing out,
since the stream and the servo commands share one radio. Going below 15 doesn't
help either — a camera control costs `max(frame gap, ~66 ms)`, because a UVC
control write on a streaming device runs about 4x slower than on an idle one.
Use `bench.py` after changing any of this.

The device is opened once, by a single capture thread, because a V4L2 node
can't be opened twice. Viewers subscribe to the latest frame and a slow one
skips frames rather than accumulating a backlog.

Your user must be in the `video` group (`sudo usermod -aG video $USER`, then log
out and back in). Under systemd, also set `SupplementaryGroups=video` and
`PrivateDevices=no` — the latter hides `/dev/video*` outright.

Autofocus is pinned off in `CONTROLS`, since it hunts on every pan. Set
`power_line_frequency` to `2` if you're on 60 Hz mains.

The `ADJUSTABLE` table in `camera.py` decides which UVC controls the UI can
drive live — focus (with an auto toggle), zoom, and tilt. The browser builds
its sliders from `/api/controls`, so adding a control there needs no frontend
change. Whatever you set is written back into `CONTROLS`, so it survives the
camera being unplugged and reconnecting.

Zoom and tilt are digital, done by cropping the sensor: tilt is `±10°` in 1°
steps and generally only bites once you're zoomed past 1×, since at 1× there's
no margin left to crop into. `pan_absolute` exists too but isn't exposed — the
servo already pans 0–180°.

## Audio

The C200's microphone shows up as an ALSA capture device. `audio.py` shells out
to `arecord` for 16 kHz mono s16le and fans the chunks out over a WebSocket —
about 256 kbit/s, roughly 1% of what the video costs, so it isn't compressed.
The device is addressed by name (`plughw:CARD=C200,DEV=0`) because card numbers
move between reboots.

Run `python audio.py` for a level meter that proves the mic works without the
web server in the way.

Playback in the browser sits behind the **Escuchar** button — browsers refuse to
start audio without a user gesture. Chunks are scheduled back to back against
the `AudioContext` clock with a 150 ms cushion, and resync if that cushion runs
out. Audio and video are separate streams with no sync between them, so they
drift by ~100 ms.

The camera has **no speaker** — `aplay -l` lists only HDMI and the Pi's 3.5 mm
jack — so talkback plays through `plughw:CARD=Headphones,DEV=0`, which needs a
powered speaker in that jack.

Talkback is push-to-talk over the same WebSocket: the browser captures at
16 kHz through an `AudioWorklet` and sends raw PCM up while the button is held.
It's half duplex on purpose — while you talk, the server stops sending the
microphone back, so you don't hear yourself delayed and the two ends can't
feed back. Writes to `aplay` go through a bounded queue on their own thread,
because a blocking pipe write from the event loop would stall every other
request.

The browser only exposes a microphone in a **secure context**, so talkback
needs HTTPS (`tailscale serve`) or `localhost` on the Pi itself. Over plain
`http://` from another machine it fails silently, with no permission prompt.

## Not done yet

Tilt is unimplemented; only pan exists. The C200 does expose `tilt_absolute`
over UVC (±10°, digital), which would give a limited tilt with no second servo.
