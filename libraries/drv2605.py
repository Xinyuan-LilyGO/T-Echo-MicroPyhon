"""DRV2605 haptic driver; register setup from SensorLib (MIT)."""

from machine import Pin
import time


class DRV2605:
    def __init__(self, i2c, address=0x5A, enable=8):
        self.i2c = i2c
        self.address = address
        self.enable = Pin(enable, Pin.OUT, value=1)
        time.sleep_ms(2)
        chip_id = self._read(0) >> 5
        if chip_id not in (3, 4, 6, 7):
            raise OSError("DRV2605 chip ID mismatch: %d" % chip_id)
        for register, value in ((1, 0), (2, 0), (3, 1), (4, 1), (5, 0),
                                (0x0D, 0), (0x0E, 0), (0x0F, 0), (0x10, 0),
                                (0x13, 0x64)):
            self._write(register, value)
        self._write(0x1A, self._read(0x1A) & 0x7F)
        self._write(0x1D, self._read(0x1D) | 0x20)

    def _read(self, register):
        return self.i2c.readfrom_mem(self.address, register, 1)[0]

    def _write(self, register, value):
        self.i2c.writeto_mem(self.address, register, bytes((value,)))

    def play(self, effect=75):
        if not 1 <= effect <= 123:
            raise ValueError("Effect must be in 1..123")
        self._write(4, effect)
        self._write(5, 0)
        self._write(0x0C, 1)

    def stop(self):
        self._write(0x0C, 0)

    def sleep(self):
        self.stop()
        self._write(1, 0x40)
        self.enable.off()
