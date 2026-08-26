from servo import Servo
import sys, select, time

PAN_PIN = 15
PAN_MIN_US = 550
PAN_MAX_US = 2450
IDLE_MS = 400
HOME_ANGLE = 90

pan = Servo(PAN_PIN, min_us=PAN_MIN_US, max_us=PAN_MAX_US)

poller = select.poll()
poller.register(sys.stdin, select.POLLIN)

buf = ''
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


while True:
    while poller.poll(0):
        buf += sys.stdin.read(1)
        if len(buf) > 128:
            buf = ''

    pending = None
    bad = 0
    while '\n' in buf:
        line, buf = buf.split('\n', 1)
        cmd = parse(line)
        if cmd is None:
            bad += 1
        else:
            pending = cmd

    if pending is not None:
        target = pending
        if target != pan.angle:
            target = pan.write(target)
        print('OK', target)
    elif bad:
        print('ERR')

    if pan.attached and pan.idle_for() > IDLE_MS:
        pan.detach()

    time.sleep_ms(10)