from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pico_link import PicoLink

STATIC_DIR = '../static'
link = None


@asynccontextmanager
async def lifespan(app):
    global link
    link = PicoLink()
    print('Pico en', link.port)
    yield
    link.close()


app = FastAPI(lifespan=lifespan)


class PanRequest(BaseModel):
    angle: int = Field(ge=0, le=180)


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


@app.get('/')
def index():
    return FileResponse(STATIC_DIR + '/index.html')


app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')