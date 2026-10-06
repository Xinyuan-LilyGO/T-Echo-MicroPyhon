"""Sleep.ino: original self-test, logo, RGB and touch-low SYSTEMOFF flow.

The RTC test sets 2021-04-13 12:00:57. The source enables only touch LOW
as the wake source, even though a typical TTP223 board is active HIGH.
Requires the supplied USB/SYSTEMOFF-capable firmware. The BLE adapter
provides NUS/BAS; Bluefruit DFU and Device Information are not provided.
"""
import sys

_here = globals().get("__file__", "").replace("\\", "/").rsplit("/", 1)[0]
for _path in (_here + "/../../libraries", "/libraries", "/lib", "libraries", "../../libraries"):
    if _path not in sys.path:
        sys.path.append(_path)

import machine
from machine import Pin
import time
import t_echo_config as board
from ssd1681 import SSD1681
from gfx_display import GFXDisplay
from font_freemonobold9 import FONT as FreeMonoBold9pt7b
from font_freemonobold12 import FONT as FreeMonoBold12pt7b
from font_freemonobold18 import FONT as FreeMonoBold18pt7b
from display_assets import logo
from sx1262 import SX1262
from pcf8563 import PCF8563
from bme280 import BME280
from ble_uart import BleUart, BleUnavailable
from sleep_display_support import SleepResources

USING_BMP280 = False
display = radio = sensor = rtc = uart = i2c = ble = resources = None
rtc_irq = button = Touched = None
leds = ()
blinkMillis = last = rgb = 0
sleepIn = transmittedFlag = rtcInterrupt = False
UserButton_flag = Touch_flag = 0


class _AceButton:
    """The enabled Sleep.ino events with AceButton's 20/1000 ms defaults."""
    def __init__(self, number):
        self.number = number
        self.pin = Pin(number, Pin.IN, Pin.PULL_UP)
        self.raw = self.state = 1
        self.changed = self.pressed = 0
        self.long_pressed = False

    def check(self):
        now = time.ticks_ms()
        value = self.pin.value()
        if value != self.raw:
            self.raw, self.changed = value, now
        if value != self.state and time.ticks_diff(now, self.changed) >= 20:
            self.state = value
            if value == 0:
                self.pressed = now
                self.long_pressed = False
            elif not self.long_pressed:
                button_Handler(self, "released")
        if self.state == 0 and not self.long_pressed and time.ticks_diff(now, self.pressed) >= 1000:
            self.long_pressed = True
            button_Handler(self, "long")


def setupGPS():
    global uart
    print("[GPS] Initializing ... ")
    uart = board.gps_uart(9600)
    Pin(board.Gps_pps_Pin, Pin.IN)
    Pin(board.Gps_Wakeup_Pin, Pin.OUT, value=1)
    time.sleep_ms(10)
    reset = Pin(board.Gps_Reset_Pin, Pin.OUT, value=1)
    time.sleep_ms(10)
    reset.value(0)
    time.sleep_ms(10)
    reset.value(1)
    return True


def setupSensor():
    global sensor
    print("[SENSOR ] Initializing ...  ", end="")
    try:
        sensor = BME280(i2c, address=0x77)
        if sensor.has_humidity == USING_BMP280:
            raise OSError("Unexpected barometer model")
        if USING_BMP280:
            sensor.sleep()
            sensor._write(0xF5, 0x90)
            sensor.wake()
        print("success")
        return True
    except OSError as error:
        print("failed", error)
        sensor = None
        return False


def sleepSensor():
    if sensor is None:
        return
    # The original BME branch uses MODE_NORMAL and setSampling's defaults.
    sensor.sleep()
    sensor._write(0xF5, 0)
    if not USING_BMP280:
        sensor._write(0xF2, 5)
    sensor._write(0xF4, 0xB4 if USING_BMP280 else 0xB7)


def setupFlash():
    print("[FLASH ] Initializing ...  ", end="")
    try:
        identity = resources.flash_identity()
        if identity != b"\xba\x60\x15":
            raise OSError("Expected ZD25WQ16B JEDEC BA6015, got %r" % identity)
        print("JEDEC ID: 0x%02X%02X%02X" % tuple(identity))
        print("Flash size: 2048 KB")
        return True
    except OSError as error:
        print("failed", error)
        return False


def rtcInterruptCb(pin):
    global rtcInterrupt
    rtcInterrupt = True


