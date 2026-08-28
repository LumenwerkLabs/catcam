import network
import time

HOSTNAME = 'catcam'


def connect(ssid, password, ip=None, timeout_ms=20000):
    wlan = network.WLAN(network.STA_IF)
    try:
        network.hostname(HOSTNAME)
    except Exception:
        pass
    wlan.active(True)
    wlan.config(pm=0xa11140)
    if ip:
        wlan.ifconfig(ip)
    if not wlan.isconnected():
        wlan.connect(ssid, password)
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while not wlan.isconnected():
            if time.ticks_diff(deadline, time.ticks_ms()) < 0:
                raise RuntimeError('no conecté a ' + ssid)
            time.sleep_ms(200)
    return wlan
