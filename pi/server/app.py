from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from camera import Camera
from pico_link import PicoLink

STATIC_DIR = '../static'
BOUNDARY = 'frame'
link = None
camera = None


@asynccontextmanager
async def lifespan(app):
    global link, camera
    link = PicoLink()
    print('Pico en', link.address)
    # Sin cámara el pan sigue andando: no tiramos el server abajo.
    try:
        camera = Camera().start()
    except Exception as exc:
        print('sin cámara:', exc)
    yield
    if camera:
        camera.stop()
    link.close()


app = FastAPI(lifespan=lifespan)


class PanRequest(BaseModel):
    angle: int = Field(ge=0, le=180)


class FocusRequest(BaseModel):
    auto: bool = False
    value: Optional[int] = Field(default=None, ge=300, le=650)


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
                jpeg = await queue.get()
                yield (b'--' + BOUNDARY.encode() + b'\r\n'
                       b'Content-Type: image/jpeg\r\n'
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


@app.get('/api/focus')
def get_focus():
    state = _camera().focus()
    if state is None:
        raise HTTPException(503, 'la cámara no está abierta')
    return state


@app.post('/api/focus')
def set_focus(req: FocusRequest):
    cam = _camera()
    try:
        cam.set_focus(value=req.value, auto=req.auto)
    except (OSError, RuntimeError, KeyError) as exc:
        raise HTTPException(502, 'la cámara rechazó el control: {}'.format(exc))
    return cam.focus()


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