def setupRTC():
    global rtc, rtc_irq, i2c, rtcInterrupt, last
    print("[PCF8563] Initializing ...  ", end="")
    rtc_irq = Pin(board.RTC_Int_Pin, Pin.IN)
    rtc_irq.irq(trigger=Pin.IRQ_FALLING, handler=rtcInterruptCb)
    i2c = board.shared_i2c()
    present = False
    # The source do/while executes all four probes, each followed by 200 ms.
    for _ in range(4):
        try:
            present = i2c.writeto(0x51, b"") == 0
        except OSError:
            present = False
        time.sleep_ms(200)
    if not present:
        print("failed")
        return False
    try:
        rtc = PCF8563(i2c)
        print("success")
        print("============rtc self test==============")
        year, mon, day, hour, minute, sec = 2021, 4, 13, 12, 0, 57
        rtc.enable_alarm(False)
        rtc.set_datetime(year, mon, day, hour, minute, sec)
        rtc.set_alarm(minute=1)
        rtc.enable_alarm()
        seconds = 0
        while True:
            now = time.ticks_ms()
            if time.ticks_diff(now, last) > 1000:
                stamp = rtc.datetime()
                if stamp is not None:
                    print("%d:%d:%d" % stamp[3:])
                last = now
                seconds += 1
                if seconds >= 10:
                    print("RTC alarm is unusual !")
                    return False
            if rtcInterrupt:
                rtcInterrupt = False
                rtc.clear_alarm()
                # Sleep.ino checks the original local values, not an RTC read.
                if (year, mon, day, hour, minute) != (2021, 4, 13, 12, 0):
                    print("RTC datetime is unusual !")
                    return False
                print("RTC alarm is normal !")
                return True
            time.sleep_ms(1)
    except OSError as error:
        print("failed", error)
        return False


def setupDisplay():
    global display
    panel = SSD1681(board.display_spi(), board.ePaper_Cs, board.ePaper_Dc,
                    board.ePaper_Rst, board.ePaper_Busy, rotation=3)
    display = GFXDisplay(panel)
    display.setRotation(3)
    display.fillScreen(1)
    display.setTextColor(0)
    display.setFont(FreeMonoBold12pt7b)


def setFlag(pin):
    global transmittedFlag
    transmittedFlag = True


def setupLoRa():
    global radio
    print("[SX1262] Initializing ...  ", end="")
    radio = SX1262(board.radio_spi(), board.LoRa_Cs, board.LoRa_Busy,
                   board.LoRa_Rst, board.LoRa_Dio1)
    try:
        # RadioLib begin(868.0) uses its actual defaults, including 10 dBm.
        radio.begin(868.0)
        radio.dio1.irq(trigger=Pin.IRQ_RISING, handler=setFlag)
        print(" success")
        return True
    except OSError as error:
        print("failed, code", error)
        return False


def connect_callback(handle):
    print("Connected to", handle)


def disconnect_callback(handle, reason):
    print("\nDisconnected, reason = 0x%X" % reason)


def setupBLE():
    global ble
    try:
        ble = BleUart("Bluefruit52", on_connect=connect_callback,
                      on_disconnect=disconnect_callback)
        ble.set_battery_level(100)
        ble.start()
    except BleUnavailable as error:
        print("BLE unavailable:", error)
        return
    print("Please use Adafruit's Bluefruit LE app to connect in UART mode")
    print("Once connected, enter character(s) that you wish to send")
    print("Firmware BLE adapter lacks Bluefruit DFU/DIS and advertising/TX-power controls")


def boardInit():
    global leds, button, Touched
    print("Start\n")
    print("sd_power_reset_reason_get:%X" % machine.mem32[0x40000400])
    Pin(board.Power_Enable_Pin, Pin.OUT, value=1)
    Pin(board.ePaper_Backlight, Pin.OUT, value=1)
    leds = tuple(Pin(number, Pin.OUT) for number in
                 (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin))
    button = _AceButton(board.UserButton_Pin)
    Touched = _AceButton(board.Touch_Pin)
    for _ in range(10):
        for led in leds:
            led.value(not led.value())
        time.sleep_ms(300)
    for led in leds:
        led.value(1)
    setupBLE()
    # Resolve SYSTEMOFF after BLE activation, and before the filesystem sleeps.
    resources.prepare()
    setupDisplay()
    result = 0
    for bit, setup_device in ((1, setupGPS), (2, setupLoRa), (3, setupFlash),
                               (4, setupRTC), (5, setupSensor)):
        if setup_device():
            result |= 1 << bit
    display.setCursor(15, 25)
    display.setFont(FreeMonoBold9pt7b)
    display.fillScreen(1)
    display.println("ePaper SeftTest")
    display.drawFastHLine(0, display.getCursorY() - 5, display.width(), 0)
    display.println()
    display.setFont(FreeMonoBold12pt7b)
    for bit, label in ((1, "[GPS]       "), (2, "[SX1262]    "),
                       (3, "[FLASH]     "), (4, "[PCF8563]   "), (5, "[BARO]      ")):
        display.print(label)
        display.println("+" if result & (1 << bit) else "-")
    display.update()
    time.sleep_ms(500)


