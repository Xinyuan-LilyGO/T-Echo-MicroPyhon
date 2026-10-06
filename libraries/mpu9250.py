"""MPU9250/AK8963 with Madgwick AHRS, ported from MPU9250-0.4.6.

See licenses/MPU9250-MIT.txt. Sensor units: g, degrees/s, microtesla, C.
"""

import math
import struct
import time


class MPU9250:
    def __init__(self, i2c, address=0x68, declination=-7.51):
        self.i2c = i2c
        self.address = address
        self.declination = declination
        self.accel = (0.0, 0.0, 0.0)
        self.gyro = (0.0, 0.0, 0.0)
        self.mag = (0.0, 0.0, 0.0)
        self.temperature = 0.0
        self.mag_adjust = (1.0, 1.0, 1.0)
        self.mag_bias = (0.0, 0.0, 0.0)
        self.mag_scale = (1.0, 1.0, 1.0)
        self.gyro_bias = (0.0, 0.0, 0.0)
        self.q = (1.0, 0.0, 0.0, 0.0)
        self.beta = math.sqrt(0.75) * math.radians(40.0)
        self._last_us = None

    def _read(self, register, length=1, address=None):
        return self.i2c.readfrom_mem(
            self.address if address is None else address, register, length)

    def _write(self, register, value, address=None):
        self.i2c.writeto_mem(self.address if address is None else address,
                            register, bytes((value,)))

    def who_am_i(self):
        """Read the chip ID without changing any device registers."""
        return self._read(0x75)[0]

    def probe(self):
        try:
            return self.who_am_i() in (0x71, 0x73)
        except OSError:
            return False

    def begin(self):
        who = self.who_am_i()
        if who not in (0x71, 0x73):
            raise OSError("MPU9250 WHO_AM_I at 0x%02X: got 0x%02X; expected 0x71/0x73" %
                          (self.address, who))
        self._write(0x6B, 0x80)
        time.sleep_ms(100)
        self._write(0x6B, 0x00)
        time.sleep_ms(100)
        self._write(0x6B, 0x01)
        time.sleep_ms(200)
        for register, value in ((0x1A, 3), (0x19, 4), (0x1B, 0x18),
                                (0x1C, 0x18), (0x1D, 3),
                                (0x37, 0x22), (0x38, 1)):
            self._write(register, value)
        time.sleep_ms(100)
        mag_id = self._read(0x00, address=0x0C)[0]
        if mag_id != 0x48:
            raise OSError("AK8963 WHO_AM_I at 0x0C: got 0x%02X; expected 0x48" % mag_id)
        self._write(0x0A, 0, 0x0C)
        time.sleep_ms(10)
        self._write(0x0A, 0x0F, 0x0C)
        time.sleep_ms(10)
        self.mag_adjust = tuple((v - 128) / 256.0 + 1.0
                                for v in self._read(0x10, 3, 0x0C))
        self._write(0x0A, 0, 0x0C)
        time.sleep_ms(10)
        self._write(0x0A, 0x16, 0x0C)
        time.sleep_ms(10)
        self._last_us = time.ticks_us()
        return True

    def update(self):
        if not self._read(0x3A)[0] & 1:
            return False
        raw = struct.unpack(">hhhhhhh", self._read(0x3B, 14))
        self.accel = tuple(v / 2048.0 for v in raw[:3])
        self.temperature = raw[3] / 333.87 + 21.0
        self.gyro = tuple(raw[i + 4] * (2000.0 / 32768.0) - self.gyro_bias[i]
                          for i in range(3))
        if self._read(0x02, address=0x0C)[0] & 1:
            magnetic = self._read(0x03, 7, 0x0C)
            if not magnetic[6] & 0x08:
                values = struct.unpack("<hhh", magnetic[:6])
                self.mag = tuple((values[i] * (4912.0 / 32760.0) *
                                  self.mag_adjust[i] - self.mag_bias[i]) *
                                 self.mag_scale[i] for i in range(3))
        now = time.ticks_us()
        dt = (time.ticks_diff(now, self._last_us) / 1000000.0
              if self._last_us is not None else 0.005)
        self._last_us = now
        # A slow e-paper refresh loses motion samples; do not integrate that
        # entire gap using one instantaneous gyro sample.
        dt = max(0.000001, min(dt, 0.05))
        a, g, m = self.accel, self.gyro, self.mag
        self._madgwick(-a[0], a[1], a[2], math.radians(g[0]),
                       -math.radians(g[1]), -math.radians(g[2]),
                       m[1], -m[0], m[2], dt)
        return True

    def _madgwick(self, ax, ay, az, gx, gy, gz, mx, my, mz, dt):
        q0, q1, q2, q3 = self.q
        rate = [0.5 * (-q1 * gx - q2 * gy - q3 * gz),
                0.5 * (q0 * gx + q2 * gz - q3 * gy),
                0.5 * (q0 * gy - q1 * gz + q3 * gx),
                0.5 * (q0 * gz + q1 * gy - q2 * gx)]
        an = math.sqrt(ax * ax + ay * ay + az * az)
        mn = math.sqrt(mx * mx + my * my + mz * mz)
        if an > 0 and mn > 0:
            ax, ay, az = ax / an, ay / an, az / an
            mx, my, mz = mx / mn, my / mn, mz / mn
            q00, q01, q02, q03 = q0*q0, q0*q1, q0*q2, q0*q3
            q11, q12, q13 = q1*q1, q1*q2, q1*q3
            q22, q23, q33 = q2*q2, q2*q3, q3*q3
            hx = (mx*q00 - 2*q0*my*q3 + 2*q0*mz*q2 + mx*q11 +
                  2*q1*my*q2 + 2*q1*mz*q3 - mx*q22 - mx*q33)
            hy = (2*q0*mx*q3 + my*q00 - 2*q0*mz*q1 + 2*q1*mx*q2 -
                  my*q11 + my*q22 + 2*q2*mz*q3 - my*q33)
            bx = math.sqrt(hx*hx + hy*hy)
            bz = (-2*q0*mx*q2 + 2*q0*my*q1 + mz*q00 + 2*q1*mx*q3 -
                  mz*q11 + 2*q2*my*q3 - mz*q22 + mz*q33)
            fa = 2*q13 - 2*q02 - ax
            fb = 2*q01 + 2*q23 - ay
            fc = 1 - 2*q11 - 2*q22 - az
            fm = bx*(0.5-q22-q33) + bz*(q13-q02) - mx
            fn = bx*(q12-q03) + bz*(q01+q23) - my
            fo = bx*(q02+q13) + bz*(0.5-q11-q22) - mz
            step = [
                -2*q2*fa + 2*q1*fb - bz*q2*fm +
                (-bx*q3+bz*q1)*fn + bx*q2*fo,
                2*q3*fa + 2*q0*fb - 4*q1*fc + bz*q3*fm +
                (bx*q2+bz*q0)*fn + (bx*q3-2*bz*q1)*fo,
                -2*q0*fa + 2*q3*fb - 4*q2*fc + (-2*bx*q2-bz*q0)*fm +
                (bx*q1+bz*q3)*fn + (bx*q0-2*bz*q2)*fo,
                2*q1*fa + 2*q2*fb + (-2*bx*q3+bz*q1)*fm +
                (-bx*q0+bz*q2)*fn + bx*q1*fo]
            norm = math.sqrt(sum(v*v for v in step))
            if norm > 0:
                for i in range(4):
                    rate[i] -= self.beta * step[i] / norm
        updated = tuple(self.q[i] + rate[i] * dt for i in range(4))
        norm = math.sqrt(sum(v*v for v in updated))
        self.q = tuple(v / norm for v in updated)

    def orientation(self):
        """Return yaw, pitch, roll in degrees (same order as the display)."""
        w, x, y, z = self.q
        roll = math.degrees(math.atan2(2*(w*x+y*z), w*w-x*x-y*y+z*z))
        pitch = -math.degrees(math.asin(max(-1.0, min(1.0, 2*(x*z-w*y)))))
        yaw = math.degrees(math.atan2(2*(x*y+w*z), w*w+x*x-y*y-z*z))
        yaw = (yaw + self.declination + 180.0) % 360.0 - 180.0
        return yaw, pitch, roll

    def sleep(self, enabled=True):
        value = self._read(0x6B)[0]
        self._write(0x6B, value | 0x40 if enabled else value & ~0x40)
