"""Dial for 60 seconds, then use the user button to answer/hang up calls."""
import sys
for _path in ('/libraries', '/lib', 'libraries'):
    if _path not in sys.path:
        sys.path.append(_path)
try:
    _base = __file__.replace('\\', '/').rsplit('/', 1)[0]
    sys.path.append(_base + '/../../../libraries')
except NameError:
    pass

from machine import Pin
import time
from a7682 import A7682
from ssd1681 import SSD1681
from gfx_display import GFXDisplay
from display_assets import logo
from font_freemonobold12 import FONT as FreeMonoBold12pt7b
import t_echo_config as board

GSM_PIN = ''
NUMBER = '+380xxxxxxxxx'
CALL_DURATION_MS = 60000


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
                    board.ePaper_Rst, board.ePaper_Busy, rotation=3)
    panel.begin()
    display = GFXDisplay(panel)
    display.setFont(FreeMonoBold12pt7b)
    display.setTextColor(0)
    display.fillScreen(1)
    display.setCursor(15, 25)
    display.print('start  test  ')
    display.update()
    if require_sim:
        modem.init(sim_pin)
    else:
        modem.command('ATE0')
        modem.command('AT+CMEE=2')
    print('Modem Name:', modem.command('ATI'))
    return modem, display, led


def main():
    modem, display, led = setup_modem(GSM_PIN)
    led.interval_ms = 3000
    button = Pin(board.UserButton_Pin, Pin.IN, Pin.PULL_UP)
    modem.wait_for_network()
    if NUMBER and all(character in '+0123456789*#' for character in NUMBER):
        try:
            modem.hangup()
            modem.dial(NUMBER)
            print('Dialing:', NUMBER)
            started = time.ticks_ms()
            while time.ticks_diff(time.ticks_ms(), started) < CALL_DURATION_MS:
                modem.poll()
                led.update()
                time.sleep_ms(20)
        finally:
            modem.hangup()
    else:
        print('Set NUMBER for the outgoing test; incoming calls remain available.')
    display.fillScreen(1)
    display.update()
    previous_button = button.value()
    stable_button = previous_button
    edge_at = time.ticks_ms()
    last_check = time.ticks_ms()
    shown_calls = None
    try:
        while True:
            now = time.ticks_ms()
            raw = button.value()
            if raw != previous_button:
                previous_button = raw
                edge_at = now
            if raw != stable_button and time.ticks_diff(now, edge_at) >= 30:
                stable_button = raw
                if raw:
                    calls = modem.calls()
                    if any(call['state'] in (4, 5) for call in calls):
                        modem.answer()
                    else:
                        modem.hangup()
            if time.ticks_diff(now, last_check) >= 3000:
                last_check = now
                calls = modem.calls()
                signature = [(call['state'], call['number']) for call in calls]
                if signature != shown_calls:
                    shown_calls = signature
                    display.fillScreen(1)
                    display.setCursor(0, 20)
                    for call in calls:
                        if call['state'] in (4, 5):
                            display.println('RING...')
                            display.println('Number:')
                            display.print(call['number'])
                    display.update()
                    print('Calls:', calls)
            modem.poll()
            led.update()
            time.sleep_ms(10)
    finally:
        modem.hangup()
        led.off()


if __name__ == '__main__':
    main()
