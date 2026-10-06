"""LoraTransmit.ino: 433 MHz, SF12, CR4/6, sync AB, 22 dBm."""
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
    print("success!")
    radio.enable_interrupt()
    print("[SX1262] Sending first packet ... ", end="")
    radio.start_transmit("Hello World!")
    time.sleep_ms(200)
    logo(display)
    display.show()
    count = 0
    try:
        while True:
            transmission_state = radio.finish_transmit()
            if transmission_state is not None:
                print("transmission finished!" if transmission_state else "transmission timeout!")
                time.sleep_ms(1000)
                print("[SX1262] Sending another packet ... ", end="")
                radio.start_transmit("Hello World! #%d" % count)
                count += 1
            time.sleep_ms(5)
    finally:
        radio.dio1.irq(handler=None)
        radio.standby()


if __name__ == "__main__":
    main()
