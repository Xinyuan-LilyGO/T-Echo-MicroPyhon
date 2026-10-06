"""Factory.ino and its display/gps/snesor/radio/flash/bleApp/pdm.cpp helpers.

Original: Lewis He / LILYGO, 2020-2025. All application logic is in this file.
The nRF internal filesystem is mounted at /internal by the board firmware.
USB MSC and Arduino BSP/bootloader metadata need firmware interfaces.
"""
import sys

_file = globals().get("__file__", "").replace("\\", "/")
_here = _file.rsplit("/", 1)[0] if "/" in _file else "."
for _path in (_here + "/../../libraries", "/flash/libraries", "/libraries", "/lib", "libraries"):
    if _path not in sys.path:
        sys.path.append(_path)

import os
import time
import gc
import binascii
import machine
from machine import Pin, mem32
import t_echo_config as board
gc.collect()
from ssd1681 import SSD1681
gc.collect()
from gfx_display import GFXDisplay
gc.collect()
from font_freemono9 import FONT as FreeMono9pt7b
from font_freemono12 import FONT as FreeMono12pt7b
from font_factory_status24 import FONT as FreeMonoBold24pt7b
from font_freepuhuiti7 import FONT as FreePuhuiti7pt7b
from nmea import NMEA

LORA_FREQ = 920.0
LORA_BW = 125.0
LORA_TX_POWER = 22
LORA_SF = 9
RADIO_TYPE = "SX1262"
DEFAULT_FONT = FreeMono9pt7b
DEFAULT_FONT_HEIGHT = FreeMono9pt7b[4]
GxEPD_WHITE, GxEPD_BLACK = 1, 0
PPS_LED = board.GreenLed_Pin
SENDER_LED = board.RedLed_Pin
RECV_LED = board.BlueLed_Pin
FAILED_LED = 0xFF
CONNECT_LED = board.BlueLed_Pin + 1
DISCONNECT_LED = board.BlueLed_Pin + 2
WHITE_LED = board.BlueLed_Pin + 3
OFF_LED = 0x7C
DEBUG_MODE = False

display = dispPort = rfPort = Wire = SerialGPS = None
radio = rtc = bme = flash = IMU = IMU_9250 = bhy = drv = pdm = None
backlight = ble = button = touch = rtc_irq = None
gpsVersion = BLE_NAME = ""
funcSelectIndex = 0
prevFuncSelectIndex = 0xFF
index_max = 7
devices_probe_mask = 0
gotoSleep = sleepIn = False
bri_duty = 15
rtcInterrupt = isRtcOnline = isRadioOnline = False
gps_interval = sensor_interval = radio_interval = pdm_interval = 0
prevCharsProcessed = gps_start_ms = gps_use_second = 0
syncDateTime = update_use_second = False
radio_sender_interval = 3000
txCounter = 0
startListing = True
transmittedFlag = False
transmissionState = 0
_receiver_started = _tx_pending = False
pdm_avg = 0
sampleBuffer = None
_led_mode = _led_previous = 0
ledState = 0
lastDebounceTime = _led_due = _button_due = 0
_tasks_running = False
_touch_pending = _pending_clicks = 0
_rendering = False
_firmware_flash = None
_flash_mounted = _flash_unmounted = _flash_asleep = False
_flash_verified = False
_flash_size = 0
_cwd = _vfs = _enter_off = None


class FactoryGPS(NMEA):
    """TinyGPS++ update flags, which its field accessors consume."""
    def __init__(self):
        super().__init__()
        self.time_updated = self.location_updated = self.satellites_updated = False

    def _parse(self, line):
        accepted = super()._parse(line)
        if accepted:
            kind = line[3:6]
            fields = line.split(b"*", 1)[0].split(b",")
            if kind in (b"GGA", b"RMC"):
                if len(fields[1]) >= 6 and self.utc is not None:
                    self.time_updated = True
                if ((kind == b"GGA" and fields[6] not in (b"", b"0")) or
                        (kind == b"RMC" and fields[2] == b"A")):
                    self.location_updated = self.valid
            if kind == b"GGA":
                self.satellites_updated = self.satellites is not None
        return accepted


gps = FactoryGPS()


class FactoryButton:
    """AceButton defaults: debounce 20 ms, click <200 ms, long press 1000 ms."""
    def __init__(self, pin):
        self.pin = Pin(pin, Pin.IN, Pin.PULL_UP)
        self.raw = self.stable = self.pin.value()
        self.changed = time.ticks_ms()
        self.pressed = None
        self.long_sent = False

    def check(self):
        now = time.ticks_ms()
        value = self.pin.value()
        if value != self.raw:
            self.raw, self.changed = value, now
        if value != self.stable and time.ticks_diff(now, self.changed) >= 20:
            self.stable = value
            if not value:
                self.pressed, self.long_sent = now, False
            else:
                elapsed = time.ticks_diff(now, self.pressed) if self.pressed is not None else 200
                self.pressed = None
                if elapsed < 200:
                    handleEvent("clicked")
        if not self.stable and self.pressed is not None and not self.long_sent:
            if time.ticks_diff(now, self.pressed) >= 1000:
                self.long_sent = True
                handleEvent("long_pressed")


def _probe(name, function, bit):
    global devices_probe_mask
    gc.collect()
    try:
        result = bool(function())
    except (OSError, RuntimeError) as error:
        print(name + ":", error)
        result = False
    finally:
        gc.collect()
    print(name + ": " + ("OK" if result else "FAIL"))
    if result:
        devices_probe_mask |= 1 << bit
    return result


def _title(text, baseline=None):
    bx, by, width, height = display.getTextBounds(text, 0, 0)
    display.setCursor((display.width() - width) // 2 - bx,
                      DEFAULT_FONT_HEIGHT if baseline is None else baseline)
    display.print(text)


