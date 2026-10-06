"""ble_hid_central.ino: paired BLE keyboard with e-paper output."""
import sys
# Put the intended libraries before the current directory and older copies.
HID_LIBRARY_PATHS = ['/flash/libraries']
if "__file__" in globals() and "/" in __file__:
    HID_LIBRARY_PATHS.append(__file__.rsplit("/", 1)[0] + "/../../libraries")
HID_LIBRARY_PATHS.extend(('/libraries', '/lib', '../../libraries'))
for _path in reversed(HID_LIBRARY_PATHS):
    while _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

import time
from machine import Pin
import t_echo_config as board
from ssd1681 import SSD1681
from gfx_display import GFXDisplay
from font_freemonobold12 import FONT as FreeMonoBold12pt7b

PROTOCOL_MODE = 1  # Matches the ino's hid.setBootMode(false); 0 = Boot mode.


def load_hid():
    # A previous run can leave old Python modules cached after files are copied.
    # Do this before acquiring the BLE peripheral or initializing the display.
    for name in ('ble_hid', 'ble_hid_nrf'):
        sys.modules.pop(name, None)
    errors = []
    module = None
    try:
        import ble_hid as module
    except ImportError:
        errors.append('Upload ble_hid.py to /flash/libraries')
    if module is not None:
        version = getattr(module, 'HID_LIBRARY_API', 0)
        print('[HID LIB] file=%s API=%s (expected 3)' %
              (getattr(module, '__file__', '<frozen>'), version))
        if version != 3:
            errors.append('Replace old ble_hid.py in /flash/libraries with the API 3 file, then restart Python')
    # Match HIDCentral's import order; a stale optional native module must
    # not block standard bluetooth or the stock discovery-only fallback.
    backend_found = False
    backend_error = None
    try:
        import bluetooth
    except ImportError:
        try:
            import _ble_hid
        except ImportError:
            pass
        else:
            version = getattr(_ble_hid, 'API_VERSION', 0)
            print('[HID FW] _ble_hid API=%s (expected 1)' % version)
            if version != 1:
                backend_error = 'The native HID module API is incompatible'
            else:
                try:
                    import ble_hid_nrf
                except (ImportError, RuntimeError):
                    backend_error = 'Upload matching ble_hid_nrf.py to /flash/libraries'
                else:
                    version = getattr(ble_hid_nrf, 'HID_LIBRARY_API', 0)
                    print('[HID ADAPTER] API=%s (expected 2)' % version)
                    backend_found = version == 2
                    if not backend_found:
                        backend_error = 'Replace old ble_hid_nrf.py in /flash/libraries with the API 2 file'
    else:
        backend_found = True
        print('[HID FW] Standard bluetooth module available')
    if not backend_found:
        try:
            from ubluepy import Scanner
        except ImportError:
            print('[HID FW] no usable bluetooth/_ble_hid/ubluepy Scanner backend')
            errors.append(backend_error or 'BLE scanning support is missing from this firmware')
        else:
            if backend_error:
                print('[HID FW]', backend_error, '- using ubluepy scanning')
            print('[HID FW] ubluepy: scan only; no pairing or keyboard input')
    if errors:
        for error in errors:
            print('[INSTALL]', error)
        raise RuntimeError('HID installation incomplete; see [INSTALL] above')
    return module


class KeyboardScreen:
    COLUMNS = 15
    ROWS = 9

    def __init__(self):
        self.lines = ['']

    def newline(self):
        if len(self.lines) == self.ROWS:
            self.lines = ['']
        else:
            self.lines.append('')

    def feed(self, character):
        if character == '\b':
            if self.lines[-1]:
                self.lines[-1] = self.lines[-1][:-1]
            elif len(self.lines) > 1:
                self.lines.pop()
        elif character in ('\n', '\r'):
            self.newline()
        elif character == '\t':
            for _ in range(4 - len(self.lines[-1]) % 4):
                self.feed(' ')
        elif character >= ' ':
            if len(self.lines[-1]) == self.COLUMNS:
                self.newline()
            self.lines[-1] += character

    def draw(self, display):
        display.fillScreen(1)
        for row, line in enumerate(self.lines):
            for column, character in enumerate(line):
                display.setCursor(column * 13, 20 + row * 21)
                display.print(character)
        display.update()


