"""SX126x_Transmit_Interrupt.ino: RadioLib defaults, 434 MHz SF9."""
import sys
for _path in ("/libraries", "/lib", "../../libraries"):
    if _path not in sys.path:
        sys.path.append(_path)
if "__file__" in globals() and "/" in __file__:
    sys.path.append(__file__.rsplit("/", 1)[0] + "/../../libraries")
import time
import t_echo_config as board
from sx1262 import SX1262


def main():
    board.power_on()
    radio = SX1262(board.radio_spi(), board.LoRa_Cs, board.LoRa_Busy,
                   board.LoRa_Rst, board.LoRa_Dio1)
    print("[SX1262] Initializing ... ", end="")
    radio.begin(434.0, 125.0, 9, 7, 0x12, 10, 8, True)
    print("success!")
    radio.enable_interrupt()
    count = 0
    print("[SX1262] Sending first packet ... ", end="")
    radio.start_transmit("Hello World!")
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
