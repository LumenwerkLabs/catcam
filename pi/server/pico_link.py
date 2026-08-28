import socket
import threading
import time

HOST = 'catcam.local'
PORT = 8888
TIMEOUT = 1.0


class PicoLink:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.address = '{}:{}'.format(host, port)
        self._sock = None
        self._file = None
        self._lock = threading.Lock()
        self._connect()

    def _connect(self):
        self._close()
        sock = socket.create_connection((self.host, self.port), timeout=TIMEOUT)
        sock.settimeout(TIMEOUT)
        # Nagle junta los paquetes chicos y le agrega ~40 ms a cada comando.
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._file = sock.makefile('rwb')

    def _close(self):
        for obj in (self._file, self._sock):
            try:
                if obj is not None:
                    obj.close()
            except OSError:
                pass
        self._sock = self._file = None

    def send(self, command):
        with self._lock:
            # Un reintento: el wifi se corta y el Pico rebootea, y el socket
            # muerto recién se nota al escribir.
            for last in (False, True):
                try:
                    if self._sock is None:
                        self._connect()
                    self._file.write((command + '\n').encode())
                    self._file.flush()
                    reply = self._file.readline()
                    if not reply:
                        raise OSError('el Pico cerró la conexión')
                    return reply.decode().strip()
                except OSError:
                    self._close()
                    if last:
                        raise

    def pan(self, angle):
        return self.send('PAN {}'.format(int(angle)))

    def home(self):
        return self.send('HOME')

    def close(self):
        with self._lock:
            self._close()


if __name__ == '__main__':
    link = PicoLink()
    print('conectado a', link.address)
    for a in (90, 120, 150, 120, 90):
        print(link.pan(a))
        time.sleep(0.5)
    link.close()
