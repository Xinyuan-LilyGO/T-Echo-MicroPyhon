"""Shared T-Echo peripheral shutdown for the Sleep examples."""

from machine import Pin
import time
import t_echo_config as board


def release_peripheral_pins():
    """Call only after peripheral drivers have entered sleep and deinitialized."""
    keep_flash = board.external_flash_mounted()
    for number in (board.ePaper_Mosi, board.ePaper_Sclk, board.ePaper_Dc,
                   board.ePaper_Busy, board.LoRa_Miso, board.LoRa_Mosi,
                   board.LoRa_Sclk, board.LoRa_Dio1, board.LoRa_Busy,
                   board.Gps_Rx_Pin, board.Gps_Tx_Pin, board.Gps_pps_Pin,
                   board.Adc_Pin, board.RTC_Int_Pin):
        Pin(number, Pin.IN)
    for number in (board.LoRa_Cs, board.LoRa_Rst,
                   board.ePaper_Cs, board.ePaper_Rst):
        Pin(number, Pin.IN, Pin.PULL_UP)
    if not keep_flash:
        for number in (board.Flash_Miso, board.Flash_Mosi, board.Flash_Sclk,
                       board.Flash_WP, board.Flash_HOLD):
            Pin(number, Pin.IN)
        Pin(board.Flash_Cs, Pin.IN, Pin.PULL_UP)
    for number in (board.Gps_Wakeup_Pin, board.Gps_Reset_Pin, board.ePaper_Backlight):
        Pin(number, Pin.OUT, value=0)
        Pin(number, Pin.IN, Pin.PULL_DOWN)
    for number in (board.SDA_Pin, board.SCL_Pin):
        Pin(number, Pin.IN, Pin.PULL_UP)
    for number in (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin):
        Pin(number, Pin.IN, Pin.PULL_UP)
    if not keep_flash:
        Pin(board.Power_Enable_Pin, Pin.OUT, value=0)
        Pin(board.Power_Enable_Pin, Pin.IN, Pin.PULL_DOWN)


def close_usb_cdc():
    """Return True only when the firmware exposes an actual USB disable API."""
    import machine
    if hasattr(machine, "USBDevice"):
        machine.USBDevice().active(False)
        return True
    try:
        import pyb
    except ImportError:
        return False
    if hasattr(pyb, "usb_mode"):
        pyb.usb_mode(None)
        return True
    return False


def light_sleep_ms(duration):
    """Timed idle sleep without assuming unsupported nRF lightsleep()."""
    import machine
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < duration:
        if hasattr(machine, "idle"):
            machine.idle()
        time.sleep_ms(10)
