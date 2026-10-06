"""Sleep_Display's original Adafruit_SSD1681 drawing and refresh behavior.

Derived from T-Echo/lib/Adafruit_EPD (Adafruit, BSD) and Sleep_Display
(LILYGO, GPL-3.0). Keep the license notices in libraries/licenses.
"""

from machine import Pin
import time
import t_echo_config as board
from ssd1681 import SSD1681
from freesans9 import FONT


def _display_spi():
    # Adafruit_SPIDevice uses 4 MHz. The panel has no MISO connection.
    return board.spi_bus(1, board.SCREEN_SCLK, board.SCREEN_MOSI,
                         board.SCREEN_MOSI, baudrate=4000000)


class SleepDisplayScreen(SSD1681):
    def __init__(self, spi_factory=_display_spi):
        # Adafruit rotation 0 stores (x,y) at RAM (y,199-x), which is this
        # driver's rotation 3. Do not add a second rotation to the text.
        super().__init__(None, board.SCREEN_CS, board.SCREEN_DC,
                         board.SCREEN_RST, board.SCREEN_BUSY, rotation=3)
        self._spi_factory = spi_factory
        self._byte = bytearray(1)
        # Adafruit's second, noninverted color plane stays zero for white
        # and black pixels. It is not a copy of the black image plane.
        self._previous = bytearray(len(self.buffer))

    def _write_bytes(self, data):
        # Adafruit_SSD1681 enables singleByteTxns, including image bytes.
        for value in data:
            self._byte[0] = value
            self.cs.off()
            try:
                self.spi.write(self._byte)
            finally:
                self.cs.on()

    def _command(self, command, data=None):
        self.cs.on()
        self.dc.off()
        self._write_bytes((command,))
        if data is not None:
            self._data(data)

    def _data(self, data):
        self.dc.on()
        self._write_bytes(data)

    def reset(self):
        self.reset_pin.on()
        time.sleep_ms(10)
        self.reset_pin.off()
        time.sleep_ms(10)
        self.reset_pin.on()
        time.sleep_ms(10)

    def begin(self):
        print("set pins")
        self.dc = Pin(board.SCREEN_DC, Pin.OUT)
        self.cs = Pin(board.SCREEN_CS, Pin.OUT, value=1)
        self.reset_pin = Pin(board.SCREEN_RST, Pin.OUT, value=1)
        self.spi = self._spi_factory()
        print("hard reset")
        self.reset()
        print("busy")
        self.busy = Pin(board.SCREEN_BUSY, Pin.IN)
        print("done!")
        self.sleep()

    def _power_up(self):
        self.reset()
        time.sleep_ms(100)
        self.wait_ready()
        self._command(0x12)
        self.wait_ready()
        time.sleep_ms(20)
        self._command(0x11, b"\x03")
        self._command(0x3C, b"\x05")
        self._command(0x18, b"\x80")
        self._set_address()
        self._command(0x01, b"\xC7\x00\x00")
        self._command(0x44, b"\x00\x18")
        self._command(0x45, b"\x00\x00\xC7\x00")
        self._awake = True

    def show(self, mode=SSD1681.FULL_REFRESH, sleep=True):
        if mode != self.FULL_REFRESH:
            raise ValueError("Sleep_Display uses Adafruit full refresh only")
        self._power_up()
        self._set_address()
        self._command(0x24)
        self._data(self._ram_bytes())
        time.sleep_ms(2)
        self._set_address()
        self._command(0x26)
        self._data(self._previous)
        self._command(0x22, b"\xF7")
        self._command(0x20)
        self.wait_ready()
        if sleep:
            self.sleep()

    def sleep(self):
        self._command(0x10, b"\x01")
        time.sleep_ms(100)
        self._awake = False

    def stage(self, text):
        self.fill(1)
        self.gfx_font_text(text, 10, 60, FONT, color=0, size=1, wrap=True)
        self.show(sleep=True)

    def end(self):
        Pin(board.SCREEN_DC, Pin.IN)
        Pin(board.SCREEN_RST, Pin.IN, Pin.PULL_UP)
        Pin(board.SCREEN_CS, Pin.IN, Pin.PULL_UP)
        Pin(board.SCREEN_BUSY, Pin.IN)
        Pin(board.SCREEN_SCLK, Pin.IN)
        Pin(board.SCREEN_MOSI, Pin.IN)
        self.spi.deinit()
        self._awake = False


def make_screen():
    return SleepDisplayScreen()
