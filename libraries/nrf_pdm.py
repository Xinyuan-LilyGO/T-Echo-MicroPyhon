"""nRF52840 PDM EasyDMA capture for the T-Echo microphone shield.

Pin numbers and left-channel selection match PDMSerialPlotter.ino.
Uses nominal 16 kHz mono, signed 16-bit little-endian PCM, gain register 30.
Each read captures a fresh block after discarding startup samples. Serial
printing can therefore take arbitrarily long without overwriting that block.
"""

import gc
import time
import uctypes
from machine import Pin, mem32


class NrfPDM:
    _BASE = 0x4001D000
    _START = _BASE
    _STOP = _BASE + 4
    _STARTED = _BASE + 0x100
    _STOPPED = _BASE + 0x104
    _END = _BASE + 0x108
    _INTENCLR = _BASE + 0x308
    _ENABLE = _BASE + 0x500
    _CLOCK = _BASE + 0x504
    _MODE = _BASE + 0x508
    _GAINL = _BASE + 0x518
    _GAINR = _BASE + 0x51C
    _RATIO = _BASE + 0x520
    _PSEL_CLK = _BASE + 0x540
    _PSEL_DIN = _BASE + 0x544
    _PTR = _BASE + 0x560
    _MAXCNT = _BASE + 0x564

    def __init__(self, data=6, clock=8, select=15, gain=30, sample_rate=16000):
        if sample_rate != 16000:
            raise ValueError("PDM supports 16000 Hz mono for this example")
        if not 0 <= gain <= 80:
            raise ValueError("PDM gain register must be in 0..80")
        self.sample_rate = sample_rate
        self.running = False
        self._owned = None
        self._scratch = bytearray(1024)
        self._pin_select = Pin(select, Pin.OUT, value=0)
        self._pin_clock = Pin(clock, Pin.OUT, value=0)
        self._pin_data = Pin(data, Pin.IN)
        mem32[self._ENABLE] = 0
        mem32[self._INTENCLR] = 0xFFFFFFFF
        mem32[self._CLOCK] = 0x0A000000  # 32 MHz / 25 = 1.280 MHz.
        mem32[self._RATIO] = 1  # 1.280 MHz / 80 = 16 kHz.
        mem32[self._MODE] = 1  # Mono, left sampled on falling clock edge.
        mem32[self._GAINL] = gain
        mem32[self._GAINR] = gain
        mem32[self._PSEL_CLK] = clock
        mem32[self._PSEL_DIN] = data

    @staticmethod
    def _clear(event):
        mem32[event] = 0
        _ = mem32[event]

    def _wait(self, event, timeout_ms):
        started = time.ticks_ms()
        while not mem32[event]:
            if time.ticks_diff(time.ticks_ms(), started) >= timeout_ms:
                raise OSError("PDM DMA timeout")
            time.sleep_ms(1)

    def _queue(self, buffer):
        mem32[self._PTR] = uctypes.addressof(buffer)
        mem32[self._MAXCNT] = len(buffer) // 2
        self._clear(self._STARTED)

    def readinto(self, buffer):
        """Capture into a bytearray; return the number of PCM bytes captured.

        The byte count must be divisible by four, between 256 and 65532.
        A warmup block and a trailing scratch block keep DMA away from the
        returned data. Captures have gaps between calls, as in a serial plotter.
        """
        if not isinstance(buffer, bytearray):
            raise TypeError("PDM capture requires a writable bytearray")
        if len(buffer) < 256 or len(buffer) > 65532 or len(buffer) & 3:
            raise ValueError("PDM buffer must contain 256..65532 bytes, aligned to 4")
        if uctypes.addressof(buffer) & 3 or uctypes.addressof(self._scratch) & 3:
            raise ValueError("PDM EasyDMA buffers must be 32-bit aligned")
        self._owned = buffer
        timeout_ms = 1000 + len(buffer) * 1000 // (2 * self.sample_rate)
        self._clear(self._STOPPED)
        self._clear(self._END)
        self._queue(self._scratch)
        mem32[self._ENABLE] = 1
        gc.disable()
        try:
            self.running = True
            mem32[self._START] = 1
            self._wait(self._STARTED, timeout_ms)
            self._queue(buffer)
            self._wait(self._STARTED, timeout_ms)
            self._queue(self._scratch)
            self._wait(self._STARTED, timeout_ms)
        finally:
            try:
                self.stop()
            finally:
                gc.enable()
        return len(buffer)

    def stop(self):
        try:
            if self.running:
                self._clear(self._STOPPED)
                mem32[self._STOP] = 1
                self._wait(self._STOPPED, 100)
        finally:
            mem32[self._ENABLE] = 0
            self.running = False
            self._owned = None

    deinit = stop
