import glob
import threading
import time

import serial

BAUD = 115200
PATTERNS = ['/dev/ttyACM*', '/dev/cu.usbmodem*']


def find_port():
    for pattern in PATTERNS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    raise RuntimeError('no encuentro el Pico')


class PicoLink:
    def __init__(self, port=None):
        self.port = port or find_port()
        self._serial = serial.Serial(self.port, BAUD, timeout=0.2)
        self._lock = threading.Lock()
        time.sleep(0.3)
        self._serial.reset_input_buffer()

    def send(self, command):
        with self._lock:
            self._serial.write((command + '\n').encode())
            self._serial.flush()
            return self._serial.readline().decode().strip()

    def pan(self, angle):
        return self.send('PAN {}'.format(int(angle)))

    def home(self):
        return self.send('HOME')

    def close(self):
        self._serial.close()


if __name__ == '__main__':
    link = PicoLink()
    print('conectado a', link.port)
    for a in (90, 120, 150, 120, 90):
        print(link.pan(a))
        time.sleep(0.5)
    link.close()