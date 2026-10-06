"""ICM-20948 driver based on the validated T-Echo-Lite MicroPython driver."""

import math
import struct
import time


class ICM20948:
    def __init__(self, i2c, address=0x68):
        self.i2c = i2c
        self.address = address
        self.bank = -1
        self._identified = False
        self.gyro_offset = [0.0, 0.0, 0.0]
        self.accel_offset = [0.0, 0.0, 0.0]
        self.mag = (0.0, 0.0, 0.0)

    def _select(self, bank):
        if bank != self.bank:
            self.i2c.writeto_mem(self.address, 0x7F, bytes((bank << 4,)))
            self.bank = bank

    def _write(self, bank, register, value):
        self._select(bank)
        self.i2c.writeto_mem(self.address, register, bytes((value,)))

    def _read(self, bank, register, length=1):
        self._select(bank)
        return self.i2c.readfrom_mem(self.address, register, length)

    def who_am_i(self):
        """Do not write bank-select to an unknown device sharing this address.

        A new instance expects the power-on bank (0). Once this instance has
        verified the ID, it can safely restore bank 0 for later probes.
        """
        if self._identified:
            self._select(0)
        who = self.i2c.readfrom_mem(self.address, 0x00, 1)[0]
        if who == 0xEA:
            self._identified = True
            self.bank = 0
        return who

    def probe(self):
        try:
            return self.who_am_i() == 0xEA
        except OSError:
            return False

    def begin(self, calibrate=False):
        who = self.who_am_i()
        if who != 0xEA:
            raise OSError("ICM20948 WHO_AM_I at 0x%02X: got 0x%02X; expected 0xEA" %
                          (self.address, who))
        self._write(0, 0x06, 0x80)
        self.bank = -1
        time.sleep_ms(100)
        self._write(0, 0x06, 0x01)
        self._write(0, 0x07, 0x00)
        self._write(2, 0x01, 0x31)  # +/-250 dps, DLPF 6.
        self._write(2, 0x00, 0x00)
        self._write(2, 0x14, 0x31)  # +/-2 g, DLPF 6.
        self._write(2, 0x10, 0x00)
        self._write(2, 0x11, 0x00)
        self._write(2, 0x53, 0x06)  # Temperature DLPF 6.
        self._init_magnetometer()
        if calibrate:
            self.calibrate()
        return True

    def _init_magnetometer(self):
        self._write(0, 0x03, 0x20)
        self._write(3, 0x01, 0x07)
        self._mag_write(0x32, 0x01)
        time.sleep_ms(10)
        # Use the internal I2C master, so AK09916 need not appear in scan().
        self._write(3, 0x03, 0x8C)
        self._write(3, 0x04, 0x01)
        self._write(3, 0x05, 0x81)
        time.sleep_ms(20)
        if self._read(0, 0x3B)[0] != 0x09:
            raise OSError("AK09916 magnetometer WHO_AM_I mismatch")
        self._mag_write(0x31, 0x04)  # Continuous 20 Hz.
        self._write(3, 0x03, 0x8C)
        self._write(3, 0x04, 0x10)
        self._write(3, 0x05, 0x89)
        time.sleep_ms(60)

    def _mag_write(self, register, value):
        self._write(3, 0x03, 0x0C)
        self._write(3, 0x04, register)
        self._write(3, 0x06, value)
        self._write(3, 0x05, 0x81)
        time.sleep_ms(10)

    def calibrate(self, samples=100):
        if samples < 1:
            raise ValueError("calibration needs at least one sample")
        sums_g = [0.0, 0.0, 0.0]
        sums_a = [0.0, 0.0, 0.0]
        for _ in range(samples):
            accel, gyro, _, _ = self.read(raw_offsets=False)
            for index in range(3):
                sums_a[index] += accel[index]
                sums_g[index] += gyro[index]
            time.sleep_ms(5)
        self.gyro_offset = [value / samples for value in sums_g]
        self.accel_offset = [sums_a[0] / samples, sums_a[1] / samples,
                             sums_a[2] / samples - 1.0]

    def read(self, raw_offsets=True):
        """Return acceleration (g), gyro (dps), temperature (C), mag (uT)."""
        data = self._read(0, 0x2D, 23)
        values = struct.unpack(">hhhhhhh", data[:14])
        accel = [values[i] / 16384.0 for i in range(3)]
        gyro = [values[i + 3] / 131.0 for i in range(3)]
        temperature = values[6] / 333.87 + 21.0
        ext = data[14:23]
        if ext[0] & 1 and not ext[8] & 0x08:
            self.mag = tuple(v * 0.15 for v in struct.unpack("<hhh", ext[1:7]))
        if raw_offsets:
            accel = [accel[i] - self.accel_offset[i] for i in range(3)]
            gyro = [gyro[i] - self.gyro_offset[i] for i in range(3)]
        return tuple(accel), tuple(gyro), temperature, self.mag

    def orientation(self):
        accel, gyro, temperature, mag = self.read()
        ax, ay, az = accel
        pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
        roll = math.degrees(math.atan2(ay, az))
        yaw = math.degrees(math.atan2(mag[1], mag[0])) % 360
        return pitch, roll, yaw, accel, gyro, mag, temperature

    def sleep(self, enabled=True):
        value = self._read(0, 0x06)[0]
        self._write(0, 0x06, value | 0x40 if enabled else value & ~0x40)
