"""Port of MPU9250.ino: e-paper yaw/pitch/roll with Madgwick fusion."""

import sys
_here = globals().get("__file__", "").replace("\\", "/")
_base = _here.rsplit("/", 3)[0] if _here.count("/") >= 3 else "."
for _path in ("/flash/libraries", "/libraries", "/lib", _base + "/libraries", "../../libraries"):
    if _path not in sys.path:
        sys.path.append(_path)

import time
from machine import Pin
import t_echo_config as board
from ssd1681 import SSD1681
from gfx_display import GFXDisplay
from font_freemonobold9 import FONT as FreeMonoBold9pt7b
from font_freemonobold12 import FONT as FreeMonoBold12pt7b
from mpu9250 import MPU9250
from imu_probe import find_imu

DISPLAY_INTERVAL_MS = 1000
POLL_INTERVAL_MS = 5


def _make_imu(i2c):
    return find_imu(i2c, MPU9250, "MPU9250/MPU9255", (0x71, 0x73))


def main():
    board.power_on()
    time.sleep_ms(100)
    print("MPU9250 example v2: checking the actual sensor")
    try:
        imu = _make_imu(board.shared_i2c(freq=100000))
    except OSError as error:
        print("Stopped: %s" % error)
        return
    Pin(board.ePaper_Backlight, Pin.OUT, value=0)
    Pin(board.UserButton_Pin, Pin.IN, Pin.PULL_UP)
    Pin(board.Touch_Pin, Pin.IN, Pin.PULL_UP)
    leds = [Pin(pin, Pin.OUT, value=1) for pin in
            (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin)]
    for _ in range(10):
        for led in leds:
            led.value(1 - led.value())
        time.sleep_ms(300)
    for led in leds:
        led.value(1)
    panel = None
    try:
        imu.begin()
        print("MPU9250/MPU9255 connected at 0x%02X" % imu.address)
        panel = SSD1681(board.display_spi(), board.ePaper_Cs, board.ePaper_Dc,
                        board.ePaper_Rst, board.ePaper_Busy, rotation=3,
                        low_memory=True)
        panel.begin()
        display = GFXDisplay(panel)
        display.setTextColor(0)
        display.setCursor(15, 25)
        display.setFont(FreeMonoBold9pt7b)
        display.fillScreen(1)
        display.println('ePaper SelfTest')
        display.drawFastHLine(0, display.getCursorY() - 5, display.width(), 0)
        display.println()
        display.setFont(FreeMonoBold12pt7b)
        display.println('[MPU9250]    +')
        display.update()
        time.sleep_ms(1000)
        display.fillScreen(1)
        display.update()
        last_display = time.ticks_ms()
        while True:
            if (imu.update() and
                    time.ticks_diff(time.ticks_ms(), last_display) >= DISPLAY_INTERVAL_MS):
                yaw, pitch, roll = imu.orientation()
                print("Yaw: %.2f  Pitch: %.2f  Roll: %.2f" % (yaw, pitch, roll))
                display.fillScreen(1)
                display.setCursor(0, 25)
                display.println('mpu9250_test: ')
                display.print('Yaw: ')
                display.println('%.2f' % yaw)
                display.print('Pitch: ')
                display.println('%.2f' % pitch)
                display.print('Roll: ')
                display.println('%.2f' % roll)
                panel.show(panel.PARTIAL_REFRESH, sleep=False)
                last_display = time.ticks_ms()
            time.sleep_ms(POLL_INTERVAL_MS)
    except KeyboardInterrupt:
        print("MPU9250 stopped.")
    except OSError as error:
        print("MPU9250 stopped: %s" % error)
    finally:
        try:
            imu.sleep()
        except OSError as error:
            print("MPU9250 cleanup: %s" % error)
        if panel is not None:
            try:
                panel.sleep()
            except OSError as error:
                print("Display cleanup: %s" % error)


if __name__ == "__main__":
    main()
