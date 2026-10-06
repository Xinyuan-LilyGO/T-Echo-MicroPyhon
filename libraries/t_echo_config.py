"""T-Echo pins: raw nRF numbers, P0.n=n and P1.n=32+n.

Source: T-Echo/examples/Factory/utilities.h and Sleep_Display/pin_config.h.
Requires a T-Echo compatible MicroPython board build (see README_CN.md).
"""

import time
import os
import sys
from machine import Pin, SPI, I2C, UART, ADC, mem32

ePaper_Miso = SCREEN_MISO = 38
ePaper_Mosi = SCREEN_MOSI = 29
ePaper_Sclk = SCREEN_SCLK = 31
ePaper_Cs = SCREEN_CS = 30
ePaper_Dc = SCREEN_DC = 28
ePaper_Rst = SCREEN_RST = 2
ePaper_Busy = SCREEN_BUSY = 3
ePaper_Backlight = SCREEN_BL = 43
SCREEN_WIDTH = SCREEN_HEIGHT = 200
LoRa_Miso = SX1262_MISO = 23
LoRa_Mosi = SX1262_MOSI = 22
LoRa_Sclk = SX1262_SCLK = 19
LoRa_Cs = SX1262_CS = 24
LoRa_Rst = SX1262_RST = 25
LoRa_Dio1 = SX1262_DIO1 = 20
LoRa_Busy = SX1262_BUSY = 17
Flash_Cs = 47
Flash_Miso = 45
Flash_Mosi = 44
Flash_Sclk = 46
Flash_WP = 7
Flash_HOLD = 5
Touch_Pin = TTP223_TOUCH = 11
Adc_Pin = BATTERY_VOLTAGE_ADC = 4
SDA_Pin = IIC_SDA = 26
SCL_Pin = IIC_SCL = 27
RTC_Int_Pin = RTC_INT = 16
Gps_Rx_Pin = GPS_UART_RX = 41
Gps_Tx_Pin = GPS_UART_TX = 40
Gps_Wakeup_Pin = GPS_WAKEUP = 34
Gps_Reset_Pin = GPS_RST = 37
Gps_pps_Pin = GPS_1PPS = 36
UserButton_Pin = nRF52840_BOOT = 42
Power_Enable_Pin = VDD3_3_EN = 12
Power_Enable1_Pin = 13
GreenLed_Pin = LED_1 = 33
RedLed_Pin = LED_2 = 35
BlueLed_Pin = LED_3 = 14
DRV2605_ENABLE_PIN = 8
BUZZER_PIN = 6
A7682_RXD = 6
A7682_TXD = 8
A7682_RI = 39
A7682_DTR = 38
A7682_PWR = 15

# read_u16() describes its range; never infer ADC resolution from its value.
# Upstream nRF machine.ADC uses VDD/4 reference and 1/4 gain: full scale
# is the measured MCU supply, nominally 3.3 V, not Arduino's internal 3.0 V.
ADC_FULL_SCALE_V = 3.3
BATTERY_DIVIDER = 2.0
ADC_NATIVE_MAX = 255
GPS_UART_ID = 0
MODEM_UART_ID = 0
EXTERNAL_FLASH_MOUNT = "/flash"


def power_on(reset=False):
    enable = Pin(Power_Enable_Pin, Pin.OUT, value=1)
    if reset:
        enable.off()
        time.sleep_ms(100)
        enable.on()
    time.sleep_ms(10)
    return enable


def leds_off():
    for number in (GreenLed_Pin, RedLed_Pin, BlueLed_Pin):
        Pin(number, Pin.OUT, value=1)


def shared_i2c(freq=400000):
    return I2C(0, scl=Pin(SCL_Pin), sda=Pin(SDA_Pin), freq=freq)


def spi_bus(bus_id, sck, mosi, miso, baudrate=8000000):
    return SPI(bus_id, baudrate=baudrate, polarity=0, phase=0,
               sck=Pin(sck), mosi=Pin(mosi), miso=Pin(miso))


def display_spi():
    # Do not drive the unconnected ePaper_Miso: it is the modem's DTR pin.
    # Half-duplex panel SPI can use its MOSI pin as the unused RX selection.
    return spi_bus(1, SCREEN_SCLK, SCREEN_MOSI, SCREEN_MOSI)


def radio_spi():
    return spi_bus(2, SX1262_SCLK, SX1262_MOSI, SX1262_MISO)


def external_flash_mounted():
    """Treat /flash as firmware-owned; never take over its pins from Python.

    Older nRF builds do not expose the VFS mount table. Conservatively keep
    flash powered whenever the firmware's external mount path exists.
    """
    try:
        os.stat(EXTERNAL_FLASH_MOUNT)
    except OSError as error:
        if error.args and error.args[0] == 2:
            return False
        raise
    return True


def flash_spi():
    if external_flash_mounted():
        raise OSError("External flash is in use at /flash; raw SPI access is disabled")
    # Keep SPIM3 reserved for firmware/filesystem use. Some nRF builds omit
    # machine.SoftSPI, so provide the same mode-0 transfer API using GPIO.
    try:
        from machine import SoftSPI
    except ImportError:
        from soft_spi import SoftSPI
    Pin(Flash_WP, Pin.OUT, value=1)
    Pin(Flash_HOLD, Pin.OUT, value=1)
    return SoftSPI(baudrate=1000000, polarity=0, phase=0,
                   sck=Pin(Flash_Sclk), mosi=Pin(Flash_Mosi), miso=Pin(Flash_Miso))