def sleepPeripherals():
    resources.sleep_flash()
    if radio is not None:
        radio.sleep()
    sleepSensor()
    Pin(board.ePaper_Backlight, Pin.OUT, value=0)
    if i2c is not None and hasattr(i2c, "deinit"):
        i2c.deinit()
    for number in (board.SDA_Pin, board.SCL_Pin):
        Pin(number, Pin.IN)
    if radio is not None:
        radio.spi.deinit()
    for number in (board.LoRa_Miso, board.LoRa_Mosi, board.LoRa_Sclk):
        Pin(number, Pin.IN)
    Pin(board.LoRa_Cs, Pin.IN, Pin.PULL_UP)
    Pin(board.LoRa_Rst, Pin.IN)
    display.panel.spi.deinit()
    for number in (board.ePaper_Miso, board.ePaper_Mosi, board.ePaper_Sclk,
                   board.ePaper_Cs, board.ePaper_Dc, board.ePaper_Rst,
                   board.ePaper_Busy, board.Flash_Cs, board.Flash_Miso,
                   board.Flash_Mosi, board.Flash_Sclk):
        Pin(number, Pin.IN)
    Pin(board.Power_Enable_Pin, Pin.OUT, value=0)
    Pin(board.Power_Enable_Pin, Pin.IN)
    for number in (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin):
        Pin(number, Pin.IN)
    if uart is not None:
        uart.deinit()
    for number in (board.Gps_Wakeup_Pin, board.Gps_Rx_Pin, board.Gps_Tx_Pin,
                   board.Gps_Reset_Pin, board.Gps_pps_Pin, board.Adc_Pin):
        Pin(number, Pin.IN)


def sleepnRF52840():
    global sleepIn
    display.fillScreen(1)
    display.setFont(FreeMonoBold18pt7b)
    display.setCursor(50, 100)
    display.println("Sleep")
    display.update()
    time.sleep_ms(2000)
    sleepIn = True
    sleepPeripherals()
    for number in (board.UserButton_Pin, board.Touch_Pin, board.RTC_Int_Pin):
        port, bit = divmod(number, 32)
        address = 0x50000700 + port * 0x300 + bit * 4
        machine.mem32[address] &= ~(3 << 16)
    Pin(board.Touch_Pin, Pin.IN, Pin.PULL_UP)
    machine.mem32[0x50000700 + board.Touch_Pin * 4] = (3 << 16) | (3 << 2)
    if resources.close_usb() is False:
        print("USB shutdown unavailable; keeping USB active before SYSTEMOFF")
    resources.system_off()


def button_Handler(key, event):
    global UserButton_flag, Touch_flag
    if key.number == board.UserButton_Pin:
        if event == "released":
            UserButton_flag = 1
        elif event == "long":
            sleepnRF52840()
    elif key.number == board.Touch_Pin:
        if event == "released":
            Touch_flag = 1
        elif event == "long":
            print("Touch kEventLongPressed")


def setup():
    time.sleep_ms(200)
    boardInit()
    time.sleep_ms(2000)
    print("setup")
    display.setRotation(2)
    display.setFont(FreeMonoBold12pt7b)
    display.fillScreen(1)
    logo(display.panel)
    display.update()
    display.setRotation(3)
    print("setup dome")


def loop():
    global blinkMillis, rgb
    now = time.ticks_ms()
    if time.ticks_diff(now, blinkMillis) > 1000:
        blinkMillis = now
        for index, led in enumerate(leds):
            led.value(0 if index == rgb else 1)
        rgb = (rgb + 1) % 3
    button.check()
    Touched.check()
    machine.lightsleep()


def main():
    global resources
    resources = SleepResources(require_usb=False)
    try:
        setup()
        while True:
            loop()
    except BaseException:
        resources.restore()
        raise


if __name__ == "__main__":
    main()
