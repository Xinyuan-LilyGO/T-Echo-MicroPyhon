"""nRF52840 PWM0 single-pin output for the Factory backlight.

Uses hardware sequence playback and retains the EasyDMA buffer for its entire
lifetime. PWM0 is reserved by this instance; do not use machine.PWM0 alongside.
"""

from array import array
from machine import Pin, mem32
import uctypes
import time


class Backlight:
    _BASE = 0x4001C000

    def __init__(self, pin, value=51, countertop=16000):
        countertop = int(countertop)
        if not 3 <= countertop <= 32767:
            raise ValueError("PWM countertop must be between 3 and 32767")
        self.countertop = countertop
        self.pin = Pin(pin, Pin.OUT, value=0)
        self.sequence = array("H", [0x8000])
        self._write(0x500, 0)
        self._write(0x560, pin)
        for offset in (0x564, 0x568, 0x56C):
            self._write(offset, 0xFFFFFFFF)
        self._write(0x504, 0)
        self._write(0x508, countertop)
        self._write(0x50C, 0)
        self._write(0x510, 0)
        self._write(0x514, 0)
        self._write(0x520, uctypes.addressof(self.sequence))
        self._write(0x524, 1)
        self._write(0x528, 0)
        self._write(0x52C, 0)
        self._write(0x500, 1)
        self.set(value)

    def _write(self, offset, value):
        mem32[self._BASE + offset] = value

    def set(self, value):
        value = max(0, min(255, int(value)))
        self.sequence[0] = 0x8000 | (value * self.countertop // 255)
        self._write(0x008, 1)

    def deinit(self):
        self._write(0x104, 0)
        self._write(0x004, 1)
        start = time.ticks_ms()
        while not mem32[self._BASE + 0x104]:
            if time.ticks_diff(time.ticks_ms(), start) > 50:
                break
        self._write(0x500, 0)
        self._write(0x560, 0xFFFFFFFF)
        self.pin.init(Pin.OUT, value=0)
