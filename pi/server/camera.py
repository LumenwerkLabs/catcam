"""Captura MJPEG de la webcam USB.

Un solo hilo abre el device, porque /dev/video0 no se puede abrir dos veces.
Los clientes se suscriben al último frame: el que va lento saltea frames en vez
de acumular backlog.
"""

import asyncio
import threading
import time

from linuxpy.video.device import Device, VideoCapture

# Ruta estable: el número de /dev/video0 depende del orden de enumeración.
DEVICE = ('/dev/v4l/by-id/'
          'usb-Anker_PowerConf_C200_Anker_PowerConf_C200_ACNV9P1F07509355-video-index0')
SIZE = (1280, 720)
FPS = 12        # recortado por software, ver _capture
RETRY_S = 2.0

# La C200 trae autofoco y auto-exposición: al panear se ponen a buscar y se ve
# el pumping. focus_absolute va de 300 a 650; probá los dos extremos y quedate
# con el que enfoque a distancia de habitación.
FOCUS_MIN, FOCUS_MAX = 300, 650

CONTROLS = {
    'power_line_frequency': 1,          # 1 = 50 Hz, 2 = 60 Hz
    'focus_automatic_continuous': 0,
    'focus_absolute': 500,
}


class Camera:
    def __init__(self, device=DEVICE, size=SIZE, fps=FPS, controls=None):
        self.device = device
        self.size = size
        self.fps = fps
        self.controls = CONTROLS if controls is None else controls
        self.error = None
        self._frame = None
        self._seq = 0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = None
        self._subs = []
        self._dev = None
        self._devlock = threading.Lock()

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    # -- captura ---------------------------------------------------------

    def _run(self):
        while not self._stop.is_set():
            try:
                self._capture()
            except Exception as exc:
                self.error = exc
                print('camera:', exc)
                self._stop.wait(RETRY_S)   # desconexión, device ocupado, etc.

    def _apply_controls(self, dev):
        for name, value in self.controls.items():
            try:
                dev.controls[name].value = value
            except Exception as exc:
                print('camera: no pude fijar {} = {} ({})'.format(name, value, exc))

    def _capture(self):
        dev = Device(self.device)
        dev.open()
        try:
            self._apply_controls(dev)
            cap = VideoCapture(dev)
            cap.set_format(self.size[0], self.size[1], 'MJPG')
            cap.set_fps(self.fps)
            with self._devlock:
                self._dev = dev
            with cap:
                self.error = None
                interval = 1.0 / self.fps if self.fps else 0.0
                last = 0.0
                for frame in cap:
                    if self._stop.is_set():
                        return
                    # La C200 sólo entrega 30 fps: no hay otro intervalo que
                    # negociar, así que recortamos acá. Cada frame son ~130 KB,
                    # o sea ~31 Mbit/s sin recorte.
                    now = time.monotonic()
                    if now - last < interval:
                        continue
                    last = now
                    self._publish(bytes(frame))
        finally:
            with self._devlock:
                self._dev = None
            dev.close()

    def _publish(self, jpeg):
        with self._cond:
            self._frame = jpeg
            self._seq += 1
            self._cond.notify_all()
            subs = list(self._subs)
        for loop, queue in subs:
            loop.call_soon_threadsafe(self._offer, queue, jpeg)

    # -- suscripción asyncio (para el endpoint de streaming) --------------

    def subscribe(self):
        """Cola con el último frame. Un cliente lento saltea, no acumula."""
        entry = (asyncio.get_running_loop(), asyncio.Queue(maxsize=1))
        with self._cond:
            self._subs.append(entry)
        return entry[1]

    def unsubscribe(self, queue):
        with self._cond:
            self._subs = [s for s in self._subs if s[1] is not queue]

    @staticmethod
    def _offer(queue, jpeg):
        if queue.full():
            try:
                queue.get_nowait()      # tiramos el viejo, mandamos el nuevo
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(jpeg)
        except asyncio.QueueFull:
            pass

    # -- foco -------------------------------------------------------------

    def focus(self):
        """Estado actual, o None si la cámara no está abierta."""
        with self._devlock:
            if self._dev is None:
                return None
            auto = self._dev.controls['focus_automatic_continuous'].value
            value = self._dev.controls['focus_absolute'].value
        return {'auto': bool(auto), 'value': value,
                'min': FOCUS_MIN, 'max': FOCUS_MAX}

    def set_focus(self, value=None, auto=None):
        """focus_absolute está inactive mientras el AF corre: hay que apagarlo
        primero. Guardamos lo elegido en self.controls para que sobreviva a una
        reconexión de la cámara."""
        with self._devlock:
            if self._dev is None:
                raise RuntimeError('la cámara no está abierta')
            if auto:
                self._dev.controls['focus_automatic_continuous'].value = 1
                self.controls['focus_automatic_continuous'] = 1
            else:
                self._dev.controls['focus_automatic_continuous'].value = 0
                self.controls['focus_automatic_continuous'] = 0
                if value is not None:
                    value = max(FOCUS_MIN, min(FOCUS_MAX, int(value)))
                    self._dev.controls['focus_absolute'].value = value
                    self.controls['focus_absolute'] = value

    # -- consumo ---------------------------------------------------------

    def snapshot(self):
        with self._cond:
            return self._frame

    def frames(self, timeout=5.0):
        """Generador del último frame. El cliente lento saltea, no acumula."""
        seen = -1
        while not self._stop.is_set():
            with self._cond:
                if not self._cond.wait_for(lambda: self._seq != seen, timeout):
                    continue
                seen, frame = self._seq, self._frame
            if frame:
                yield frame


if __name__ == '__main__':
    cam = Camera().start()
    print('abriendo', cam.device)
    n, t0 = 0, time.time()
    for jpeg in cam.frames():
        n += 1
        print('frame {:2d}  {:7d} bytes'.format(n, len(jpeg)))
        if n == 20:
            break
    dt = time.time() - t0
    print('{} frames en {:.1f}s -> {:.1f} fps'.format(n, dt, n / dt))
    with open('/tmp/catcam.jpg', 'wb') as fh:
        fh.write(cam.snapshot())
    print('último frame en /tmp/catcam.jpg')
    cam.stop()