def _header(title):
    display.setFullWindow()
    display.firstPage()
    display.fillScreen(GxEPD_WHITE)
    display.setFont(DEFAULT_FONT)
    display.setTextColor(GxEPD_BLACK)
    _title(title)
    display.drawFastHLine(0, 30, display.width(), GxEPD_BLACK)


def _row(index, label, value):
    display.setCursor(5, 42 + 24 * index)
    display.print(label)
    x, y = display.getCursorX() + 5, display.getCursorY()
    print("i:%d X:%d Y:%d" % (index - 1, x, y))
    display.setCursor(x, y)
    display.print(value)


def _finish_page():
    global _rendering
    _rendering = True
    try:
        display.nextPage()
    finally:
        _rendering = False


def drawBox(x, y, width, height, color):
    display.fillRect(x, y + 2, width, height, color)


def _partial(rectangle, value):
    x, y, width, height = rectangle
    display.setFont(DEFAULT_FONT)
    display.setTextColor(GxEPD_BLACK)
    display.setPartialWindow(x, y, width, height)
    display.firstPage()
    drawBox(x, y, width, height, GxEPD_WHITE)
    display.setCursor(x, y + DEFAULT_FONT_HEIGHT)
    display.print(value)
    _finish_page()


def _build_date():
    version = os.uname().version
    if " on " in version:
        date = version.split(" on ")[-1][:10].split("-")
        if len(date) == 3 and all(piece.isdigit() for piece in date):
            months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
            month = int(date[1])
            if 1 <= month <= 12:
                return "%s %2d %s" % (months[month - 1], int(date[2]), date[0])
    return "N/A"


