"""GPIO SPI fallback for an unmanaged flash bus, mode 0 and MSB first.

Python GPIO calls make this slower than the requested baudrate. This class
does not claim a hardware SPI peripheral; do not use it on a mounted flash.
"""

from machine import Pin
import time


class SoftSPI:
    def __init__(self, baudrate=1000000, polarity=0, phase=0,
                 sck=None, mosi=None, miso=None):
        if polarity != 0 or phase != 0:
            raise ValueError("GPIO SoftSPI supports only mode 0")
        if baudrate <= 0:
            raise ValueError("baudrate must be positive")
        if sck is None or mosi is None or miso is None:
            raise ValueError("sck, mosi and miso pins are required")
        self.sck = sck
        self.mosi = mosi
        self.miso = miso
        self._active = False
        self._delay_us = max(1, (500000 + baudrate - 1) // baudrate)
        self.sck.init(Pin.OUT, value=0)
        self.mosi.init(Pin.OUT, value=0)
        self.miso.init(Pin.IN)
        self._active = True

    def _check_active(self):
        if not self._active:
            raise OSError("SoftSPI is deinitialized")

    def _byte(self, value):
        received = 0
        for shift in range(7, -1, -1):
            self.mosi.value((value >> shift) & 1)
            time.sleep_us(self._delay_us)
            self.sck.on()
            received = (received << 1) | (1 if self.miso.value() else 0)
            time.sleep_us(self._delay_us)
            self.sck.off()
        return received

    def _transfer(self, output, input_buffer, count, fill=0):
        self._check_active()
        try:
            for index in range(count):
                value = fill if output is None else output[index]
                received = self._byte(value)
                if input_buffer is not None:
                    input_buffer[index] = received
        finally:
            self.sck.off()

    def write(self, buffer):
        data = memoryview(buffer)
        self._transfer(data, None, len(data))

    def read(self, nbytes, write=0x00):
        result = bytearray(nbytes)
        self.readinto(result, write)
        return bytes(result)

    def readinto(self, buffer, write=0x00):
        data = memoryview(buffer)
        # Reject a read-only destination before clocking any flash command.
        if len(data):
            data[0] = data[0]
        self._transfer(None, data, len(data), write & 0xFF)

    def write_readinto(self, write_buffer, read_buffer):
        output = memoryview(write_buffer)
        result = memoryview(read_buffer)
        if len(output) != len(result):
            raise ValueError("SPI buffers must have equal length")
        if len(result):
            result[0] = result[0]
        self._transfer(output, result, len(output))

    def deinit(self):
        if not self._active:
            return
        self._active = False
        try:
            self.sck.off()
        finally:
            try:
                self.mosi.init(Pin.IN)
            finally:
                try:
                    self.miso.init(Pin.IN)
                finally:
                    self.sck.init(Pin.IN)
