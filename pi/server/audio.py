"""Captura del micrófono de la C200.

Mismo patrón que camera.py: un solo hilo lee el device y los clientes se
suscriben. El trabajo pesado lo hace arecord, así no sumamos dependencias
de audio al proyecto.
"""

import asyncio
import queue as queuelib
import subprocess
import threading

# Por nombre y no plughw:3,0: el número de card se mueve entre reboots, igual
# que pasaba con /dev/video0.
DEVICE = 'plughw:CARD=C200,DEV=0'
# La C200 no tiene parlante: la salida es el jack de 3,5 de la Pi.
OUT_DEVICE = 'plughw:CARD=Headphones,DEV=0'
RATE = 16000        # voz clara a 256 kbit/s, ~1% de lo que gasta el video
CHANNELS = 1
CHUNK_MS = 60
CHUNK = RATE * CHANNELS * 2 * CHUNK_MS // 1000   # s16le: 2 bytes por muestra
RETRY_S = 2.0
BACKLOG = 10        # ~600 ms; más que eso ya es retardo, no colchón


class Microphone:
    def __init__(self, device=DEVICE, rate=RATE, channels=CHANNELS):
        self.device = device
        self.rate = rate
        self.channels = channels
        self.error = None
        self.chunks = 0
        self.last = None
        self._subs = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc = None
        self._thread = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._kill()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _kill(self):
        proc, self._proc = self._proc, None
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                pass

    def _run(self):
        while not self._stop.is_set():
            try:
                self._capture()
            except Exception as exc:
                self.error = exc
                print('audio:', exc)
            self._kill()
            if not self._stop.is_set():
                self._stop.wait(RETRY_S)   # micrófono desconectado, device ocupado

    def _capture(self):
        cmd = ['arecord', '-D', self.device, '-f', 'S16_LE', '-r', str(self.rate),
               '-c', str(self.channels), '-t', 'raw', '-q']
        # start_new_session lo saca del grupo de procesos de la terminal: si no,
        # un Ctrl-C sobre uvicorn le pega a arecord y ensucia la salida.
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE,
                                      start_new_session=True)
        self.error = None
        while not self._stop.is_set():
            pcm = self._proc.stdout.read(CHUNK)
            if not pcm:
                err = self._proc.stderr.read().decode('utf-8', 'replace').strip()
                raise RuntimeError(err or 'arecord terminó solo')
            self.chunks += 1
            self.last = pcm
            self._publish(pcm)

    def _publish(self, pcm):
        with self._lock:
            subs = list(self._subs)
        for loop, queue in subs:
            loop.call_soon_threadsafe(self._offer, queue, pcm)

    @staticmethod
    def _offer(queue, pcm):
        if queue.full():
            try:
                queue.get_nowait()      # es audio en vivo: tiramos lo viejo
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(pcm)
        except asyncio.QueueFull:
            pass

    def subscribe(self):
        entry = (asyncio.get_running_loop(), asyncio.Queue(maxsize=BACKLOG))
        with self._lock:
            self._subs.append(entry)
        return entry[1]

    def unsubscribe(self, queue):
        with self._lock:
            self._subs = [s for s in self._subs if s[1] is not queue]

    def stats(self):
        return {'chunks': self.chunks, 'rate': self.rate,
                'channels': self.channels, 'subscribers': len(self._subs),
                'error': None if self.error is None else str(self.error)}


class Speaker:
    """Reproduce PCM del navegador por el jack de la Pi.

    Las escrituras entran desde el event loop, así que no pueden bloquear: van
    a una cola y un hilo se las pasa a aplay. Si el cliente manda más rápido de
    lo que el device consume, se pierde lo viejo en vez de trabar el server.
    """

    def __init__(self, device=OUT_DEVICE, rate=RATE, channels=CHANNELS):
        self.device = device
        self.rate = rate
        self.channels = channels
        self.error = None
        self._queue = queuelib.Queue(maxsize=32)
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def open(self):
        with self._lock:
            if self._thread is not None:
                return
            cmd = ['aplay', '-D', self.device, '-f', 'S16_LE',
                   '-r', str(self.rate), '-c', str(self.channels), '-t', 'raw', '-q']
            self._stop.clear()
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                          stderr=subprocess.PIPE,
                                          start_new_session=True)
            self._thread = threading.Thread(target=self._drain,
                                            args=(self._proc,), daemon=True)
            self._thread.start()
            self.error = None

    def write(self, pcm):
        try:
            self._queue.put_nowait(pcm)
        except queuelib.Full:
            pass        # audio en vivo: lo que llegó tarde ya no sirve

    def _drain(self, proc):
        stdin = proc.stdin
        while not self._stop.is_set():
            try:
                pcm = self._queue.get(timeout=0.2)
            except queuelib.Empty:
                continue
            try:
                stdin.write(pcm)
                stdin.flush()
            except (OSError, ValueError) as exc:
                # ValueError: close() ya cerró el pipe abajo nuestro. Es el
                # final normal de un push to talk, no hay nada que reportar.
                if not self._stop.is_set():
                    self.error = exc
                    print('audio out:', exc)
                return

    def close(self):
        with self._lock:
            self._stop.set()
            thread, self._thread = self._thread, None
            proc, self._proc = self._proc, None
        if thread:
            thread.join(timeout=1)
        if proc:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                pass
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queuelib.Empty:
                break


if __name__ == '__main__':
    # Medidor de nivel: si las barras se mueven al hablar, el micrófono anda.
    import array
    import math
    import time

    mic = Microphone().start()
    print('nivel del micrófono, Ctrl-C para salir')
    try:
        while True:
            time.sleep(0.25)
            pcm = mic.last
            if not pcm:
                print('sin audio', mic.error or '')
                continue
            samples = array.array('h', pcm)
            rms = math.sqrt(sum(s * s for s in samples) / len(samples))
            print('{:5.0f}  {}'.format(rms, '#' * min(int(rms / 200), 50)))
    except KeyboardInterrupt:
        mic.stop()
