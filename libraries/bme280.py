"""BME280/BMP280 compensation from Bosch's documented reference formulas.

Default sampling matches Factory: temperature x2, pressure x16, humidity x1,
filter x16, normal mode. read() returns C, hPa, %RH (None for BMP280).
"""

import struct
import time


def _signed12(value):
    return value - 4096 if value & 0x800 else value


class BME280:
    def __init__(self, i2c, address=None):
        self.i2c = i2c
        if address is None:
            addresses = i2c.scan()
            address = 0x77 if 0x77 in addresses else 0x76
        self.address = address
        self.chip_id = self._read(0xD0, 1)[0]
        if self.chip_id not in (0x60, 0x58, 0x56, 0x57):
            raise OSError("BME280/BMP280 chip ID mismatch: 0x%02x" % self.chip_id)
        self.has_humidity = self.chip_id == 0x60
        self._write(0xE0, 0xB6)
        time.sleep_ms(5)
        deadline = time.ticks_ms()
        while self._read(0xF3, 1)[0] & 1:
            if time.ticks_diff(time.ticks_ms(), deadline) > 100:
                raise OSError("BME280 calibration timeout")
            time.sleep_ms(2)
        self.t_cal = struct.unpack("<Hhh", self._read(0x88, 6))
        self.p_cal = struct.unpack("<Hhhhhhhhh", self._read(0x8E, 18))
        if self.has_humidity:
            data = self._read(0xE1, 7)
            self.h_cal = (self._read(0xA1, 1)[0], struct.unpack("<h", data[:2])[0],
                          data[2], _signed12((data[3] << 4) | (data[4] & 15)),
                          _signed12((data[5] << 4) | (data[4] >> 4)),
                          data[6] - 256 if data[6] > 127 else data[6])
        self._write(0xF5, 0x10)
        self.wake()
        time.sleep_ms(55)

    def _read(self, register, length):
        return self.i2c.readfrom_mem(self.address, register, length)

    def _write(self, register, value):
        self.i2c.writeto_mem(self.address, register, bytes((value,)))

    def wake(self):
        if self.has_humidity:
            self._write(0xF2, 1)
        self._write(0xF4, 0x57)

    def sleep(self):
        self._write(0xF4, 0x54)

    def read(self):
        data = self._read(0xF7, 8 if self.has_humidity else 6)
        ap = ((data[0] << 16) | (data[1] << 8) | data[2]) >> 4
        at = ((data[3] << 16) | (data[4] << 8) | data[5]) >> 4
        if ap == 0x80000 or at == 0x80000:
            raise OSError("BME280 measurement unavailable")
        t1, t2, t3 = self.t_cal
        v1 = (at / 16384.0 - t1 / 1024.0) * t2
        v2 = ((at / 131072.0 - t1 / 8192.0) ** 2) * t3
        fine = v1 + v2
        temperature = fine / 5120.0
        p1, p2, p3, p4, p5, p6, p7, p8, p9 = self.p_cal
        v1 = fine / 2.0 - 64000.0
        v2 = v1 * v1 * p6 / 32768.0 + v1 * p5 * 2.0
        v2 = v2 / 4.0 + p4 * 65536.0
        v1 = (p3 * v1 * v1 / 524288.0 + p2 * v1) / 524288.0
        v1 = (1.0 + v1 / 32768.0) * p1
        if v1 == 0:
            raise OSError("Invalid BME280 pressure calibration")
        pressure = (1048576.0 - ap - v2 / 4096.0) * 6250.0 / v1
        pressure += (p9 * pressure * pressure / 2147483648.0
                     + pressure * p8 / 32768.0 + p7) / 16.0
        humidity = None
        if self.has_humidity:
            ah = (data[6] << 8) | data[7]
            h1, h2, h3, h4, h5, h6 = self.h_cal
            value = fine - 76800.0
            value = (ah - (h4 * 64.0 + h5 / 16384.0 * value)) * (
                h2 / 65536.0 * (1.0 + h6 / 67108864.0 * value *
                               (1.0 + h3 / 67108864.0 * value)))
            humidity = max(0.0, min(100.0, value * (1.0 - h1 * value / 524288.0)))
        return temperature, pressure / 100.0, humidity
