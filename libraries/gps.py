"""L76K probe and configuration translated from GPS.ino/gps.cpp."""

import time


def command(uart, body):
    checksum = 0
    for ch in body.encode():
        checksum ^= ch
    uart.write(("$%s*%02X\r\n" % (body, checksum)).encode())


def probe(uart, retries=5):
    for _ in range(retries):
        command(uart, "PCAS03,0,0,0,0,0,0,0,0,0,0,,,0,0")
        time.sleep_ms(200)
        start = time.ticks_ms()
        while uart.any() and time.ticks_diff(time.ticks_ms(), start) < 500:
            uart.read()
        command(uart, "PCAS06,0")
        response = bytearray()
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < 1000:
            data = uart.read()
            if data:
                response.extend(data)
                if b"$GPTXT,01,01,02" in response:
                    return bytes(response)
                if len(response) > 512:
                    response = response[-128:]
            time.sleep_ms(5)
    return None


def configure(uart):
    command(uart, "PCAS04,5")
    time.sleep_ms(250)
    command(uart, "PCAS03,1,0,0,0,1,0,0,0,0,0,,,0,0")
    time.sleep_ms(250)
    command(uart, "PCAS11,3")


def console_bridge(uart):
    import sys
    import select
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    while True:
        data = uart.read()
        if data:
            try:
                sys.stdout.write(data.decode())
            except UnicodeError:
                print(data)
        if poll.poll(0):
            data = sys.stdin.read(1)
            if data:
                uart.write(data)
        time.sleep_ms(2)