def main():
    print('BLE HID Central v4: checking installed libraries and firmware')
    ble_hid_module = load_hid()
    board.power_on()
    Pin(board.ePaper_Backlight, Pin.OUT, value=0)
    panel = SSD1681(board.display_spi(), board.ePaper_Cs, board.ePaper_Dc,
                    board.ePaper_Rst, board.ePaper_Busy, rotation=3)
    panel.begin()
    display = GFXDisplay(panel)
    display.setFont(FreeMonoBold12pt7b)
    display.setTextColor(0)
    decoder = ble_hid_module.KeyboardDecoder()
    yes = Pin(board.UserButton_Pin, Pin.IN, Pin.PULL_UP)
    no = Pin(board.Touch_Pin, Pin.IN)
    leds = [Pin(n, Pin.OUT, value=1) for n in
            (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin)]
    for _ in range(10):
        for led in leds:
            led.value(1 - led.value())
        time.sleep_ms(300)
    for led in leds:
        led.value(1)
    state = {"keys": [], "status": "Scan ble keyboard", "dirty": True,
             "passkey": None, "clear": True}
    text_screen = KeyboardScreen()

    def keyboard(report):
        if report and report[0] & 0x11:
            print('Ctrl ', end='')
        if report and report[0] & 0x22:
            print('Shift ', end='')
        if report and report[0] & 0x44:
            print('Alt ', end='')
        state['keys'].extend(decoder.feed(report))
        if len(state['keys']) > 256:
            raise OSError('Keyboard display queue overflow')

    def status(message):
        print(message)
        state["status"] = ('Scan ble keyboard' if message == 'Scan BLE keyboard'
                           else 'Connect to the BLE keyboard' if message == 'Keyboard connected'
                           else message)
        state["dirty"] = True
        if message == "Scan BLE keyboard":
            decoder.held = set()
            decoder.caps = False
            state['passkey'] = None
            state['keys'] = []
        if message == "Keyboard connected":
            state["passkey"] = None
            state['keys'] = []
            state['clear'] = True

    def passkey(action, number):
        print("Passkey: %06d" % number)
        if action == 4:
            print('Compare codes: USER button accepts; touch pad rejects (30 seconds).')
        else:
            print('Type these 6 digits on the BLE keyboard, then press Enter.')
        state["passkey"] = "%06d" % number
        state["dirty"] = True

    central = ble_hid_module.HIDCentral(keyboard, status, passkey, protocol_mode=PROTOCOL_MODE)
    panel.idle_callback = central.poll
    last_led = time.ticks_ms()
    led_index = 0
    try:
        central.start()
        while True:
            central.poll()
            if central.pending_confirmation is not None:
                if not yes.value():
                    central.confirm(True)
                elif no.value():
                    central.confirm(False)
            now = time.ticks_ms()
            if time.ticks_diff(now, last_led) >= 1000:
                for i, led in enumerate(leds):
                    led.value(i != led_index)
                led_index = (led_index + 1) % 3
                last_led = now
            if state["dirty"]:
                state["dirty"] = False
                display.fillScreen(1)
                display.setCursor(0, 20)
                if state["passkey"]:
                    display.println('Passkey:')
                    display.println(state['passkey'])
                else:
                    display.print(state['status'])
                display.update()
            if state['keys']:
                if state['clear']:
                    text_screen = KeyboardScreen()
                    state['clear'] = False
                # Group reports collected during the previous e-paper refresh.
                for _ in range(min(32, len(state['keys']))):
                    character = state['keys'].pop(0)
                    text_screen.feed(character)
                    print(character, end='')
                text_screen.draw(display)
            time.sleep_ms(10)
    finally:
        panel.idle_callback = None
        central.close()
        board.leds_off()


if __name__ == "__main__":
    main()
