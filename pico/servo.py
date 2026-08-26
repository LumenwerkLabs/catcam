from machine import Pin, PWM
import time

PERIOD_US = 20000


class Servo:
    def __init__(self, pin, min_us=1000, max_us=2000, min_angle=0, max_angle=180):
        self._pwm = PWM(Pin(pin))
        self._pwm.freq(50)
        self.min_us = min_us
        self.max_us = max_us
        self.min_angle = min_angle
        self.max_angle = max_angle
        self.angle = None
        self.attached = False
        self._last_move = time.ticks_ms()

    def _us_to_duty(self, us):
        return int(us * 65535 / PERIOD_US)

    def write_us(self, us):
        """Pulso crudo. Para calibrar."""
        self._pwm.duty_u16(self._us_to_duty(us))
        self.attached = True
        self._last_move = time.ticks_ms()

    def write(self, angle):
        angle = max(self.min_angle, min(self.max_angle, angle))
        span = self.max_angle - self.min_angle
        us = self.min_us + (self.max_us - self.min_us) * (angle - self.min_angle) / span
        self.write_us(us)
        self.angle = angle
        return angle

    def detach(self):
        """Corta el PWM: el servo deja de zumbar."""
        self._pwm.duty_u16(0)
        self.attached = False

    def idle_for(self):
        return time.ticks_diff(time.ticks_ms(), self._last_move)