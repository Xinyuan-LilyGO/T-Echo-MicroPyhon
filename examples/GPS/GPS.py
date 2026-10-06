"""GPS.ino: L76K initialization and bidirectional USB/UART console."""
import sys
from machine import Pin
import time
import select
import t_echo_config as board

GNSS_DISABLE_NMEA_OUTPUT = b"$PCAS03,0,0,0,0,0,0,0,0,0,0,,,0,0*02\r\n"
GNSS_GET_VERSION = b"$PCAS06,0*1B\r\n"


def gnss_probe(uart):
    for _ in range(5):
        uart.write(GNSS_DISABLE_NMEA_OUTPUT)
        time.sleep_ms(5)
        started = time.ticks_ms()
        while uart.any() and time.ticks_diff(time.ticks_ms(), started) < 500:
            data = uart.read()
            if data:
                sys.stdout.write(data.decode())
        if uart.any():
            print("Wait L76K stop output timeout!")
            print("GPS OUT PUT NOT DISABLE .")
            time.sleep_ms(500)
            continue
        time.sleep_ms(200)
        uart.write(GNSS_GET_VERSION)
        response = bytearray()
        started = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), started) < 1000:
            data = uart.read()
            if data:
                response.extend(data)
                if b"$GPTXT,01,01,02" in response:
                    print("L76K GNSS init succeeded, using L76K GNSS Module\n")
                    return True
                if len(response) > 512:
                    response = response[-128:]
            time.sleep_ms(2)
        time.sleep_ms(500)
    return False


def main():
    board.power_on()
    print("[GPS] Initializing ... ")
    uart = board.gps_uart(9600)
    Pin(board.Gps_pps_Pin, Pin.IN)
    Pin(board.Gps_Wakeup_Pin, Pin.OUT, value=1)
    time.sleep_ms(10)
    reset = Pin(board.Gps_Reset_Pin, Pin.OUT, value=1)
    time.sleep_ms(10)
    reset.value(0)
    time.sleep_ms(10)
    reset.value(1)
    found = gnss_probe(uart)
    if found:
        uart.write(b"$PCAS04,5*1C\r\n")
        time.sleep_ms(250)
        uart.write(b"$PCAS03,1,0,0,0,1,0,0,0,0,0,,,0,0*02\r\n")
        time.sleep_ms(250)
        uart.write(b"$PCAS11,3*1E\r\n")
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    try:
        while True:
            if not found:
                print("GPS not found...")
                time.sleep_ms(1000)
            # Correct the original loop's swapped available()/read() sides.
            data = uart.read()
            if data:
                try:
                    sys.stdout.write(data.decode())
                except UnicodeError:
                    print(data)
            if poll.poll(0):
                data = sys.stdin.read(1)
                if data:
                    uart.write(data.encode())
            time.sleep_ms(2)
    finally:
        poll.unregister(sys.stdin)
        uart.deinit()


if __name__ == "__main__":
    main()
