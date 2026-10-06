"""MQTT subscribe/publish over A7682, matching MqttClient.ino topics."""
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
from modem_protocols import MQTTClient

GSM_PIN = ''
APN = 'YourAPN'
GPRS_USER = ''
GPRS_PASS = ''
BROKER = 'broker.hivemq.com'
CLIENT_ID = 'GsmClientTest'
MQTT_USER = None
MQTT_PASS = None
TOPIC_LED = 'GsmClientTest/led'
TOPIC_INIT = 'GsmClientTest/init'
TOPIC_LED_STATUS = 'GsmClientTest/ledStatus'


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
    display.fillScreen(1)
    display.update()
    print('Modem Name:', modem.command('ATI'))
    return modem, display, led


def wait_retry(led, seconds=10):
    started = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), started) < seconds * 1000:
        led.update()
        time.sleep_ms(20)


def main():
    if APN == 'YourAPN':
        raise ValueError('Set APN in MqttClient.py to your SIM carrier APN')
    modem, display, led = setup_modem(GSM_PIN)
    led_status = [False]
    mqtt = MQTTClient(modem, CLIENT_ID, BROKER, user=MQTT_USER, password=MQTT_PASS)

    def callback(topic, payload):
        print('Message arrived [%s]: %s' % (topic.decode(), payload))
        if topic == TOPIC_LED.encode():
            led_status[0] = not led_status[0]
            value = '1' if led_status[0] else '0'
            print('ledStatus:', value)
            mqtt.publish(TOPIC_LED_STATUS, value)

    mqtt.callback = callback
    try:
        while True:
            try:
                modem.connect_data(APN, GPRS_USER, GPRS_PASS)
                mqtt.connect()
                mqtt.publish(TOPIC_INIT, 'GsmClientTest started')
                mqtt.subscribe(TOPIC_LED)
                while True:
                    mqtt.check_msg()
                    led.update()
                    time.sleep_ms(100)
            except OSError as error:
                print('MQTT reconnect:', error)
                mqtt.close()
                wait_retry(led)
    finally:
        mqtt.close()
        led.off()


if __name__ == '__main__':
    main()
