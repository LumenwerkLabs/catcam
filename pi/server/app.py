import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from audio import Microphone, Speaker
from camera import ADJUSTABLE, Camera
from pico_link import PicoLink

STATIC_DIR = '../static'
BOUNDARY = 'frame'
link = None
camera = None
mic = None
speaker = None


@asynccontextmanager
async def lifespan(app):
    global link, camera, mic, speaker
    link = PicoLink()
    print('Pico en', link.address)
    # Sin cámara el pan sigue andando: no tiramos el server abajo.
    try:
        camera = Camera().start()
    except Exception as exc:
        print('sin cámara:', exc)
    try:
        mic = Microphone().start()
    except Exception as exc:
        print('sin micrófono:', exc)
    speaker = Speaker()
    yield
    if speaker:
        speaker.close()
    if mic:
        mic.stop()
    if camera:
        camera.stop()
    link.close()


app = FastAPI(lifespan=lifespan)


class PanRequest(BaseModel):
    angle: int = Field(ge=0, le=180)


class ControlRequest(BaseModel):
    auto: bool = False
    value: Optional[int] = None


@app.post('/api/pan')
def set_pan(req: PanRequest):
    reply = link.pan(req.angle)
    if not reply.startswith('OK'):
        raise HTTPException(502, 'el Pico respondió: {!r}'.format(reply))
    return {'angle': int(reply.split()[1])}


@app.post('/api/home')
def go_home():
    reply = link.home()
    if not reply.startswith('OK'):
        raise HTTPException(502, 'el Pico respondió: {!r}'.format(reply))
    return {'angle': int(reply.split()[1])}


@app.get('/api/stream')
async def stream():
    if camera is None:
        raise HTTPException(503, 'no hay cámara')
    queue = camera.subscribe()

    async def parts():
        try:
            while True:
                jpeg, stamped = await queue.get()
                # Edad del frame al salir: si es chica, lo que se demora está
                # río abajo (buffer del socket, red, browser).
                age = int((time.monotonic() - stamped) * 1000)
                yield (b'--' + BOUNDARY.encode() + b'\r\n'
                       b'Content-Type: image/jpeg\r\n'
                       b'X-Frame-Age: ' + str(age).encode() + b'\r\n'
                       b'Content-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n'
                       + jpeg + b'\r\n')
        finally:
            camera.unsubscribe(queue)

    return StreamingResponse(
        parts(),
        media_type='multipart/x-mixed-replace; boundary=' + BOUNDARY,
        headers={'Cache-Control': 'no-store'},
    )


def _camera():
    if camera is None:
        raise HTTPException(503, 'no hay cámara')
    return camera


@app.get('/api/controls')
def get_controls():
    state = _camera().controls_state()
    if any(v is None for v in state.values()):
        raise HTTPException(503, 'la cámara no está abierta')
    return state


@app.post('/api/controls/{name}')
def set_control(name: str, req: ControlRequest):
    if name not in ADJUSTABLE:
        raise HTTPException(404, 'no existe el control {!r}'.format(name))
    cam = _camera()
    try:
        state = cam.set_control(name, value=req.value, auto=req.auto)
    except (OSError, RuntimeError, KeyError) as exc:
        raise HTTPException(502, 'la cámara rechazó el control: {}'.format(exc))
    # set_control ya devuelve el estado; releerlo cuesta otro ioctl de ~50 ms.
    return state if state.get('value') is not None else cam.control(name)


@app.websocket('/api/audio')
async def audio_socket(ws: WebSocket):
    await ws.accept()
    if mic is None:
        await ws.close(code=1011, reason='no hay micrófono')
        return
    await ws.send_json({'rate': mic.rate, 'channels': mic.channels,
                        'format': 's16le', 'talkback': speaker is not None})
    queue = mic.subscribe()
    talking = False

    async def downlink():
        # Mientras el cliente habla no le mandamos el micrófono: se escucharía
        # a sí mismo con retardo, y encima realimenta si comparten ambiente.
        while True:
            pcm = await queue.get()
            if not talking:
                await ws.send_bytes(pcm)

    async def uplink():
        nonlocal talking
        while True:
            msg = await ws.receive()
            if msg['type'] == 'websocket.disconnect':
                return
            text = msg.get('text')
            if text is not None:
                talking = bool(json.loads(text).get('talk'))
                if speaker:
                    speaker.open() if talking else speaker.close()
            elif msg.get('bytes') and speaker and talking:
                speaker.write(msg['bytes'])

    down = asyncio.create_task(downlink())
    try:
        await uplink()
    except WebSocketDisconnect:
        pass
    finally:
        down.cancel()
        mic.unsubscribe(queue)
        if speaker and talking:
            speaker.close()


@app.get('/api/stats')
def get_stats():
    stats = _camera().stats()
    stats['audio'] = mic.stats() if mic else None
    return stats


@app.get('/api/snapshot')
def snapshot():
    jpeg = camera.snapshot() if camera else None
    if jpeg is None:
        raise HTTPException(503, 'todavía no hay imagen')
    return Response(jpeg, media_type='image/jpeg',
                    headers={'Cache-Control': 'no-store'})


@app.get('/')
def index():
    return FileResponse(STATIC_DIR + '/index.html')


app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')