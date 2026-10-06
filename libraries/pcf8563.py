"""PCF8563/HYM8563 RTC and minute alarm used by the T-Echo examples."""

import time


def _bcd(value):
    return (value // 10) * 16 + value % 10


def _decimal(value):
    return (value >> 4) * 10 + (value & 15)


class PCF8563:
    def __init__(self, i2c, address=0x51):
        self.i2c = i2c
        self.address = address
        self.voltage_low = False
        # HYM8563 can NACK its first access after power-up.
        for attempt in range(4):
            try:
                self._read(0, 2)
                break
            except OSError:
                if attempt == 3:
                    raise
                time.sleep_ms(200)

    def _read(self, register, length=1):
        return self.i2c.readfrom_mem(self.address, register, length)

    def _write(self, register, data):
        self.i2c.writeto_mem(self.address, register, data)

    def datetime(self):
        """Return (year, month, day, hour, minute, second), or None if invalid."""
        data = self._read(2, 7)
        self.voltage_low = bool(data[0] & 0x80)
        result = ((1900 if data[5] & 0x80 else 2000) + _decimal(data[6]),
                  _decimal(data[5] & 0x1F), _decimal(data[3] & 0x3F),
                  _decimal(data[2] & 0x3F), _decimal(data[1] & 0x7F),
                  _decimal(data[0] & 0x7F))
        if self.voltage_low or not (1 <= result[1] <= 12 and 1 <= result[2] <= 31
                                   and result[3] < 24 and result[4] < 60
                                   and result[5] < 60):
            return None
        return result

    def set_datetime(self, year, month, day, hour=0, minute=0, second=0):
        if not (1900 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31
                and 0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
            raise ValueError("Invalid RTC date/time")
        offsets = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
        y = year - (1 if month < 3 else 0)
        weekday = (y + y // 4 - y // 100 + y // 400 + offsets[month - 1] + day) % 7
        self._write(2, bytes((_bcd(second), _bcd(minute), _bcd(hour),
                              _bcd(day), weekday,
                              _bcd(month) | (0x80 if year < 2000 else 0),
                              _bcd(year % 100))))
        control = self._read(0)[0]
        self._write(0, bytes((control & ~0x20,)))
        self.voltage_low = False

    def set_alarm(self, minute=None, hour=None, day=None, weekday=None):
        values = (minute, hour, day, weekday)
        bounds = ((0, 59), (0, 23), (1, 31), (0, 6))
        for value, limits in zip(values, bounds):
            if value is not None and not limits[0] <= value <= limits[1]:
                raise ValueError("Invalid RTC alarm field")
        self._write(9, bytes(0x80 if value is None else _bcd(value) for value in values))

    def enable_alarm(self, enable=True):
        value = self._read(1)[0] & ~0x0A
        self._write(1, bytes((value | (2 if enable else 0),)))

    def alarm_active(self):
        return bool(self._read(1)[0] & 8)

    def clear_alarm(self):
        self._write(1, bytes((self._read(1)[0] & ~8,)))

    def self_test_alarm(self, irq_pin, timeout_ms=10000):
        """Original Sleep test: 2021-04-13 12:00:57, minute 01 interrupt."""
        self.enable_alarm(False)
        self.set_datetime(2021, 4, 13, 12, 0, 57)
        self.set_alarm(minute=1)
        self.enable_alarm()
        start = time.ticks_ms()
        try:
            while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
                if self.alarm_active() and irq_pin.value() == 0:
                    stamp = self.datetime()
                    return stamp is not None and stamp[:5] == (2021, 4, 13, 12, 1)
                time.sleep_ms(20)
            return False
        finally:
            self.enable_alarm(False)
            self.clear_alarm()
