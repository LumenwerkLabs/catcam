"""Captura del micrófono de la C200.

Mismo patrón que camera.py: un solo hilo lee el device y los clientes se
suscriben. El trabajo pesado lo hace arecord, así no sumamos dependencias
de audio al proyecto.
"""

import asyncio
import subprocess
import threading

# Por nombre y no plughw:3,0: el número de card se mueve entre reboots, igual
# que pasaba con /dev/video0.
DEVICE = 'plughw:CARD=C200,DEV=0'
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
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
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
