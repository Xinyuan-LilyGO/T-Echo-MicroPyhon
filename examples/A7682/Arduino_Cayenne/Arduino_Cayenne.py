"""Cayenne MQTT temperature, pressure, altitude and battery channels 1..4."""
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
from bme280 import BME280
from font_freemonobold18 import FONT as FreeMonoBold18pt7b
import t_echo_config as board
from modem_protocols import MQTTClient

APN = 'YourAPN'
GPRS_USER = ''
GPRS_PASS = ''
GSM_PIN = ''
USERNAME = 'Cayenne username'
PASSWORD = 'Cayenne password'
CLIENT_ID = 'Cayenne clientID'
BROKER = 'mqtt.mydevices.com'
PORT = 1883
SEALEVELPRESSURE_HPA = 1013.25
TEMPERATURE_VIRTUAL_CHANNEL = 1
BAROMETER_VIRTUAL_CHANNEL = 2
ALTITUDE_VIRTUAL_CHANNEL = 3
BATTERY_VIRTUAL_CHANNEL = 4
LIGHTSENSOR_VIRTUAL_CHANNEL = 5  # No light sensor/readout exists in the original.
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


def cayenne_callback(mqtt, topic, payload):
    channel = topic.rsplit(b'/', 1)[-1].decode()
    fields = payload.split(b',', 1)
    if len(fields) != 2:
        print('Invalid Cayenne command:', payload)
        return
    sequence, value = fields
    print('Channel %s, value %s' % (channel, value.decode()))
    # CAYENNE_IN(1) and CAYENNE_IN_DEFAULT only log and acknowledge.
    mqtt.publish('v1/%s/things/%s/response' % (USERNAME, CLIENT_ID), b'ok,' + sequence)


def main():
    if (APN == 'YourAPN' or not USERNAME or not PASSWORD or not CLIENT_ID or
            USERNAME.startswith('Cayenne ') or PASSWORD.startswith('Cayenne ') or
            CLIENT_ID.startswith('Cayenne ')):
        raise ValueError('Set APN and Cayenne USERNAME, PASSWORD, CLIENT_ID in Arduino_Cayenne.py')
    modem, display, led = setup_modem(GSM_PIN)
    led.interval_ms = 2000
    try:
        sensor = BME280(board.shared_i2c())
    except OSError as error:
        sensor = None
        print('BME280 unavailable; publishing battery only:', error)
    mqtt = MQTTClient(modem, CLIENT_ID, BROKER, PORT, USERNAME, PASSWORD)
    topic_base = 'v1/%s/things/%s/data/' % (USERNAME, CLIENT_ID)

    def publish(channel, data_type, unit, value):
        mqtt.publish(topic_base + str(channel), '%s,%s=%s' % (data_type, unit, value))

    mqtt.callback = lambda topic, payload: cayenne_callback(mqtt, topic, payload)

    try:
        while True:
            try:
                modem.connect_data(APN, GPRS_USER, GPRS_PASS)
                mqtt.connect()
                mqtt.subscribe('v1/%s/things/%s/cmd/+' % (USERNAME, CLIENT_ID))
                last_sample = time.ticks_ms()
                while True:
                    mqtt.check_msg()
                    led.update()
                    if time.ticks_diff(time.ticks_ms(), last_sample) >= SAMPLE_INTERVAL_MS:
                        last_sample = time.ticks_ms()
                        if sensor:
                            temperature, pressure, humidity = sensor.read()
                            publish(TEMPERATURE_VIRTUAL_CHANNEL, 'temp', 'c', temperature)
                            # The .ino divides Pa by 1000 but labels it hPa; use correct Pa/100 units.
                            publish(BAROMETER_VIRTUAL_CHANNEL, 'bp', 'hpa', pressure)
                            publish(ALTITUDE_VIRTUAL_CHANNEL, 'meters', 'm',
                                    altitude(pressure, SEALEVELPRESSURE_HPA))
                            show_sensor(display, temperature, pressure, humidity)
                        millivolts = board.battery_voltage() * 1000.0
                        publish(BATTERY_VIRTUAL_CHANNEL, 'voltage', 'mv', millivolts)
                        print('Battery: %.1f mV' % millivolts)
                    time.sleep_ms(50)
            except OSError as error:
                print('Cayenne reconnect:', error)
                mqtt.close()
                wait_retry(led)
    finally:
        mqtt.close()
        if sensor:
            sensor.sleep()
        led.off()


if __name__ == '__main__':
    main()
