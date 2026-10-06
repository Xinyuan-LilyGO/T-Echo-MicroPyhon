"""BHI260AP I2C RAM firmware loader and Bosch BHY2 FIFO driver.

Protocol ported from SensorLib's Bosch BHY2 v1.6.0 driver. See
licenses/Bosch-BSD-3-Clause.txt and licenses/SensorLib-MIT.txt.
The firmware file is streamed in 32-byte transactions, never loaded into RAM.
"""

import os
import struct
import time


def _le(data):
    value = 0
    for index in range(len(data)):
        value |= data[index] << (8 * index)
    return value


class BHI260AP:
    API_VERSION = 2
    ACCEL_PASSTHROUGH = 1
    GYRO_PASSTHROUGH = 10
    _SYSTEM_SIZES = {0: 1, 244: 23, 245: 2, 246: 3,
                     247: 6, 248: 4, 250: 18, 251: 2, 252: 3,
                     253: 6, 254: 4, 255: 1}

    def __init__(self, i2c, address=0x28, firmware=None, raw=False):
        self.i2c = i2c
        self.address = address
        self.firmware = firmware
        self.raw = raw
        self.latest = {}
        self.updated = set()
        self.sensor_info = {}
        self.available = ()
        self._sizes = dict(self._SYSTEM_SIZES)
        self._sizes.update({1: 7, 10: 7})
        self._pending = {1: bytearray(), 2: bytearray(), 3: bytearray()}
        self._remaining = {1: 0, 2: 0, 3: 0}
        self._timestamps = {1: 0, 2: 0, 3: 0}
        self.initialized = False
        self.last_meta = None
        self._enabled = {}
        self._suspended = None

    def _read(self, register, count=1):
        data = self.i2c.readfrom_mem(self.address, register, count)
        if len(data) != count:
            raise OSError("BHI260AP short I2C read")
        return data

    def _write(self, register, data):
        if isinstance(data, int):
            data = bytes((data,))
        self.i2c.writeto_mem(self.address, register, data)

    def probe(self):
        try:
            return self._read(0x1C)[0] == 0x89
        except OSError:
            return False

    def _wait_boot(self, mask, timeout_ms=5000):
        started = time.ticks_ms()
        while True:
            status = self._read(0x25)[0]
            if status & 0x40:
                raise OSError("BHI260AP firmware verification failed (error 0x%02X)" %
                              self._read(0x2E)[0])
            if status & mask == mask:
                return status
            if time.ticks_diff(time.ticks_ms(), started) >= timeout_ms:
                raise OSError("BHI260AP boot timeout (status 0x%02X)" % status)
            time.sleep_ms(10)

    def _command(self, command, payload=b""):
        length = (len(payload) + 3) & ~3
        packet = struct.pack("<HH", command, length) + payload
        packet += bytes(length - len(payload))
        for offset in range(0, len(packet), 32):
            self._write(0x00, packet[offset:offset + 32])

    def _firmware_path(self):
        if self.firmware:
            return self.firmware
        filename = "bosch_app30_shuttle_bhi260.fw"
        folder = globals().get("__file__", "").replace("\\", "/")
        candidates = []
        if "/" in folder:
            candidates.append(folder.rsplit("/", 1)[0] + "/" + filename)
        candidates += ["/flash/libraries/" + filename,
                       "/libraries/" + filename, "/lib/" + filename,
                       "libraries/" + filename, filename]
        for candidate in candidates:
            try:
                os.stat(candidate)
                return candidate
            except OSError:
                pass
        raise OSError("Upload libraries/" + filename + " to the board")

    def upload_firmware(self, path):
        length = os.stat(path)[6]
        if not length or length & 3 or length // 4 > 65535:
            raise ValueError("invalid Bosch RAM firmware size")
        with open(path, "rb") as source:
            first = source.read(28)
            if first[:2] != b"\x2b\x66":
                raise ValueError("invalid Bosch RAM firmware magic")
            # RAM-upload length is in words; every other command uses bytes.
            self._write(0x00, struct.pack("<HH", 2, length // 4) + first)
            sent = len(first)
            while sent < length:
                block = source.read(min(32, length - sent))
                if not block:
                    raise OSError("truncated Bosch firmware file")
                self._write(0x00, block)
                sent += len(block)
        self._wait_boot(0x30)

    def begin(self, cache_sensor_info=True):
        """Boot and validate all sensors; optionally retain only FIFO sizes."""
        if not self.probe():
            raise OSError("BHI260AP PRODUCT_ID mismatch at 0x%02X" % self.address)
        path = self._firmware_path()
        self._write(0x14, 1)
        time.sleep_ms(10)
        self._wait_boot(0x10, 500)
        self._write(0x07, 0x08)  # Enable FIFO/status/fault, disable debug IRQ.
        self._write(0x06, 0x80)  # Asynchronous status channel like SensorLib.
        self.latest.clear()
        self.updated.clear()
        self._enabled.clear()
        self._suspended = None
        self.initialized = False
        for channel in (1, 2, 3):
            self._pending[channel] = bytearray()
            self._remaining[channel] = 0
            self._timestamps[channel] = 0
        self.upload_firmware(path)
        self._command(3)
        self._wait_boot(0x30)
        started = time.ticks_ms()
        while not _le(self._read(0x20, 2)):
            if time.ticks_diff(time.ticks_ms(), started) > 5000:
                raise OSError("BHI260AP kernel did not start")
            time.sleep_ms(10)
        time.sleep_ms(50)
        self.update()
        present = self.get_parameter(0x11F)
        if len(present) != 32:
            raise OSError("BHI260AP invalid virtual sensor list")
        self.available = tuple(i for i in range(1, 243)
                               if present[i // 8] & (1 << (i % 8)))
        self.sensor_info.clear()
        for sensor_id in self.available:
            info = self.get_sensor_info(sensor_id)
            if cache_sensor_info:
                self.sensor_info[sensor_id] = info
        self.initialized = True
        return True

    def get_parameter(self, parameter, timeout_ms=2000):
        previous = self._read(0x06)[0]
        self._write(0x06, previous & ~0x80)
        try:
            self._command(parameter | 0x1000)
            started = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), started) < timeout_ms:
                if not self._read(0x2D)[0] & 0x20:
                    time.sleep_ms(1)
                    continue
                code, count = struct.unpack("<HH", self._read(3, 4))
                if count > 4096:
                    raise OSError("BHI260AP invalid parameter response size")
                result = bytearray()
                while count:
                    size = min(count, 32)
                    result.extend(self._read(3, size))
                    count -= size
                if code == parameter:
                    return bytes(result)
                if code in (8, 9, 15):
                    raise OSError("BHI260AP command rejected: %s" % repr(result))
            raise OSError("BHI260AP parameter 0x%03X timeout" % parameter)
        finally:
            self._write(0x06, previous)

    def get_sensor_info(self, sensor_id):
        data = self.get_parameter(0x300 + sensor_id)
        if len(data) != 28 or data[20] < 1:
            raise OSError("BHI260AP invalid sensor info")
        self._sizes[sensor_id] = data[20]
        return {"id": sensor_id, "type": data[0], "driver_id": data[1],
                "driver_version": data[2], "max_range": _le(data[4:6]),
                "resolution": _le(data[6:8]),
                "max_rate": struct.unpack("<f", data[8:12])[0],
                "min_rate": struct.unpack("<f", data[21:25])[0],
                "event_size": data[20]}

    def info(self):
        return {"product_id": self._read(0x1C)[0],
                "rom_version": _le(self._read(0x1E, 2)),
                "kernel_version": _le(self._read(0x20, 2)),
                "user_version": _le(self._read(0x22, 2)),
                "boot_status": self._read(0x25)[0],
                "error": self._read(0x2E)[0], "sensors": self.available}

    def configure(self, sensor_id, sample_rate=100.0, latency_ms=0):
        if sensor_id not in self.available:
            raise ValueError("BHI260AP sensor %d unavailable in firmware" % sensor_id)
        if sample_rate < 0 or not 0 <= latency_ms <= 0xFFFFFF:
            raise ValueError("invalid sample rate or report latency")
        payload = bytes((sensor_id,)) + struct.pack("<f", float(sample_rate))
        payload += bytes((latency_ms & 255, (latency_ms >> 8) & 255,
                          (latency_ms >> 16) & 255))
        self._command(13, payload)
        if sample_rate:
            self._enabled[sensor_id] = (sample_rate, latency_ms)
        else:
            self._enabled.pop(sensor_id, None)

    def sleep(self, enabled=True):
        """Stop enabled sensors and mark the host suspended; False resumes.

        The Bosch kernel remains loaded in RAM. Board power-off removes it.
        """
        if enabled:
            if self._suspended is None:
                self._suspended = dict(self._enabled)
                for sensor_id in tuple(self._enabled):
                    self.configure(sensor_id, 0.0)
            self._write(0x06, self._read(0x06)[0] | 0x10)
        else:
            self._write(0x06, self._read(0x06)[0] & ~0x10)
            saved = self._suspended or {}
            for sensor_id, settings in saved.items():
                self.configure(sensor_id, settings[0], settings[1])
            self._suspended = None

    @staticmethod
    def timestamp_parts(ticks):
        # Split before scaling: only the sub-second part needs nanoseconds.
        # Avoid creating a large temporary integer for every output sample.
        return ticks // 64000, (ticks % 64000) * 15625

    def _parse(self, channel, data):
        pending = self._pending[channel]
        pending.extend(data)
        offset = 0
        while offset < len(pending):
            sensor_id = pending[offset]
            size = self._sizes.get(sensor_id)
            if size is None:
                raise OSError("BHI260AP unknown FIFO event %d" % sensor_id)
            if size < 1:
                raise OSError("BHI260AP invalid FIFO event size")
            if len(pending) - offset < size:
                break
            if sensor_id in (245, 246, 251, 252):
                self._timestamps[channel] += _le(pending[offset + 1:offset + size])
            elif sensor_id in (247, 253):
                self._timestamps[channel] = _le(pending[offset + 1:offset + size])
            elif sensor_id in (248, 254):
                payload = pending[offset + 1:offset + size]
                self.last_meta = tuple(payload)
                if payload[0] == 16:
                    self.initialized = True
                elif payload[0] == 11:
                    raise OSError("BHI260AP sensor error: %s" % repr(tuple(payload)))
                elif payload[0] == 12:
                    raise OSError("BHI260AP FIFO overflow; reduce sample rate or output load")
                elif payload[0] == 19 and self.initialized:
                    raise OSError("BHI260AP sensor firmware reset during acquisition")
            elif sensor_id in (1, 10):
                if size != 7:
                    raise OSError("BHI260AP invalid XYZ event size")
                # Explicit little-endian sign extension, including -32768.
                # Raw mode keeps the high-rate data path entirely integer.
                x = pending[offset + 1] | (pending[offset + 2] << 8)
                y = pending[offset + 3] | (pending[offset + 4] << 8)
                z = pending[offset + 5] | (pending[offset + 6] << 8)
                x = x - 65536 if x & 32768 else x
                y = y - 65536 if y & 32768 else y
                z = z - 65536 if z & 32768 else z
                if self.raw:
                    values = (x, y, z)
                else:
                    scale = 1.0 / 4096.0 if sensor_id == 1 else 2000.0 / 32768.0
                    values = (x * scale, y * scale, z * scale)
                self.latest[sensor_id] = (self._timestamps[channel], values)
                self.updated.add(sensor_id)
            offset += size
        if offset:
            self._pending[channel] = pending[offset:]

    def _drain(self, channel, max_bytes=None):
        # A FIFO transfer has one length header. If a budget splits the
        # transfer, the next call must continue with payload, not a new header.
        if not self._remaining[channel]:
            self._remaining[channel] = _le(self._read(channel, 2))
        consumed = 0
        while self._remaining[channel] and (max_bytes is None or consumed < max_bytes):
            size = min(self._remaining[channel], 32)
            if max_bytes is not None:
                size = min(size, max_bytes - consumed)
            data = self._read(channel, size)
            self._remaining[channel] -= size
            consumed += size
            self._parse(channel, data)
        return consumed

    def update(self, max_bytes=None):
        """Drain pending FIFO data; return IDs updated in this call.

        latest[id] contains (timestamp in 1/64000 s ticks, (x, y, z)).
        Acceleration is in g; gyroscope values are in degrees/s. With
        raw=True, values are signed 16-bit counts (4096/g, 32768/2000 dps).
        max_bytes limits FIFO payload per call, preserving unfinished transfers.
        """
        if max_bytes is not None and (not isinstance(max_bytes, int) or max_bytes < 1):
            raise ValueError("max_bytes must be a positive integer")
        self.updated.clear()
        status = self._read(0x2D)[0]
        if status & 0x80:
            error = self._read(0x2E)[0]
            if error:
                raise OSError("BHI260AP fault 0x%02X" % error)
        # Service diagnostic events first. A transfer already in progress may
        # no longer have its status bit asserted, but still needs draining.
        for channel, mask in ((3, 0x40), (1, 0x06), (2, 0x18)):
            if self._remaining[channel] or status & mask:
                used = self._drain(channel, max_bytes)
                if max_bytes is not None:
                    max_bytes -= used
                    if max_bytes == 0:
                        break
        return self.updated
