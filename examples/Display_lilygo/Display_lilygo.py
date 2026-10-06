"""Display_lilygo.ino: original LILYGO bitmap and active-low RGB cycle."""
import sys
from machine import Pin
import time
import t_echo_config as board
from ssd1681 import SSD1681
from display_assets import logo

def main():
    board.power_on()
    Pin(board.ePaper_Backlight, Pin.OUT, value=0)
    leds = [Pin(pin, Pin.OUT, value=1) for pin in
            (board.GreenLed_Pin, board.RedLed_Pin, board.BlueLed_Pin)]
    Pin(board.UserButton_Pin, Pin.IN, Pin.PULL_UP)
    Pin(board.Touch_Pin, Pin.IN, Pin.PULL_UP)
    for _ in range(10):
        for led in leds:
            led.value(1 - led.value())
        time.sleep_ms(300)
    for led in leds:
        led.on()
    display = SSD1681(board.display_spi(), board.ePaper_Cs, board.ePaper_Dc,
                      board.ePaper_Rst, board.ePaper_Busy, rotation=2)
    display.begin()
    # Horizontal bitmap flip with rotation=0 corrects the vertical mirror.
    logo(display, rotation=1)
    display.show()
    index = 0
    try:
        while True:
            for number, led in enumerate(leds):
                led.value(0 if number == index else 1)
            index = (index + 1) % 3
            time.sleep_ms(1000)
    finally:
        for led in leds:
            led.on()
        display.sleep()


if __name__ == "__main__":
    main()
