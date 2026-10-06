"""LoraReceive.ino: receiver paired with LoraTransmit.py."""
import sys
for _path in ("/libraries", "/lib", "../../libraries"):
    if _path not in sys.path:
        sys.path.append(_path)
if "__file__" in globals() and "/" in __file__:
    sys.path.append(__file__.rsplit("/", 1)[0] + "/../../libraries")
import time
from machine import Pin
import t_echo_config as board
from sx1262 import SX1262
from ssd1681 import SSD1681
from display_assets import logo


def main():
    board.power_on()
    print("Start\n")
    Pin(board.ePaper_Backlight, Pin.OUT, value=0)
    Pin(board.UserButton_Pin, Pin.IN, Pin.PULL_UP)
    Pin(board.Touch_Pin, Pin.IN, Pin.PULL_UP)
    leds = [Pin(pin, Pin.OUT, value=1) for pin in
            (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin)]
    for _ in range(10):
        for led in leds:
            led.value(1 - led.value())
        time.sleep_ms(300)
    for led in leds:
        led.value(1)
    display = SSD1681(board.display_spi(), board.ePaper_Cs, board.ePaper_Dc,
                      board.ePaper_Rst, board.ePaper_Busy, rotation=2)
    display.begin()
    radio = SX1262(board.radio_spi(), board.LoRa_Cs, board.LoRa_Busy,
                   board.LoRa_Rst, board.LoRa_Dio1)
    print("[SX1262] Initializing ... ", end="")
    radio.begin(433.0, 125.0, 12, 6, 0xAB, 22, 16, False)
    radio.set_current_limit(140)
    radio.enable_interrupt()
    print("Radio Starting to listen ... ", end="")
    radio.start_receive()
    print("success!")
    time.sleep_ms(200)
    logo(display)
    display.show()
    try:
        while True:
            if radio.poll():
                data = radio.read()
                if data is not None:
                    print("[SX1262] Received packet!")
                    try:
                        text = data.decode()
                    except UnicodeError:
                        text = repr(data)
                    print("[SX1262] Data:\t\t" + text)
                    print("[SX1262] RSSI:\t\t%.2f dBm" % radio.last_rssi)
                    print("[SX1262] SNR:\t\t%.2f dB" % radio.last_snr)
                    print("[SX1262] Frequency error:\tunavailable on SX1262")
                elif radio.last_irq & (radio.IRQ_CRC_ERROR | radio.IRQ_HEADER_ERROR):
                    print("CRC error!")
                radio.start_receive()
            time.sleep_ms(5)
    finally:
        radio.dio1.irq(handler=None)
        radio.standby()


if __name__ == "__main__":
    main()
