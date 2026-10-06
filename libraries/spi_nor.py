"""SPI NOR driver; capacity read from JEDEC ID, including T-Echo GD25LQ128."""

from machine import Pin
import time


class SpiNor:
    SIZE = 16 * 1024 * 1024
    PAGE_SIZE = 256
    SECTOR_SIZE = 4096

    def __init__(self, spi, cs):
        self.spi = spi
        self.cs = Pin(cs, Pin.OUT, value=1) if isinstance(cs, int) else cs

    def _xfer(self, command, read=0, data=None):
        self.cs.off()
        try:
            self.spi.write(command)
            if data is not None:
                self.spi.write(data)
                return None
            if read:
                return self.spi.read(read, 0x00)
            return None
        finally:
            self.cs.on()

    def wake(self):
        self._xfer(b"\xAB")
        time.sleep_ms(1)

    def sleep(self):
        self.wait_ready()
        self._xfer(b"\xB9")

    def jedec_id(self):
        return int.from_bytes(self._xfer(b"\x9F", 3), "big")

    def size(self):
        jedec = self.jedec_id()
        exponent = jedec & 0xFF
        if jedec in (0, 0xFFFFFF) or not 16 <= exponent <= 24:
            raise OSError("SPI NOR missing or unsupported JEDEC capacity: %06x" % jedec)
        return 1 << exponent

    def status(self):
        return self._xfer(b"\x05", 1)[0]

    def wait_ready(self, timeout_ms=180000):
        start = time.ticks_ms()
        while self.status() & 1:
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                raise OSError("SPI flash busy timeout")
            time.sleep_ms(1)

    def write_enable(self):
        self._xfer(b"\x06")

    def read(self, address, length):
        command = bytes((0x03, (address >> 16) & 0xFF,
                         (address >> 8) & 0xFF, address & 0xFF))
        return self._xfer(command, length)

    def page_program(self, address, data):
        if len(data) > self.PAGE_SIZE - (address & 0xFF):
            raise ValueError("write crosses a flash page")
        self.wait_ready()
        self.write_enable()
        command = bytes((0x02, (address >> 16) & 0xFF,
                         (address >> 8) & 0xFF, address & 0xFF))
        self._xfer(command, data=data)
        self.wait_ready()

    def write(self, address, data):
        offset = 0
        while offset < len(data):
            count = min(self.PAGE_SIZE - (address & 0xFF), len(data) - offset)
            self.page_program(address, memoryview(data)[offset:offset + count])
            address += count
            offset += count

    def erase_sector(self, address):
        self.wait_ready()
        self.write_enable()
        command = bytes((0x20, (address >> 16) & 0xFF,
                         (address >> 8) & 0xFF, address & 0xFF))
        self._xfer(command)
        self.wait_ready()

    def erase_chip(self):
        self.wait_ready()
        self.write_enable()
        self._xfer(b"\xC7")
        self.wait_ready()
