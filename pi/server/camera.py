"""Captura MJPEG de la webcam USB.

Un solo hilo abre el device, porque /dev/video0 no se puede abrir dos veces.
Los clientes se suscriben al último frame: el que va lento saltea frames en vez
de acumular backlog.
"""

import threading
import time

from linuxpy.video.device import Device, VideoCapture

# Ruta estable: el número de /dev/video0 depende del orden de enumeración.
DEVICE = ('/dev/v4l/by-id/'
          'usb-Anker_PowerConf_C200_Anker_PowerConf_C200_ACNV9P1F07509355-video-index0')
SIZE = (1280, 720)
FPS = 15
RETRY_S = 2.0

# La C200 trae autofoco y auto-exposición: al panear se ponen a buscar y se ve
# el pumping. focus_absolute va de 300 a 650; probá los dos extremos y quedate
# con el que enfoque a distancia de habitación.
CONTROLS = {
    'power_line_frequency': 1,          # 1 = 50 Hz, 2 = 60 Hz
    'focus_automatic_continuous': 0,
    'focus_absolute': 300,
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
            with cap:
                self.error = None
                for frame in cap:
                    if self._stop.is_set():
                        return
                    self._publish(bytes(frame))
        finally:
            dev.close()

    def _publish(self, jpeg):
        with self._cond:
            self._frame = jpeg
            self._seq += 1
            self._cond.notify_all()

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
