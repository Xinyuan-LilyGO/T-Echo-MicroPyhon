"""Sleep_Display.ino: original text, FreeSans9 layout and shutdown sequence.

Load every dependency before detaching USB and unmounting external flash.
The original caption says 20 seconds but its loop performs 30 one-second waits.
"""
import sys

_here = globals().get("__file__", "").replace("\\", "/").rsplit("/", 1)[0]
for _path in (_here + "/../../libraries", "/libraries", "/lib", "libraries", "../../libraries"):
    if _path not in sys.path:
        sys.path.append(_path)

from machine import Pin
import time
import t_echo_config as board
from sx1262 import SX1262
from sleep_display_screen import make_screen
from sleep_display_support import SleepResources

STEP_DELAY_MS = 5000


def _power(value):
    Pin(board.Power_Enable_Pin, Pin.OUT, value=value)
    if not value:
        Pin(board.Power_Enable_Pin, Pin.IN, Pin.PULL_DOWN)


def _release_radio():
    for number in (board.LoRa_Miso, board.LoRa_Mosi, board.LoRa_Sclk,
                   board.LoRa_Dio1, board.LoRa_Busy):
        Pin(number, Pin.IN)
    for number in (board.LoRa_Cs, board.LoRa_Rst):
        Pin(number, Pin.IN, Pin.PULL_UP)


def _release_flash():
    for number in (board.Flash_Sclk, board.Flash_Mosi, board.Flash_Miso,
                   board.Flash_WP, board.Flash_HOLD):
        Pin(number, Pin.IN)
    Pin(board.Flash_Cs, Pin.IN, Pin.PULL_UP)


def main():
    print("Ciallo")
    Pin(board.UserButton_Pin, Pin.IN, Pin.PULL_UP)
    _power(1)
    for number in (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin):
        Pin(number, Pin.OUT, value=0)
    Pin(board.ePaper_Backlight, Pin.OUT, value=1)
    Pin(board.Gps_Wakeup_Pin, Pin.OUT, value=1)
    Pin(board.Gps_Reset_Pin, Pin.OUT, value=1)
    Pin(board.Gps_pps_Pin, Pin.IN, Pin.PULL_UP)

    display = make_screen()
    display.begin()
    print("[SX1262] Initializing ... ")
    # The .ino opens SPI but leaves radio.begin() commented out. Do not
    # introduce RF configuration, GPS UART traffic or GPS reset pulses here.
    radio = SX1262(board.radio_spi(), board.LoRa_Cs, board.LoRa_Busy,
                   board.LoRa_Rst, board.LoRa_Dio1)
    resources = SleepResources(require_usb=False)
    try:
        resources.prepare()
        identity = resources.flash_identity()
        if identity in (b"\x00\x00\x00", b"\xff\xff\xff"):
            print("Flash initialization failed")
            raise OSError("Flash initialization failed")
        print("Flash initialization successful")

        def stage(text):
            display.stage(text)
            time.sleep_ms(STEP_DELAY_MS)

        stage("All functions are open normally")
        Pin(board.ePaper_Backlight, Pin.OUT, value=0)
        Pin(board.ePaper_Backlight, Pin.IN, Pin.PULL_DOWN)
        stage("1.Turn off screen backlight")

        if resources.close_usb() is False:
            print("USB shutdown API unavailable; continuing with USB active")
        stage("2.Close USBCDC serial port")

        radio.sleep()
        radio.spi.deinit()
        _release_radio()
        stage("3.Lora enters sleep mode")

        resources.sleep_flash()
        _release_flash()
        stage("4.Flash enters deep sleep mode")

        for number in (board.Gps_Wakeup_Pin, board.Gps_Reset_Pin):
            Pin(number, Pin.OUT, value=0)
            Pin(number, Pin.IN, Pin.PULL_DOWN)
        for number in (board.Gps_pps_Pin, board.Gps_Rx_Pin, board.Gps_Tx_Pin):
            Pin(number, Pin.IN, Pin.PULL_UP)
        stage("5.GPS enters sleep mode")

        for number in (board.SDA_Pin, board.SCL_Pin):
            Pin(number, Pin.OUT, value=1)
            Pin(number, Pin.IN, Pin.PULL_UP)
        stage("6.Close IIC")

        for number in (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin):
            Pin(number, Pin.OUT, value=1)
            Pin(number, Pin.IN, Pin.PULL_UP)
        stage("7.Close LED")

        display.stage("8.NRF52840 enters 20 second light sleep")
        display.end()
        _power(0)
        resources.light_sleep()

        _power(1)
        display.begin()
        display.stage("9.NRF52840 enters deep sleep")
        display.end()
        _power(0)
        # systemOff(nRF52840_BOOT, LOW): P1.10, input, pull-up, SENSE low.
        board.mem32[0x50000A00 + 10 * 4] = (3 << 16) | (3 << 2)
        resources.system_off()
    except BaseException:
        # Restore the filesystem and console before returning to the REPL.
        # Restoring a volume never formats or erases its contents.
        resources.restore()
        raise


if __name__ == "__main__":
    main()
