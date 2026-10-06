"""Streaming NMEA-0183 parser used by the L76K examples."""

import math
import time


def _number(value, default=None):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _coordinate(value, hemisphere):
    number = _number(value)
    if number is None:
        return None
    degrees = int(number // 100)
    result = degrees + (number - degrees * 100) / 60.0
    return -result if hemisphere in ("S", "W") else result


class NMEA:
    def __init__(self):
        self.buffer = bytearray()
        self.chars = 0
        self.sentences = 0
        self.failed_checksum = 0
        self.latitude = None
        self.longitude = None
        self.altitude_m = None
        self.speed_kmph = None
        self.course_deg = None
        self.satellites = None
        self.satellites_visible = 0
        self.max_cn0 = 0
        self.hdop = None
        self.vdop = None
        self.pdop = None
        self.date = None
        self.utc = None
        self.utc_centisecond = 0
        self.fix_ms = None

    def feed(self, data):
        updates = 0
        for value in data:
            self.chars += 1
            if value == 10:
                line = bytes(self.buffer).strip()
                self.buffer = bytearray()
                try:
                    if line and self._parse(line):
                        updates += 1
                except (ValueError, IndexError, OverflowError):
                    # A complete checksum-valid sentence can still contain
                    # malformed numeric fields, particularly during startup.
                    pass
            elif value == 36:
                self.buffer = bytearray(b"$")
            elif value != 13 and len(self.buffer) < 160:
                self.buffer.append(value)
        return updates

    def _parse(self, line):
        if not line.startswith(b"$"):
            return False
        star = line.find(b"*")
        if star >= 0:
            check = 0
            for value in line[1:star]:
                check ^= value
            try:
                expected = int(line[star + 1:star + 3], 16)
            except ValueError:
                return False
            if check != expected:
                self.failed_checksum += 1
                return False
            line = line[:star]
        try:
            fields = line.decode().split(",")
        except UnicodeError:
            return False
        kind = fields[0][-3:]
        if kind == "GGA" and len(fields) >= 10:
            self.utc = self._time(fields[1]) or self.utc
            self.utc_centisecond = self._centisecond(fields[1])
            if fields[6] not in ("", "0"):
                self.latitude = _coordinate(fields[2], fields[3])
                self.longitude = _coordinate(fields[4], fields[5])
                self.fix_ms = time.ticks_ms()
                self.sentences += 1
            self.satellites = int(fields[7] or 0)
            self.hdop = _number(fields[8])
            self.altitude_m = _number(fields[9])
            return True
        elif kind == "RMC" and len(fields) >= 10:
            self.utc = self._time(fields[1]) or self.utc
            self.utc_centisecond = self._centisecond(fields[1])
            if fields[2] == "A":
                self.latitude = _coordinate(fields[3], fields[4])
                self.longitude = _coordinate(fields[5], fields[6])
                speed = _number(fields[7])
                self.speed_kmph = None if speed is None else speed * 1.852
                self.course_deg = _number(fields[8])
                self.fix_ms = time.ticks_ms()
                self.sentences += 1
            if len(fields[9]) == 6:
                self.date = (2000 + int(fields[9][4:6]), int(fields[9][2:4]),
                             int(fields[9][0:2]))
            return True
        elif kind == "GSA" and len(fields) >= 18:
            self.pdop = _number(fields[15], self.pdop)
            self.hdop = _number(fields[16], self.hdop)
            self.vdop = _number(fields[17], self.vdop)
            return True
        elif kind == "GSV" and len(fields) >= 4:
            try:
                self.satellites_visible = int(fields[3] or 0)
            except ValueError:
                pass
            for index in range(7, len(fields), 4):
                if fields[index]:
                    try:
                        self.max_cn0 = max(self.max_cn0, int(fields[index]))
                    except ValueError:
                        pass
            return True
        elif kind == "VTG" and len(fields) >= 8:
            self.course_deg = _number(fields[1], self.course_deg)
            self.speed_kmph = _number(fields[7], self.speed_kmph)
            return True
        return False

    @staticmethod
    def _time(value):
        if len(value) < 6:
            return None
        return (int(value[0:2]), int(value[2:4]), int(value[4:6]))

    @staticmethod
    def _centisecond(value):
        dot = value.find(".")
        if dot < 0:
            return 0
        fraction = (value[dot + 1:] + "00")[:2]
        try:
            return int(fraction)
        except ValueError:
            return 0

    @property
    def valid(self):
        return self.latitude is not None and self.longitude is not None

    def age(self):
        return None if self.fix_ms is None else time.ticks_diff(time.ticks_ms(), self.fix_ms)


def distance_between(lat1, lon1, lat2, lon2):
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def course_to(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def cardinal(degrees):
    names = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return names[int((degrees + 11.25) // 22.5) & 15]
