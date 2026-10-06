"""Publish BME280 data to Blynk V0..V3 every two seconds via A7682."""
import sys
for _path in ('/libraries', '/lib', 'libraries'):
    if _path not in sys.path:
        sys.path.append(_path)
try:
    _base = __file__.replace('\\', '/').rsplit('/', 1)[0]
    sys.path.append(_base + '/../../../libraries')
except NameError:
    pass

import time
from a7682 import A7682
from ssd1681 import SSD1681
from gfx_display import GFXDisplay
from display_assets import logo
from font_freemonobold12 import FONT as FreeMonoBold12pt7b
from machine import Pin
import t_echo_config as board
from bme280 import BME280
from font_freemonobold18 import FONT as FreeMonoBold18pt7b
from modem_protocols import BlynkClient

BLYNK_TEMPLATE_ID = ''
BLYNK_DEVICE_NAME = ''
BLYNK_AUTH_TOKEN = ''
BLYNK_SERVER = 'blynk.cloud'
BLYNK_PORT = 80
GSM_PIN = ''
APN = 'YourAPN'
GPRS_USER = ''
GPRS_PASS = ''
SEALEVELPRESSURE_HPA = 1013.25
SAMPLE_INTERVAL_MS = 2000


class RGBStatus:
    def __init__(self, interval_ms=1000):
        self.leds = [Pin(pin, Pin.OUT, value=1) for pin in
                     (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin)]
        self.interval_ms = interval_ms
        self.last = time.ticks_ms()
        self.index = 0

    def update(self):
        if time.ticks_diff(time.ticks_ms(), self.last) >= self.interval_ms:
            self.last = time.ticks_ms()
            for index, led in enumerate(self.leds):
                led.value(0 if index == self.index else 1)
            self.index = (self.index + 1) % 3

    def off(self):
        for led in self.leds:
            led.value(1)


def setup_modem(sim_pin='', require_sim=True):
    board.power_on()
    led = RGBStatus()
    Pin(board.ePaper_Backlight, Pin.OUT, value=0)
    Pin(board.UserButton_Pin, Pin.IN, Pin.PULL_UP)
    Pin(board.Touch_Pin, Pin.IN, Pin.PULL_UP)
    modem = A7682(board.modem_uart(115200), board.A7682_PWR,
                  board.A7682_DTR, board.A7682_RI)
    modem.power_on(force_cycle=True)
    print('Start\n')
    for _ in range(10):
        for light in led.leds:
            light.value(1 - light.value())
        time.sleep_ms(300)
    led.off()
    panel = SSD1681(board.display_spi(), board.ePaper_Cs, board.ePaper_Dc,
                    board.ePaper_Rst, board.ePaper_Busy, rotation=2)
    panel.begin()
    display = GFXDisplay(panel)
    display.setFont(FreeMonoBold12pt7b)
    display.setTextColor(0)
    logo(panel)
    display.update()
    if require_sim:
        modem.init(sim_pin)
    else:
        modem.command('ATE0')
        modem.command('AT+CMEE=2')
    display.setRotation(3)
    display.fillScreen(1)
    print('Modem Name:', modem.command('ATI'))
    return modem, display, led


def wait_retry(led, seconds=10):
    started = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), started) < seconds * 1000:
        led.update()
        time.sleep_ms(20)


def altitude(pressure_hpa, sea_level_hpa=1013.25):
    return 44330.0 * (1.0 - (pressure_hpa / sea_level_hpa) ** 0.1903)


def show_sensor(display, temperature, pressure, humidity):
    if humidity is not None:
        print('Humidity = %.2f %%' % humidity)
    print('Temperature = %.2f *C' % temperature)
    print('Pressure = %.2f hPa' % pressure)
    print()
    display.fillScreen(1)
    display.setFont(FreeMonoBold12pt7b)
    display.setCursor(0, 25)
    display.println('[Temperature]')
    display.setFont(FreeMonoBold18pt7b)
    display.setCursor(30, 80)
    display.print(temperature)
    display.setFont(FreeMonoBold12pt7b)
    display.setCursor(display.getCursorX(), display.getCursorY() - 20)
    display.println('*C')
    display.setCursor(0, 120)
    display.println('[Pressure]')
    display.setFont(FreeMonoBold18pt7b)
    display.setCursor(30, 175)
    display.print(pressure)
    display.setFont(FreeMonoBold12pt7b)
    display.setCursor(display.getCursorX(), display.getCursorY() - 20)
    display.println('%')  # Preserve the original sketch's screen label.
    display.panel.show(display.panel.PARTIAL_REFRESH, sleep=False)
    time.sleep_ms(100)


def main():
    if not BLYNK_AUTH_TOKEN or APN == 'YourAPN':
        raise ValueError('Set BLYNK_AUTH_TOKEN and APN in Blynk_Console.py')
    modem, display, led = setup_modem(GSM_PIN)
    sensor = BME280(board.shared_i2c())
    blynk = BlynkClient(modem, BLYNK_AUTH_TOKEN, BLYNK_SERVER, BLYNK_PORT,
                        BLYNK_TEMPLATE_ID, BLYNK_DEVICE_NAME)
    try:
        while True:
            try:
                modem.connect_data(APN, GPRS_USER, GPRS_PASS)
                blynk.connect()
                last_sample = time.ticks_ms()
                while True:
                    blynk.run()
                    led.update()
                    if time.ticks_diff(time.ticks_ms(), last_sample) >= SAMPLE_INTERVAL_MS:
                        last_sample = time.ticks_ms()
                        temperature, pressure, humidity = sensor.read()
                        print('Temperature: %.2f C; Pressure: %.2f hPa; Humidity: %s' %
                              (temperature, pressure, humidity))
                        blynk.virtual_write(0, pressure)
                        blynk.virtual_write(1, temperature)
                        if humidity is not None:
                            blynk.virtual_write(2, humidity)
                        blynk.virtual_write(3, altitude(pressure, SEALEVELPRESSURE_HPA))
                        show_sensor(display, temperature, pressure, humidity)
                    time.sleep_ms(50)
            except OSError as error:
                print('Blynk reconnect:', error)
                blynk.close()
                wait_retry(led)
    finally:
        blynk.close()
        sensor.sleep()
        led.off()


if __name__ == '__main__':
    main()
