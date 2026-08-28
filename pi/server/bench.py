"""Mide dónde se va la latencia del video y de los controles.

Corré lo mismo desde la Pi y desde la máquina que mira el video: la diferencia
entre las dos corridas es lo que aporta la red.

    python bench.py                        # en la Pi, contra localhost
    python bench.py http://poopinPi:8000   # desde la Mac
"""
import http.client
import json
import statistics
import sys
import time
from urllib.parse import urlparse

FRAMES = 60
CONTROL_HITS = 10
BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:8000'


def connect():
    u = urlparse(BASE)
    return http.client.HTTPConnection(u.hostname, u.port or 80, timeout=15)


def get_json(path):
    c = connect()
    c.request('GET', path)
    body = c.getresponse().read()
    c.close()
    return json.loads(body)


def read_stream(n):
    """Devuelve (llegadas, edades, tamaños) leyendo n partes del multipart."""
    c = connect()
    c.request('GET', '/api/stream')
    r = c.getresponse()
    if r.status != 200:
        raise SystemExit('stream: HTTP {}'.format(r.status))
    fp, arrivals, ages, sizes = r.fp, [], [], []
    while len(arrivals) < n:
        line = fp.readline()
        if not line:
            break
        if not line.startswith(b'--'):
            continue
        length = age = None
        while True:
            h = fp.readline()
            if h in (b'\r\n', b'\n', b''):
                break
            name, _, val = h.decode('latin1').partition(':')
            if name.lower() == 'content-length':
                length = int(val)
            elif name.lower() == 'x-frame-age':
                age = float(val)
        if not length:
            continue
        body = b''
        while len(body) < length:
            chunk = fp.read(length - len(body))
            if not chunk:
                break
            body += chunk
        arrivals.append(time.monotonic())
        sizes.append(len(body))
        if age is not None:
            ages.append(age)
    c.close()
    return arrivals, ages, sizes


def time_controls(name, values):
    c = connect()
    out = []
    for v in values:
        payload = json.dumps({'value': v})
        t0 = time.monotonic()
        c.request('POST', '/api/controls/' + name, payload,
                  {'Content-Type': 'application/json'})
        r = c.getresponse()
        r.read()
        out.append((time.monotonic() - t0) * 1000)
    c.close()
    return out


def report(label, xs, unit='ms'):
    xs = sorted(xs)
    if not xs:
        print('  {:<22} sin datos'.format(label))
        return
    p95 = xs[min(len(xs) - 1, int(len(xs) * 0.95))]
    print('  {:<22} media {:7.1f}  mediana {:7.1f}  p95 {:7.1f}  max {:7.1f} {}'
          .format(label, statistics.mean(xs), statistics.median(xs), p95, xs[-1], unit))


print('midiendo contra', BASE)

before = get_json('/api/stats')
arrivals, ages, sizes = read_stream(FRAMES)
after = get_json('/api/stats')

if len(arrivals) < 2:
    raise SystemExit('no llegaron frames suficientes')

span = arrivals[-1] - arrivals[0]
gaps = [(b - a) * 1000 for a, b in zip(arrivals, arrivals[1:])]
elapsed = after['clock'] - before['clock']

print('\n== captura (en la Pi) ==')
print('  del device      {:.1f} fps'.format((after['captured'] - before['captured']) / elapsed))
print('  publicados      {:.1f} fps'.format((after['published'] - before['published']) / elapsed))
print('  descartados     {} por el recorte de fps'.format(after['dropped'] - before['dropped']))
print('  suscriptores    {}'.format(after['subscribers']))
if after['error']:
    print('  error           {}'.format(after['error']))

print('\n== entrega ==')
print('  recibidos       {} frames en {:.1f}s -> {:.1f} fps'.format(len(arrivals), span, (len(arrivals) - 1) / span))
print('  ancho de banda  {:.1f} Mbit/s ({:.0f} KB por frame)'
      .format(sum(sizes) * 8 / span / 1e6, statistics.mean(sizes) / 1024))
report('hueco entre frames', gaps)
report('edad al salir', ages)

print('\n== controles ==')
for name, values in (('zoom', [100, 150, 200, 150]), ('focus', [400, 500, 600, 500])):
    try:
        report('POST ' + name, time_controls(name, (values * 4)[:CONTROL_HITS]))
    except Exception as exc:
        print('  {:<22} {}'.format('POST ' + name, exc))

print("""
Cómo leerlo
  'edad al salir' alta         -> el atraso es nuestro (captura o cola)
  'edad al salir' baja pero
    el video se ve atrasado    -> se encola río abajo: socket, wifi o browser
  hueco p95 >> media           -> jitter, casi siempre wifi
  ancho de banda ~= el techo
    real del enlace            -> bufferbloat: bajá fps o resolución
  POST rápido pero el cambio
    tarda en verse             -> el retardo es del video, no del control
""")
