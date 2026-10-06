"""Temperature and pressure on the 200x200 e-paper, using existing firmware.

Needs a BME280 or BMP280 on the shared I2C bus (0x76 or 0x77).
Upload the Python dependencies listed in README_CN.md to /flash/libraries.
"""
import sys

_here = globals().get("__file__", "").replace("\\", "/").rsplit("/", 1)[0]
for _path in ("/flash/libraries", _here + "/../../libraries", "/libraries",
              "/lib", "libraries", "../../libraries"):
    if _path not in sys.path:
        sys.path.append(_path)

import gc
import time
from machine import Pin
import t_echo_config as board
from bme280 import BME280
from ssd1681 import SSD1681
from gfx_display import GFXDisplay
from font_freemonobold9 import FONT as LABEL_FONT
from font_freemonobold12 import FONT as TITLE_FONT
from font_freemonobold18 import FONT as NUMBER_FONT

REFRESH_INTERVAL_MS = 5000
FULL_REFRESH_EVERY = 12
ROTATION = 3
BACKLIGHT = False


def _text(display, text, font, left, baseline):
    display.setFont(font)
    bx, _, _, _ = display.getTextBounds(text, 0, 0)
    display.setCursor(left - bx, baseline)
    display.print(text)


def _value(display, value, unit, baseline):
    text = "--.--" if value is None else "%.2f" % value
    display.setFont(NUMBER_FONT)
    _, _, width, _ = display.getTextBounds(text, 0, 0)
    display.setFont(LABEL_FONT)
    _, _, unit_width, _ = display.getTextBounds(unit, 0, 0)
    degree_width = 7 if unit == "C" else 0
    left = (display.width() - width - 6 - degree_width - unit_width) // 2
    _text(display, text, NUMBER_FONT, left, baseline)
    unit_x = left + width + 6
    if unit == "C":
        # The bundled ASCII fonts do not contain a degree symbol.
        y = baseline - 24
        display.fillRect(unit_x + 1, y, 3, 1, 0)
        display.fillRect(unit_x, y + 1, 1, 3, 0)
        display.fillRect(unit_x + 4, y + 1, 1, 3, 0)
        display.fillRect(unit_x + 1, y + 4, 3, 1, 0)
        unit_x += degree_width
    _text(display, unit, LABEL_FONT, unit_x, baseline - 11)


def draw_readings(display, temperature, pressure, status=None):
    """Draw the real values; None means unavailable, never a sample reading."""
    display.setFullWindow()
    display.setTextWrap(False)
    display.setTextSize(1)
    display.setTextColor(0)
    display.fillScreen(1)
    _text(display, "[Temperature]", TITLE_FONT, 10, 29)
    _value(display, temperature, "C", 79)
    _text(display, "[Pressure]", TITLE_FONT, 10, 121)
    _value(display, pressure, "hPa", 171)
    if status:
        display.setFont(LABEL_FONT)
        _, _, width, _ = display.getTextBounds(status, 0, 0)
        _text(display, status, LABEL_FONT, (display.width() - width) // 2, 195)


def find_sensor(i2c):
    addresses = i2c.scan()
    print("I2C devices:", " ".join("0x%02X" % address for address in addresses))
    for address in (0x77, 0x76):
        if address in addresses:
            try:
                sensor = BME280(i2c, address=address)
            except OSError as error:
                print("Sensor at 0x%02X:" % address, error)
                continue
            print("%s found at 0x%02X" % (
                "BME280" if sensor.has_humidity else "BMP280", address))
            return sensor
    raise OSError("BME280/BMP280 not found at 0x76 or 0x77")


def main():
    print("Temperature / Pressure: Python only, no UF2 update")
    board.power_on()
    board.leds_off()
    backlight = Pin(board.ePaper_Backlight, Pin.OUT, value=int(BACKLIGHT))
    spi = board.display_spi()
    panel = sensor = None
    try:
        spi.init(baudrate=4000000, polarity=0, phase=0)
        gc.collect()
        panel = SSD1681(spi, board.ePaper_Cs, board.ePaper_Dc,
                        board.ePaper_Rst, board.ePaper_Busy,
                        rotation=ROTATION, low_memory=True)
        panel.begin()
        display = GFXDisplay(panel)
        try:
            sensor = find_sensor(board.shared_i2c())
        except OSError as error:
            print(error)
            print("Check BME280/BMP280: SDA=P0.26, SCL=P0.27, 3.3V and GND.")
            draw_readings(display, None, None, "Sensor not found")
            panel.show(panel.FULL_REFRESH, sleep=False)
            return

        print("Updating every %d ms; Ctrl-C to stop." % REFRESH_INTERVAL_MS)
        updates = 0
        while True:
            try:
                temperature, pressure, _ = sensor.read()  # pressure is already hPa
                status = None
                print("Temperature: %.2f C  Pressure: %.2f hPa" % (temperature, pressure))
            except OSError as error:
                print("Sensor read error:", error)
                temperature = pressure = None
                status = "Read error"
            draw_readings(display, temperature, pressure, status)
            if updates == 0:
                panel.show(panel.FULL_REFRESH, sleep=False)
            else:
                panel.show_region(0, 0, 200, 200, sleep=False)
            updates = (updates + 1) % FULL_REFRESH_EVERY
            gc.collect()
            time.sleep_ms(REFRESH_INTERVAL_MS)
    except KeyboardInterrupt:
        print("Stopped. The last image stays on the e-paper.")
    finally:
        # Keep shared power and USB on: /flash contains the user's libraries.
        for device in (sensor, panel):
            if device is not None:
                try:
                    device.sleep()
                except OSError as error:
                    print("Sleep:", error)
        backlight.value(0)
        spi.deinit()


if __name__ == "__main__":
    main()