def setupDisplay():
    global display, dispPort, backlight
    from nrf_pwm import Backlight
    gc.collect()
    backlight = Backlight(board.ePaper_Backlight, 51, countertop=255)
    dispPort = board.display_spi()
    dispPort.init(baudrate=4000000, polarity=0, phase=0)
    gc.collect()
    panel = SSD1681(dispPort, board.ePaper_Cs, board.ePaper_Dc,
                    board.ePaper_Rst, board.ePaper_Busy, rotation=3, low_memory=True)
    panel.begin()
    panel.idle_callback = _background
    display = GFXDisplay(panel)
    display.setRotation(3)
    display.setTextColor(GxEPD_BLACK)
    display.setFont(FreeMonoBold24pt7b)
    display.setFullWindow()
    display.firstPage()
    display.fillScreen(GxEPD_WHITE)
    _title("BOOTING", display.height() // 2)
    display.setFont(DEFAULT_FONT)
    _title(_build_date(), display.height() // 2 + 30)
    _finish_page()
    time.sleep_ms(3000)


def adjustBacklight():
    global bri_duty
    # bri_duty is uint8_t in display.cpp; overflow occurs before %=255.
    bri_duty = ((bri_duty + 51) & 0xFF) % 255
    backlight.set(bri_duty)


def _tone():
    buzzer = Pin(board.BUZZER_PIN, Pin.OUT, value=0)
    for _ in range(200):
        buzzer.on()
        time.sleep_us(500)
        buzzer.off()
        time.sleep_us(500)


def motor_shield_tone():
    if devices_probe_mask & (1 << 10):
        drv.play(75)
        _tone()


def handleEvent(event):
    global gotoSleep, sleepIn, _pending_clicks
    if event == "long_pressed":
        print("Enter sleep mode!")
        gotoSleep = True
    elif event == "clicked":
        sleepIn = False
        _pending_clicks += 1


def _touch_irq(pin):
    global _touch_pending
    _touch_pending += 1


def led_task():
    global _led_previous, _led_due, ledState, lastDebounceTime
    now = time.ticks_ms()
    if _led_mode != _led_previous and _led_mode:
        board.leds_off()
        _led_previous = _led_mode
    if time.ticks_diff(now, _led_due) < 0:
        return
    _led_due = time.ticks_add(now, 30)
    if _led_previous == PPS_LED:
        if Pin(board.Gps_pps_Pin).value() and time.ticks_diff(now, lastDebounceTime) > 50:
            ledState ^= 1
            Pin(PPS_LED, Pin.OUT, value=0 if ledState else 1)
            lastDebounceTime = now
    elif _led_previous in (SENDER_LED, RECV_LED):
        led = Pin(_led_previous, Pin.OUT)
        led.value(1 - led.value())
        _led_due = time.ticks_add(now, 100)
    elif _led_previous == FAILED_LED:
        Pin(board.RedLed_Pin, Pin.OUT, value=0)
        Pin(board.BlueLed_Pin, Pin.OUT, value=0)
        _led_due = time.ticks_add(now, 200)
    elif _led_previous in (CONNECT_LED, DISCONNECT_LED):
        Pin(board.BlueLed_Pin, Pin.OUT, value=0 if _led_previous == CONNECT_LED else 1)
        _led_due = time.ticks_add(now, 100)
    elif _led_previous == WHITE_LED:
        for pin in (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin):
            Pin(pin, Pin.OUT, value=0)
        _led_due = time.ticks_add(now, 100)


def _background():
    global _button_due, _touch_pending
    if not _tasks_running:
        return
    now = time.ticks_ms()
    if time.ticks_diff(now, _button_due) >= 0:
        button.check()
        _button_due = time.ticks_add(now, 10)
    led_task()
    if ble is not None:
        ble.poll()
    if funcSelectIndex == 2 and SerialGPS is not None:
        # The nRF MicroPython UART ring is 64 bytes; keep draining it while
        # GFX and the display BUSY wait yield to this background callback.
        data = SerialGPS.read()
        if data:
            gps.feed(data)
    while _touch_pending:
        _touch_pending -= 1
        if devices_probe_mask & (1 << 10):
            _tone()
        adjustBacklight()


def gnss_probe():
    global SerialGPS, gpsVersion
    for rate in (9600, 19200, 38400, 57600, 115200, 4800):
        print("Trying baud rate %u" % rate)
        SerialGPS.deinit()
        time.sleep_ms(10)
        SerialGPS = board.gps_uart(rate)
        time.sleep_ms(50)
        start = time.ticks_ms()
        line = bytearray()
        while time.ticks_diff(time.ticks_ms(), start) < 2000:
            data = SerialGPS.read()
            if data:
                for value in data:
                    if value == 10:
                        text = bytes(line).strip()
                        line = bytearray()
                        if text.startswith(b"$"):
                            print("GPS responded at rate %u: %s" % (rate, text[:10]))
                            if rate != 9600:
                                print("GPS Baud set to 9600")
                                for _ in range(3):
                                    SerialGPS.write(b"$PCAS01,1*1D\r\n")
                                    time.sleep_ms(100)
                                SerialGPS.deinit()
                                time.sleep_ms(10)
                                SerialGPS = board.gps_uart(9600)
                                SerialGPS.write(b"$PCAS10,3*1F\r\n")
                            gpsVersion = "L76K (9600)"
                            return True
                    elif len(line) < 160:
                        line.append(value)
            time.sleep_ms(10)
        print("No response at %u baud" % rate)
    SerialGPS.deinit()
    time.sleep_ms(10)
    SerialGPS = board.gps_uart(9600)
    print("AutoBaud failed, default to 9600")
    return False


def gps_probe():
    global gps_start_ms
    if not gnss_probe():
        return False
    SerialGPS.write(b"$PCAS04,5*1C\r\n")
    time.sleep_ms(250)
    SerialGPS.write(b"$PCAS03,1,0,0,0,1,0,0,0,0,0,,,0,0*02\r\n")
    time.sleep_ms(250)
    SerialGPS.write(b"$PCAS11,3*1E\r\n")
    gps_start_ms = time.ticks_ms()
    return True


def setupGPS():
    global SerialGPS, gps
    print("[GPS] Initializing ...")
    SerialGPS = board.gps_uart(9600)
    Pin(board.Gps_pps_Pin, Pin.IN)
    Pin(board.Gps_Wakeup_Pin, Pin.OUT, value=1)
    time.sleep_ms(10)
    reset = Pin(board.Gps_Reset_Pin, Pin.OUT, value=1)
    time.sleep_ms(10)
    reset.off()
    time.sleep_ms(10)
    reset.on()
    gps = FactoryGPS()
    return gps_probe()


def sleepGPS():
    if SerialGPS is not None:
        SerialGPS.deinit()
    Pin(board.Gps_Wakeup_Pin, Pin.OUT, value=0)
    for pin in (board.Gps_Reset_Pin, board.Gps_pps_Pin, board.Gps_Rx_Pin, board.Gps_Tx_Pin):
        Pin(pin, Pin.IN)


GPS_BOXES = ((43, 48, 100, 24), (65, 72, 100, 24), (65, 96, 100, 24),
             (54, 120, 120, 24), (54, 144, 100, 24), (131, 168, 100, 24))


def _gps_updated(index):
    date_valid = (gps.date is not None and gps.date[0] >= 2024
                  and 1 <= gps.date[1] <= 12 and gps.date[2] != 0)
    if index == 0:
        return True
    if index in (1, 2):
        return gps.time_updated and date_valid and (index == 1 or gps.utc is not None)
    if index == 3:
        return gps.valid and gps.location_updated
    if index == 4:
        return gps.valid
    return gps.satellites is not None and gps.satellites_updated


def _gps_value(index, updating=False):
    if index == 0:
        if updating and gps.chars < 10:
            return "No DATA"
        return str(gps.chars) + ("/%dS" % gps_use_second if updating and update_use_second else "")
    if index == 1:
        return "%d/%d/%d" % gps.date
    if index == 2:
        gps.time_updated = False
        return "%d:%d:%d" % gps.utc
    if index == 3:
        gps.location_updated = False
        return "%.6f" % gps.longitude
    if index == 4:
        return "%.6f" % gps.latitude
    gps.satellites_updated = False
    return str(gps.satellites)


def drawGPS():
    global _led_mode
    print("drawGPS")
    _header("GPS")
    for index, label in enumerate(("RX:", "DATE:", "TIME:", "LNG:", "LAT:", "SATELLITES:")):
        _row(index + 1, label, _gps_value(index) if _gps_updated(index) else "N.A")
    _finish_page()
    _led_mode = PPS_LED


def loopGPS():
    global gps_interval, prevCharsProcessed, update_use_second, gps_use_second, syncDateTime
    while SerialGPS.any():
        data = SerialGPS.read()
        if data:
            gps.feed(data)
    if gps.valid and gps.date and gps.date[0] > 2000 and not update_use_second:
        update_use_second = True
        gps_use_second = time.ticks_diff(time.ticks_ms(), gps_start_ms) // 1000
    if time.ticks_diff(time.ticks_ms(), gps_interval) <= 5000:
        return
    if gps.chars != prevCharsProcessed:
        prevCharsProcessed = gps.chars
        for index, rectangle in enumerate(GPS_BOXES):
            if _gps_updated(index):
                value = _gps_value(index, True)
                if index == 2 and not syncDateTime and isRtcOnline:
                    rtc.set_datetime(*(gps.date + gps.utc))
                    syncDateTime = True
                    print("SYNC GPS DATE TIME")
                _partial(rectangle, value)
    if gps.chars < 10:
        print("WARNING: No GPS data.  Check wiring.")
    gps_interval = time.ticks_ms()


def setupSensor():
    global bme
    from bme280 import BME280
    gc.collect()
    print("[BME280 ] Initializing ...")
    sensor = BME280(Wire, address=0x77)
    if not sensor.has_humidity:
        sensor.sleep()
        print("BME280 chip not found")
        return False
    bme = sensor
    print("success")
    return True


def rtcInterruptCb(pin):
    global rtcInterrupt
    rtcInterrupt = True


def setupRTC():
    global rtc, rtc_irq, isRtcOnline
    from pcf8563 import PCF8563
    gc.collect()
    print("[PCF8563] Initializing ...")
    rtc_irq = Pin(board.RTC_Int_Pin, Pin.IN)
    rtc_irq.irq(handler=rtcInterruptCb, trigger=Pin.IRQ_FALLING)
    rtc = PCF8563(Wire)
    isRtcOnline = True
    print("success")
    return True


def readVBAT():
    return board.battery_read()[2] * 1000.0


def _sensor_values():
    values = ["N/A", "N/A", "N/A", "N/A", "N/A", ""]
    if bme is not None:
        temperature, pressure, humidity = bme.read()
        values[:3] = ("%.2f*C" % temperature, "%.2f%%" % humidity, "%.2fhPa" % pressure)
    stamp = rtc.datetime() if isRtcOnline else None
    if stamp is not None:
        values[3] = "%04d/%02d/%02d" % stamp[:3]
        values[4] = "%02d:%02d:%02d" % stamp[3:]
    millivolts = readVBAT()
    values[5] = "USB powered" if millivolts > 4200 else "%.2fV" % (millivolts / 1000.0)
    return values


def drawSensor():
    print("drawSensor")
    values = _sensor_values()
    _header("Sensor")
    for index, label in enumerate(("TEMP:", "HR:", "PR:", "DATE:", "TIME:", "VBAT:")):
        _row(index + 1, label, values[index])
    _finish_page()


SENSOR_BOXES = ((65, 48, 100, 24), (43, 72, 100, 24), (43, 96, 100, 24),
                (65, 120, 120, 24), (65, 144, 100, 24), (65, 168, 130, 24))


def loopSensor():
    global sensor_interval
    if time.ticks_diff(time.ticks_ms(), sensor_interval) < 1000:
        return
    values = _sensor_values()
    for index, rectangle in enumerate(SENSOR_BOXES):
        if index >= 3 or bme is not None:
            _partial(rectangle, values[index])
    sensor_interval = time.ticks_ms()


def sleepSensor():
    if bme is not None:
        bme.sleep()


def setupLoRa():
    global radio, rfPort, isRadioOnline
    from sx1262 import SX1262
    gc.collect()
    rfPort = board.radio_spi()
    radio = SX1262(rfPort, board.LoRa_Cs, board.LoRa_Busy, board.LoRa_Rst, board.LoRa_Dio1)
    print("[SX1262] Initializing ...")
    radio.begin(frequency=LORA_FREQ, bandwidth=LORA_BW, spreading_factor=LORA_SF,
                coding_rate=7, sync_word=0x12, power=LORA_TX_POWER, preamble=8, crc=True)
    radio.set_current_limit(140)
    isRadioOnline = True
    print(" success")
    return True


def _radio_values(receiving=False):
    return ["%.2fMHz" % LORA_FREQ, "%.2fKHz" % LORA_BW,
            "%ddBm" % LORA_TX_POWER, "N/A"] + (["N/A", ""] if receiving else ["%dms" % radio_sender_interval])


def _radio_page(title, labels, receiving):
    global _led_mode
    _header(title)
    if isRadioOnline:
        for index, value in enumerate(_radio_values(receiving)):
            _row(index + 1, labels[index], value)
        _led_mode = RECV_LED if receiving else SENDER_LED
    else:
        display.setFont(FreeMonoBold24pt7b)
        _title("FAILED", FreeMonoBold24pt7b[4] + 45)
        _led_mode = FAILED_LED
    _finish_page()


def drawSender():
    print("drawSender")
    _radio_page("LoRa Sender", ("FREQ:", "BW:", "PW:", "DATA:", "IAT:"), False)


def drawReceiver():
    print("drawReceiver")
    _radio_page("LoRa Receiver", ("FREQ:", "BW:", "PW:", "DATA:", "RSSI:", "SNR:"), True)


def drawRadioSender(payload):
    _partial((65, 120, 120, 24), payload)


def drawRadioReceiver(payload):
    _partial((65, 120, 120, 24), payload)
    _partial((65, 144, 100, 24), "%.2fdBm" % radio.last_rssi)
    _partial((54, 168, 100, 24), "%.2fdB" % radio.last_snr)


def loopSender():
    global radio_interval, txCounter, startListing, transmittedFlag
    global transmissionState, _receiver_started, _tx_pending
    if time.ticks_diff(time.ticks_ms(), radio_interval) < radio_sender_interval or not isRadioOnline:
        return
    payload = "echo:%d" % txCounter
    txCounter += 1
    if _tx_pending:
        irq = radio.irq_status()
        transmittedFlag = bool(irq & (radio.IRQ_TX_DONE | radio.IRQ_TIMEOUT))
        transmissionState = 0 if irq & radio.IRQ_TX_DONE else -5
    if startListing:
        startListing = False
        _receiver_started = False
        print("[SX1262] Sending first packet ...")
        radio.start_transmit(payload)
        _tx_pending = True
    if transmittedFlag:
        transmittedFlag = False
        if transmissionState == 0:
            print("transmission finished!")
            # The C++ sketch displays the next counter before sending it.
            drawRadioSender(payload)
        else:
            print("failed, code", transmissionState)
        radio.finish_transmit()
        print("[SX1262] Sending another packet ...")
        radio.start_transmit(payload)
        _tx_pending = True
    radio_interval = time.ticks_ms()


def loopReceiver():
    global startListing, transmittedFlag, _receiver_started, _tx_pending
    if not isRadioOnline:
        return
    # Also start RX when Sender was skipped before its first 3-second tick.
    if not startListing or not _receiver_started:
        startListing = True
        _receiver_started = True
        _tx_pending = False
        radio.start_receive()
        print("success!")
    if radio.poll():
        payload = radio.read()
        if payload is not None:
            try:
                text = payload.decode()
            except UnicodeError:
                text = binascii.hexlify(payload).decode()
            print("[SX1262] Received packet!")
            print("[SX1262] Data:\t\t", text)
            print("[SX1262] RSSI:\t\t", radio.last_rssi, " dBm")
            print("[SX1262] SNR:\t\t", radio.last_snr, " dB")
            print("[SX1262] Frequency error:\tN/A (not exposed by SX1262)")
            drawRadioReceiver(text)
        else:
            print("CRC error or receive timeout!")
        radio.start_receive()
        transmittedFlag = False


def sleepLoRa():
    if isRadioOnline:
        radio.sleep()
    if rfPort is not None:
        rfPort.deinit()


def beginPDM():
    global pdm, sampleBuffer
    from nrf_pdm import NrfPDM
    gc.collect()
    sampleBuffer = bytearray(512)
    Pin(15, Pin.OUT, value=0)
    sensor = NrfPDM(data=6, clock=8, select=15, gain=30, sample_rate=16000)
    # Python DMA captures blocks instead of keeping the Arduino C IRQ ring.
    sensor.readinto(sampleBuffer)
    pdm = sensor
    return True


def drawPDM():
    _header("PDM Data")
    _row(1, "DATA:", pdm_avg)
    _finish_page()


def loopPDM():
    global pdm_avg, pdm_interval
    if pdm is not None:
        import struct
        count = pdm.readinto(sampleBuffer)
        total = 0
        for offset in range(0, count, 2):
            total += struct.unpack_from("<h", sampleBuffer, offset)[0]
        pdm_avg = int(total / (count // 2)) if count else 0
        print("avg:%d" % pdm_avg)
    if time.ticks_diff(time.ticks_ms(), pdm_interval) < 500:
        return
    _partial((65, 48, 100, 24), pdm_avg)
    pdm_interval = time.ticks_ms()


def connect_callback(handle):
    global _led_mode
    print("Connected to", handle)
    _led_mode = CONNECT_LED


def disconnect_callback(handle, reason):
    global _led_mode
    print("\nDisconnected, reason = 0x%x" % reason)
    _led_mode = DISCONNECT_LED


def _factory_ble_name(mac):
    return "T-Echo-" + str(mac[0]) + str(mac[1])


class FactoryBatteryService:
    """Only BAS is started by bleApp.cpp; NUS, DIS and DFU stay disabled."""
    def __init__(self):
        global BLE_NAME
        self.connected = False
        self.closing = False
        self.connection = None
        self.fast = True
        self.started = 0
        try:
            import bluetooth
        except ImportError:
            bluetooth = None
        self.bluetooth = bluetooth
        if bluetooth is not None:
            self.stack = bluetooth.BLE()
            self.stack.active(True)
            uuid = bluetooth.UUID
            ((self.battery,),) = self.stack.gatts_register_services(((uuid(0x180F),
                ((uuid(0x2A19), bluetooth.FLAG_READ | bluetooth.FLAG_NOTIFY),)),))
            self.stack.gatts_write(self.battery, b"\x00")
            mac = bytes(self.stack.config("mac")[1])
            BLE_NAME = _factory_ble_name(mac)
            self.stack.irq(self._bluetooth_event)
            try:
                self.stack.config(txpower=4)
            except (ValueError, TypeError):
                print("BLE TX power is fixed by this firmware")
        else:
            try:
                from ubluepy import Service, Characteristic, UUID, Peripheral, constants
            except ImportError:
                raise RuntimeError("Factory BLE BAS needs bluetooth or ubluepy firmware")
            self.constants = constants
            self.service = Service(UUID(0x180F))
            self.battery = Characteristic(UUID(0x2A19), props=Characteristic.PROP_READ)
            self.service.addCharacteristic(self.battery)
            self.stack = Peripheral()
            self.stack.addService(self.service)
            self.stack.setConnectionHandler(self._ubluepy_event)
            mac = ((mem32[0x100000A4] & 0xFFFFFFFF).to_bytes(4, "little") +
                   (mem32[0x100000A8] & 0xFFFF).to_bytes(2, "little"))
            BLE_NAME = _factory_ble_name(mac)
            print("ubluepy: advertising interval/TX power/peer name are firmware-owned")
            print("ubluepy: BAS is readable; this API cannot initialize a NOTIFY value before connection")

    def _bluetooth_event(self, event, data):
        if event == 1:
            self.connected = True
            self.connection = data[0]
            connect_callback(data[0])
        elif event == 2:
            self.connected = False
            self.connection = None
            disconnect_callback(data[0], 0)
            if not self.closing:
                self.start()

    def _ubluepy_event(self, event, handle, data):
        if event == self.constants.EVT_GAP_CONNECTED:
            self.connected = True
            self.connection = handle
            connect_callback(handle)
        elif event == self.constants.EVT_GAP_DISCONNECTED:
            self.connected = False
            self.connection = None
            disconnect_callback(handle, data[0] if data else 0)
            if not self.closing:
                self.start()

    def _advertise(self, interval):
        if self.bluetooth is not None:
            # General discoverable LE-only, TX power +4 dBm, Battery service.
            advertisement = b"\x02\x01\x06\x02\x0a\x04\x03\x03\x0f\x18"
            name = BLE_NAME.encode()
            response = bytes((len(name) + 1, 9)) + name
            self.stack.gap_advertise(interval, adv_data=advertisement, resp_data=response)
        else:
            self.stack.advertise(device_name=BLE_NAME, services=[self.service])
            self.battery.write(bytearray((0,)))

    def start(self):
        self.fast = True
        self.started = time.ticks_ms()
        self._advertise(20000)

    def poll(self):
        if self.bluetooth is not None and self.fast and not self.connected:
            if time.ticks_diff(time.ticks_ms(), self.started) >= 30000:
                self.fast = False
                self._advertise(152500)

    def close(self):
        self.closing = True
        if self.bluetooth is not None:
            self.stack.gap_advertise(None)
            if self.connected:
                self.stack.gap_disconnect(self.connection)
            self.stack.active(False)
        else:
            self.stack.advertise_stop()
            if self.connected:
                self.stack.disconnect()


def setupBLE():
    global ble
    print("Init Bluefruit")
    time.sleep_ms(200)
    try:
        ble = FactoryBatteryService()
        ble.start()
    except (ImportError, RuntimeError, OSError) as error:
        print("BLE initialization failed:", error)
        ble = None


def listDir():
    directory = board.EXTERNAL_FLASH_MOUNT if _flash_mounted else "/"
    try:
        for entry in os.ilistdir(directory):
            name, kind = entry[:2]
            path = directory.rstrip("/") + "/" + name
            info = os.stat(path)
            stamp = time.localtime(info[8])
            print("%d %04d-%02d-%02d %02d:%02d:%02d %s%s" % (
                info[6], stamp[0], stamp[1], stamp[2], stamp[3], stamp[4], stamp[5],
                name, "/" if kind & 0x4000 else ""))
        print("Done!")
    except OSError as error:
        print("open root failed:", error)


def setupFlash():
    global flash, _firmware_flash, _flash_mounted, _flash_size, _vfs, _cwd, _flash_verified
    _flash_verified = False
    print("[FLASH ] Initializing ...")
    try:
        import vfs
    except ImportError:
        vfs = os
    _vfs = vfs
    _cwd = os.getcwd()
    _flash_mounted = board.external_flash_mounted()
    try:
        import t_echo_spiflash
    except ImportError:
        t_echo_spiflash = None
    _firmware_flash = getattr(t_echo_spiflash, "_bdev", None)
    if _flash_mounted:
        if _firmware_flash is None:
            raise RuntimeError("Cannot identify firmware owner of /flash")
        info = os.statvfs(board.EXTERNAL_FLASH_MOUNT)
        if info[0] != _firmware_flash.BLOCK_SIZE or info[2] != _firmware_flash.BLOCK_COUNT:
            raise RuntimeError("/flash geometry does not match firmware block device")
        flash = _firmware_flash
    elif _firmware_flash is not None:
        flash = _firmware_flash
    else:
        from spi_nor import SpiNor
        gc.collect()
        flash = SpiNor(board.flash_spi(), board.Flash_Cs)
        flash.wake()
    identity = flash.jedec_id()
    if not isinstance(identity, int):
        identity = int.from_bytes(identity, "big")
    exponent = identity & 255
    if identity in (0, 0xFFFFFF) or not 16 <= exponent <= 24:
        raise OSError("Flash JEDEC ID invalid: 0x%06x" % identity)
    _flash_size = (1 << exponent) // 1024
    _flash_verified = True
    print("success\n\tJEDEC ID: 0x%06x\n\tFlash size: %d KB" % (identity, _flash_size))
    print("USB MSC requires a native firmware service; /flash is not exported as an Arduino FAT USB disk")
    listDir()
    return True


def getFlashSize():
    return _flash_size


def fsCheck():
    filename = "/internal/lilygo.txt"
    contents = b"lilygo fs test"
    print("Try create file .\n")
    try:
        with open(filename, "wb") as handle:
            if handle.write(contents) != len(contents):
                raise OSError("Text bytes do not match")
        with open(filename, "rb") as handle:
            if handle.read(len(contents)) != contents:
                raise OSError("The written bytes do not match the read bytes")
    except OSError as error:
        print("Filesystem test failed:", error)
        print("The existing /internal volume is not formatted by this test")
        return False
    return True


def setupInternalFileSystem():
    print("Internal filesystem test: /internal")
    return fsCheck()


def sleepFlash():
    global _flash_unmounted, _flash_asleep
    if board.external_flash_mounted() and (not _flash_verified or flash is None
                                           or flash is not _firmware_flash):
        raise RuntimeError("Refusing sleep: /flash owner was not verified during setup")
    if flash is None:
        return
    os.sync()
    if _flash_mounted and not _flash_unmounted:
        os.chdir("/")
        _vfs.umount(board.EXTERNAL_FLASH_MOUNT)
        _flash_unmounted = True
    _flash_asleep = True
    if flash is _firmware_flash:
        flash._wait_ready()
        flash._command(0xB9)
        time.sleep_us(10)
        flash._spi.deinit()
    else:
        flash.sleep()
        time.sleep_us(10)
        flash.spi.deinit()


def _restoreFlash():
    global _flash_unmounted, _flash_asleep
    if _flash_asleep:
        Pin(board.Power_Enable_Pin, Pin.OUT, value=1)
        time.sleep_ms(12)
        for number in (board.Flash_WP, board.Flash_HOLD, board.Flash_Cs):
            Pin(number, Pin.OUT, value=1)
        if flash is _firmware_flash:
            flash._spi.init(baudrate=8000000, polarity=0, phase=0)
            flash._command(0xAB)
            time.sleep_ms(12)
        else:
            flash.spi = board.flash_spi()
            flash.wake()
        _flash_asleep = False
    if _flash_unmounted:
        _vfs.mount(_vfs.VfsLfs2(_firmware_flash), board.EXTERNAL_FLASH_MOUNT)
        _flash_unmounted = False
    if _cwd is not None:
        os.chdir(_cwd)


def probeDevices(address):
    try:
        Wire.writeto(address, b"")
        return True
    except OSError:
        return False


def probeIMU20948():
    global IMU
    # Read WHO_AM_I before loading the driver for this optional board variant.
    try:
        Wire.writeto_mem(0x68, 0x7F, b"\x00")
        if Wire.readfrom_mem(0x68, 0x00, 1)[0] != 0xEA:
            return False
    except OSError:
        return False
    from icm20948 import ICM20948
    gc.collect()
    sensor = ICM20948(Wire, address=0x68)
    sensor.begin()
    IMU = sensor
    return True


def probeIMU9250():
    global IMU_9250
    try:
        if Wire.readfrom_mem(0x68, 0x75, 1)[0] not in (0x71, 0x73):
            return False
    except OSError:
        return False
    from mpu9250 import MPU9250
    gc.collect()
    sensor = MPU9250(Wire, address=0x68)
    sensor.begin()
    IMU_9250 = sensor
    return True


def probeBHY260AP():
    global bhy
    from bhi260ap import BHI260AP
    gc.collect()
    sensor = BHI260AP(Wire, address=0x28)
    sensor.begin(cache_sensor_info=False)
    bhy = sensor
    print("BHI260AP init successfully!")
    return True


def probeDRV2605():
    global drv
    from drv2605 import DRV2605
    gc.collect()
    drv = DRV2605(Wire, enable=board.DRV2605_ENABLE_PIN)
    print("DRV2605 init successfully!")
    return True


def setupBuzzer():
    Pin(board.BUZZER_PIN, Pin.OUT, value=0)


def drawDevProbe():
    global _led_mode
    display.setFont(DEFAULT_FONT)
    display.setTextColor(GxEPD_BLACK)
    display.setFullWindow()
    display.firstPage()
    _title("T-Echo Self Test")
    display.println()
    display.drawFastHLine(0, display.getCursorY() - 5, display.width(), GxEPD_BLACK)
    display.println()
    display.setFont(FreeMono12pt7b)
    for label, bit in (("[  GPS ] ", 1), ("[" + RADIO_TYPE + "] ", 2),
                       ("[FLASH ] ", 3), ("[PCF8563]", 4), ("[BME280 ]", 5), ("[SPIFFS ]", 6)):
        display.print(label)
        display.println("PASS" if devices_probe_mask & (1 << bit) else "FAIL")
    for label, bit in (("[IMU20948]", 7), ("[MPU9250]", 8), ("[BHI260 ]", 9)):
        if devices_probe_mask & (1 << bit):
            display.print(label)
            display.println("PASS")
            break
    else:
        display.print("[IMU]")
        display.println("FAIL")
    _finish_page()
    _led_mode = WHITE_LED


def drawDevicesInfo():
    global _led_mode
    print("drawDevicesInfo")
    _header("DEVICE INFO")
    display.setFont(FreePuhuiti7pt7b)
    # These two Arduino-specific APIs do not exist in MicroPython.
    values = ("N/A", "N/A", binascii.hexlify(board.unique_device_id()).decode().upper(),
              BLE_NAME, str(getFlashSize()) + "KB", gpsVersion)
    for index, label in enumerate(("BSP LIB   :", "BOOTLOADER:", "SERIAL No :",
                                    "BLE NAME  :", "FLASH SIZE:", "GPS:")):
        _row(index + 1, label, values[index])
    _finish_page()
    _led_mode = WHITE_LED


def drawSleep():
    display.setFullWindow()
    display.firstPage()
    display.fillScreen(GxEPD_WHITE)
    display.setFont(FreeMonoBold24pt7b)
    _title("SLEEP", FreeMonoBold24pt7b[4] + 45)
    _finish_page()


LilyGoCallBack = ((drawDevProbe, None), (drawDevicesInfo, None),
                  (drawGPS, loopGPS), (drawSensor, loopSensor),
                  (drawSender, loopSender), (drawReceiver, loopReceiver), (drawPDM, loopPDM))


def _prepare_system_off():
    global _enter_off
    native = getattr(machine, "system_off", None)
    if native is not None:
        _enter_off = native
        return
    import nrf_power
    gc.collect()
    calls = nrf_power._compile_power_calls()
    _enter_off = calls["_raw_system_off"]
    try:
        import ubluepy
    except ImportError:
        return
    if not nrf_power._verified_s140():
        raise RuntimeError("Cannot verify S140 v6/v7 for SYSTEMOFF")
    import uctypes
    enabled = bytearray(4)
    status = calls["_sd_is_enabled"](uctypes.addressof(enabled))
    if status:
        raise OSError("SoftDevice state query failed: %d" % status)
    if enabled[0]:
        _enter_off = calls["_sd_system_off"]


def deinitPins():
    if board.external_flash_mounted():
        raise RuntimeError("Refusing pin shutdown while /flash is mounted")
    pins = (board.ePaper_Miso, board.ePaper_Mosi, board.ePaper_Sclk,
            board.ePaper_Cs, board.ePaper_Dc, board.ePaper_Rst,
            board.ePaper_Busy, board.ePaper_Backlight, board.LoRa_Miso,
            board.LoRa_Mosi, board.LoRa_Sclk, board.LoRa_Cs, board.LoRa_Rst,
            22, board.LoRa_Dio1, 21, board.LoRa_Busy,
            board.Flash_Cs, board.Flash_Miso, board.Flash_Mosi, board.Flash_Sclk,
            board.Flash_WP, board.Flash_HOLD, board.Touch_Pin, board.Adc_Pin,
            board.RTC_Int_Pin, board.Gps_Rx_Pin, board.Gps_Tx_Pin,
            board.Gps_Reset_Pin, board.Gps_pps_Pin, board.UserButton_Pin,
            board.Power_Enable_Pin, board.Power_Enable1_Pin)
    for number in pins:
        Pin(number, Pin.IN, Pin.PULL_DOWN)
        port, pin = divmod(number, 32)
        address = 0x50000700 + port * 0x300 + pin * 4
        mem32[address] &= ~(3 << 16)
    for number in (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin):
        Pin(number, Pin.IN, Pin.PULL_UP)


def _sleep():
    global _tasks_running
    if board.external_flash_mounted() and (not _flash_verified or flash is None
                                           or flash is not _firmware_flash):
        raise RuntimeError("Refusing sleep: /flash owner was not verified during setup")
    # Resolve all imports/native entry points while the filesystem is mounted.
    _prepare_system_off()
    _tasks_running = False
    sleepFlash()
    drawSleep()
    sleepLoRa()
    sleepSensor()
    sleepGPS()
    for sensor in (IMU, IMU_9250, bhy, drv):
        if sensor is not None:
            sensor.sleep()
    if pdm is not None:
        pdm.stop()
    if ble is not None:
        ble.close()
    display.panel.sleep()
    dispPort.deinit()
    if hasattr(Wire, "deinit"):
        Wire.deinit()
    backlight.deinit()
    board.leds_off()
    touch.irq(handler=None)
    if rtc_irq is not None:
        rtc_irq.irq(handler=None)
    deinitPins()
    # Factory.ino deliberately does not arm GPIO wakeup. Hardware reset wakes.
    result = _enter_off()
    raise OSError("SYSTEMOFF unexpectedly returned: %r" % (result,))


def deviceProbe():
    found = Wire.scan()
    for address in found:
        print("I2C device found at address 0x%02x !" % address)
    print("done\n" if found else "No I2C devices found\n")


def setup():
    global Wire, button, touch, index_max, _tasks_running
    gc.collect()
    Pin(board.Power_Enable_Pin, Pin.OUT, value=1)
    board.leds_off()
    Pin(board.ePaper_Backlight, Pin.OUT, value=0)
    print("sd_power_reset_reason_get:0x%x" % (mem32[0x40000400] & 0xFFFFFFFF))
    button = FactoryButton(board.UserButton_Pin)
    setupDisplay()
    gc.collect()
    if DEBUG_MODE:
        _header("DEBUG")
        display.setCursor(2, 10)
        display.print("Select the T-Echo port and open the serial monitor.")
        _finish_page()
        print("DEBUG_MODE: MicroPython uses its active REPL connection")
    Wire = board.shared_i2c()
    setupBLE()
    gc.collect()
    _probe("setupGPS", setupGPS, 1)
    _probe("setupLoRa", setupLoRa, 2)
    _probe("setupFlash", setupFlash, 3)
    if probeDevices(0x51):
        _probe("setupRTC", setupRTC, 4)
    else:
        print("setupRTC: FAIL")
    if probeDevices(0x77):
        _probe("setupSensor", setupSensor, 5)
    else:
        print("setupSensor: FAIL")
    _probe("setupInternalFileSystem", setupInternalFileSystem, 6)
    if probeDevices(0x68):
        _probe("probeIMU20948", probeIMU20948, 7)
        _probe("probeIMU9250", probeIMU9250, 8)
    if probeDevices(0x28):
        _probe("probeBHY260AP", probeBHY260AP, 9)
        if probeDevices(0x5A):
            _probe("probeDRV2605", probeDRV2605, 10)
            setupBuzzer()
            index_max -= 1
        else:
            try:
                beginPDM()
            except (OSError, RuntimeError) as error:
                print("Failed to start PDM!", error)
    elif probeDevices(0x5A):
        _probe("probeDRV2605", probeDRV2605, 10)
        setupBuzzer()
    touch = Pin(board.Touch_Pin, Pin.IN, Pin.PULL_DOWN)
    touch.irq(handler=_touch_irq, trigger=Pin.IRQ_RISING)
    _tasks_running = True


def loop():
    global funcSelectIndex, prevFuncSelectIndex, _pending_clicks
    _background()
    while _pending_clicks:
        _pending_clicks -= 1
        motor_shield_tone()
        funcSelectIndex = (funcSelectIndex + 1) % index_max
        print("funcSelectIndex:", funcSelectIndex)
    first, repeat = LilyGoCallBack[funcSelectIndex]
    if prevFuncSelectIndex != funcSelectIndex:
        print("Run firstFunc")
        prevFuncSelectIndex = funcSelectIndex
        first()
    if repeat is not None:
        repeat()
    if gotoSleep:
        _sleep()


def main():
    global _tasks_running
    try:
        setup()
        while True:
            loop()
            time.sleep_ms(1)
    finally:
        _tasks_running = False
        try:
            _restoreFlash()
        finally:
            _cleanup_peripherals()


def _cleanup_call(label, action):
    try:
        action()
    except (OSError, RuntimeError) as error:
        print("%s cleanup failed: %s" % (label, error))


def _cleanup_peripherals():
    if touch is not None:
        touch.irq(handler=None)
    if rtc_irq is not None:
        rtc_irq.irq(handler=None)
    if ble is not None:
        _cleanup_call("BLE", ble.close)
    if pdm is not None:
        _cleanup_call("PDM", pdm.stop)
    if isRadioOnline:
        _cleanup_call("LoRa", radio.sleep)
    for sensor in (bme, IMU, IMU_9250, bhy, drv):
        if sensor is not None:
            _cleanup_call("Sensor", sensor.sleep)
    if SerialGPS is not None:
        _cleanup_call("GPS", sleepGPS)
    if display is not None:
        _cleanup_call("Display", display.panel.sleep)
    for bus in (rfPort, dispPort, Wire):
        if bus is not None and hasattr(bus, "deinit"):
            _cleanup_call("Bus", bus.deinit)
    if backlight is not None:
        _cleanup_call("Backlight", backlight.deinit)
    if flash is not None and flash is not _firmware_flash and not board.external_flash_mounted():
        _cleanup_call("Flash", flash.sleep)
        _cleanup_call("Flash bus", flash.spi.deinit)
    # The firmware-owned, mounted flash and its 3.3 V supply stay available.
    board.leds_off()


if __name__ == "__main__":
    main()
