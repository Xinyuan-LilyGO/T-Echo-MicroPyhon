"""SX126x_Receive_Interrupt.ino: RadioLib defaults, 434 MHz SF9."""
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
    print("[SX1262] Starting to listen ... ", end="")
    radio.start_receive()
    print("success!")
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
