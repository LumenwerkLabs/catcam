from servo import Servo
import select
import socket
import time

import secrets
from wifi import connect

PAN_PIN = 15
PAN_MIN_US = 550
PAN_MAX_US = 2450
IDLE_MS = 400
HOME_ANGLE = 90
PORT = 8888
WIFI_CHECK_MS = 5000

pan = Servo(PAN_PIN, min_us=PAN_MIN_US, max_us=PAN_MAX_US)
target = HOME_ANGLE
pan.write(target)


def parse(line):
    parts = line.strip().upper().split()
    if not parts:
        return None
    if parts[0] == 'HOME':
        return HOME_ANGLE
    if parts[0] == 'PAN' and len(parts) == 2:
        try:
            return int(parts[1])
        except ValueError:
            return None
    return None


def wifi():
    while True:
        try:
            return connect(secrets.SSID, secrets.PASSWORD,
                           getattr(secrets, 'IP', None))
        except Exception as exc:
            print('wifi:', exc)
            time.sleep(2)


wlan = wifi()
print('escuchando en {}:{}'.format(wlan.ifconfig()[0], PORT))

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(('0.0.0.0', PORT))
srv.listen(1)
srv.setblocking(False)

poller = select.poll()
poller.register(srv, select.POLLIN)

conn = None
buf = b''
checked = time.ticks_ms()

while True:
    # Todo no bloqueante: el loop tiene que seguir girando para cortar el PWM
    # a los IDLE_MS, aunque no llegue nada por la red.
    for sock, event in poller.poll(0):
        if sock is srv:
            try:
                new, addr = srv.accept()
            except OSError:
                continue
            if conn:                    # una sola sesión: el Pi es el único cliente
                poller.unregister(conn)
                conn.close()
            new.setblocking(False)
            conn, buf = new, b''
            poller.register(conn, select.POLLIN)
            print('conectado', addr)
        else:
            try:
                data = conn.recv(256)
            except OSError:
                data = b''
            if data:
                buf += data
                if len(buf) > 128:
                    buf = b''
            else:
                poller.unregister(conn)
                conn.close()
                conn, buf = None, b''
                print('desconectado')

    pending = None
    bad = 0
    while b'\n' in buf:
        line, buf = buf.split(b'\n', 1)
        cmd = parse(line.decode())
        if cmd is None:
            bad += 1
        else:
            pending = cmd

    if conn:
        try:
            if pending is not None:
                target = pending
                if target != pan.angle:
                    target = pan.write(target)
                conn.send('OK {}\n'.format(target).encode())
            elif bad:
                conn.send(b'ERR\n')
        except OSError:
            pass                        # se cortó; el poll lo limpia en la vuelta

    if pan.attached and pan.idle_for() > IDLE_MS:
        pan.detach()

    if time.ticks_diff(time.ticks_ms(), checked) > WIFI_CHECK_MS:
        checked = time.ticks_ms()
        if not wlan.isconnected():
            print('wifi caído, reconectando')
            wlan = wifi()

    time.sleep_ms(10)
