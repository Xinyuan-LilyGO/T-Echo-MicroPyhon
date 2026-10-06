"""Manual USB/UART bridge following AT_Debug.ino's transparent loop."""
import sys
for _path in ('/flash/libraries', '/libraries', '/lib', 'libraries'):
    if _path not in sys.path:
        sys.path.append(_path)
try:
    _base = __file__.replace('\\', '/').rsplit('/', 1)[0]
    sys.path.append(_base + '/../../../libraries')
except NameError:
    pass

import select
import time
from machine import Pin
import t_echo_config as board

# Only the original hardware PWRKEY sequence runs automatically, never AT
# commands. Set False when keeping an already powered modem running.
POWER_CYCLE = True
SHOW_DISPLAY = False
# Default: forward exactly what the terminal sends, as in the Arduino loop.
# Thonny sends LF on Enter; set True to add CR before a standalone LF.
ADD_CR_BEFORE_LF = False


def write_all(stream, data):
    """Finish partial writes without parsing the bytes being forwarded."""
    view = memoryview(data)
    offset = 0
    started = time.ticks_ms()
    while offset < len(view):
        sent = stream.write(view[offset:])
        if sent:
            offset += sent
            started = time.ticks_ms()
        elif time.ticks_diff(time.ticks_ms(), started) >= 5000:
            raise OSError('Serial write timeout')
        else:
            time.sleep_ms(1)


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


def setup_modem():
    board.power_on()
    led = RGBStatus()
    Pin(board.ePaper_Backlight, Pin.OUT, value=0)
    Pin(board.UserButton_Pin, Pin.IN, Pin.PULL_UP)
    Pin(board.Touch_Pin, Pin.IN, Pin.PULL_UP)
    power = Pin(board.A7682_PWR, Pin.OUT, value=0)
    uart = None
    try:
        if POWER_CYCLE:
            print('Power cycling A7682 (about 10 seconds)...')
            # Same PWRKEY levels/delays as the original sketch.
            for value, delay in ((1, 4000), (0, 4000), (0, 100),
                                 (1, 1000), (0, 500)):
                power.value(value)
                time.sleep_ms(delay)
        Pin(board.A7682_RI, Pin.IN, Pin.PULL_UP)
        Pin(board.A7682_DTR, Pin.OUT, value=0)
        time.sleep_ms(200)
        uart = board.modem_uart(115200)
        display = None
        if SHOW_DISPLAY:
            try:
                display = setup_display()
            except (ImportError, OSError, MemoryError) as error:
                print('Display unavailable; continuing with the AT console:', error)
        return uart, display, led
    except BaseException:
        led.off()
        if uart is not None:
            uart.deinit()
        raise
    finally:
        power.value(0)


def setup_display():
    from ssd1681 import SSD1681
    from gfx_display import GFXDisplay
    from display_assets import logo
    from font_freemonobold12 import FONT as FreeMonoBold12pt7b
    panel = SSD1681(board.display_spi(), board.ePaper_Cs, board.ePaper_Dc,
                    board.ePaper_Rst, board.ePaper_Busy, rotation=2)
    panel.begin()
    display = GFXDisplay(panel)
    display.setFont(FreeMonoBold12pt7b)
    display.setTextColor(0)
    logo(panel)
    display.update()
    return display


def main():
    print('A7682 manual AT console: 115200 baud; MCU TX=P0.06, RX=P0.08')
    uart, display, led = setup_modem()
    # MicroPython stdio accepts bytes; use binary streams when exposed so
    # incoming UART data never goes through Unicode decoding/re-encoding.
    console_in = getattr(sys.stdin, 'buffer', sys.stdin)
    console_out = getattr(sys.stdout, 'buffer', sys.stdout)
    poll = None
    registered = False
    previous_cr = False
    try:
        poll = select.poll()
        poll.register(console_in, select.POLLIN)
        registered = True
        print('AT console ready. Type AT and send with CR+LF; Ctrl-C stops.')
        if ADD_CR_BEFORE_LF:
            print('Enter compatibility enabled: standalone LF becomes CR+LF.')
        while True:
            led.update()
            # Bound each batch so a long paste cannot starve modem replies.
            for _ in range(64):
                if not poll.poll(0):
                    break
                data = console_in.read(1)
                if not data:
                    break
                if isinstance(data, str):
                    data = data.encode()
                if ADD_CR_BEFORE_LF and data == b'\n' and not previous_cr:
                    write_all(uart, b'\r')
                write_all(uart, data)
                previous_cr = data == b'\r'
            count = uart.any()
            if count:
                data = uart.read(min(count, 512))
                if data:
                    write_all(console_out, data)
            time.sleep_ms(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if registered:
                poll.unregister(console_in)
        finally:
            led.off()
            uart.deinit()
        print('\nAT console stopped.')


if __name__ == '__main__':
    main()