def _uart(uart_id, baudrate, tx, rx):
    try:
        return UART(uart_id, baudrate, tx=Pin(tx), rx=Pin(rx),
                    timeout=0, timeout_char=2)
    except TypeError:
        # Stock nRF firmware exposes only UART0 with build-time pins. Keep
        # its asynchronous driver and RX buffer; do not substitute bit-banging.
        uart = UART(uart_id, baudrate, timeout=0, timeout_char=2)
        try:
            _legacy_uart_pins(uart_id, tx, rx)
        except BaseException:
            uart.deinit()
            raise
        return uart


def _legacy_uart_pins(uart_id, tx, rx):
    # Check the port before accessing nRF-specific peripheral addresses.
    if sys.platform != "nrf" or uart_id != 0:
        raise RuntimeError("Legacy UART pin selection supports nRF UART0 only")
    if mem32[0x10000100] != 0x52840:  # FICR.INFO.PART
        raise RuntimeError("Legacy UART pin selection needs an nRF52840")
    base = 0x40002000
    mode = mem32[base + 0x500]
    if mode not in (4, 8):
        raise RuntimeError("UART peripheral unavailable; check UART0")
    if (mem32[base + 0x50C] == tx and mem32[base + 0x514] == rx and
            not mem32[base + 0x56C] & 1):
        return

    # This path runs immediately after UART() initialises its one-byte RX
    # transfer. Reject other DMA layouts instead of altering unknown drivers.
    if mode == 8 and mem32[base + 0x538] != 1:  # RXD.MAXCNT
        raise RuntimeError("Legacy UART has an unsupported RX DMA layout")
    if mem32[base + 0x200]:  # SHORTS
        raise RuntimeError("Legacy UART has active hardware shortcuts")

    interrupts = mem32[base + 0x304]  # INTENSET is also readable.
    mem32[base + 0x308] = 0xFFFFFFFF  # Mask this peripheral, not the SoftDevice.
    # STOPRX generates RXTO/ENDRX. Letting nrfx see those events while PSEL
    # changes aborts/rearms its one-byte transfer at the wrong time. Keep its
    # software state and RXD.PTR/MAXCNT intact, and consume only these events.
    mem32[base + 0x144] = 0  # EVENTS_RXTO
    mem32[base + 0x004] = 1  # TASKS_STOPRX
    started = time.ticks_ms()
    while not mem32[base + 0x144]:
        if time.ticks_diff(time.ticks_ms(), started) > 100:
            raise OSError("UART receiver did not stop before pin selection")
        time.sleep_ms(1)
    # No TX has been started since UART() constructed the driver; STOPTX
    # would needlessly introduce a TX completion event into its state machine.
    mem32[base + 0x500] = 0
    Pin(tx, Pin.OUT, value=1)
    Pin(rx, Pin.IN, Pin.PULL_UP)
    mem32[base + 0x50C] = tx
    mem32[base + 0x514] = rx
    mem32[base + 0x508] = 0xFFFFFFFF  # PSEL.RTS
    mem32[base + 0x510] = 0xFFFFFFFF  # PSEL.CTS
    mem32[base + 0x56C] &= ~1  # CONFIG.HWFC
    for offset in (0x108, 0x124, 0x144):  # RXDRDY, ERROR, RXTO
        mem32[base + offset] = 0
    mem32[base + 0x480] = mem32[base + 0x480]  # ERRORSRC is write-one-to-clear.
    if mode == 8:
        mem32[base + 0x110] = 0  # ENDRX
        mem32[base + 0x14C] = 0  # RXSTARTED
    mem32[base + 0x500] = mode
    mem32[base + 0x000] = 1  # STARTRX reuses the driver's RX buffer.
    mem32[base + 0x304] = interrupts


def gps_uart(baudrate=9600):
    return _uart(GPS_UART_ID, baudrate, GPS_UART_TX, GPS_UART_RX)


def modem_uart(baudrate=115200):
    # A7682 names are module-side; Arduino setPins(A7682_TXD,A7682_RXD)
    # passes MCU RX first, TX second.
    return _uart(MODEM_UART_ID, baudrate, A7682_RXD, A7682_TXD)


def battery_read(samples=5):
    adc = ADC(Pin(Adc_Pin))
    if hasattr(adc, "read_u16"):
        read, maximum = adc.read_u16, 65535
    else:
        read = adc.read if hasattr(adc, "read") else adc.value
        maximum = ADC_NATIVE_MAX
    read()
    readings = []
    for _ in range(max(1, samples)):
        readings.append(read())
        time.sleep_ms(5)
    readings.sort()
    voltage = readings[len(readings) // 2] * ADC_FULL_SCALE_V / maximum
    return int(voltage / 3.0 * 4096), voltage, voltage * BATTERY_DIVIDER


def battery_voltage():
    return battery_read()[2]


def unique_device_id():
    return ((mem32[0x10000060] & 0xFFFFFFFF).to_bytes(4, "big") +
            (mem32[0x10000064] & 0xFFFFFFFF).to_bytes(4, "big"))


def system_off_on_button(touch=False):
    leds_off()
    Pin(SCREEN_BL, Pin.OUT, value=0)
    # Disable old sense sources before arming active-low button P1.10.
    for number in (UserButton_Pin, Touch_Pin, RTC_Int_Pin):
        port, bit = divmod(number, 32)
        address = 0x50000700 + port * 0x300 + bit * 4
        mem32[address] &= ~(3 << 16)
    Pin(UserButton_Pin, Pin.IN, Pin.PULL_UP)
    while not Pin(UserButton_Pin).value():
        time.sleep_ms(10)
    mem32[0x50000A00 + 10 * 4] = (3 << 16) | (3 << 2)
    if touch:
        # TTP223 is active high, unlike the mechanical button.
        Pin(Touch_Pin, Pin.IN)
        mem32[0x50000700 + Touch_Pin * 4] = 2 << 16
    from nrf_power import system_off
    system_off()
