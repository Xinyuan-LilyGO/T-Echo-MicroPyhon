"""BHI260AP stable v3: bounded FIFO reads and signed integer output."""

import sys
_here = globals().get("__file__", "").replace("\\", "/")
_base = _here.rsplit("/", 3)[0] if _here.count("/") >= 3 else "."
for _path in ("/flash/libraries", "/libraries", "/lib", _base + "/libraries", "../../libraries"):
    if _path not in sys.path:
        sys.path.append(_path)

import time
import gc
import os
import machine
import bhi260ap
from t_echo_config import power_on, shared_i2c
from bhi260ap import BHI260AP

I2C_FREQUENCY_HZ = 100000
SAMPLE_RATE_HZ = 25
POLL_INTERVAL_MS = 20
PRINT_INTERVAL_MS = 500  # 2 lines/s; sampling and printing are independent.
FIFO_READ_BUDGET = 128  # Max payload bytes per loop; each I2C chunk is <= 32.
GC_INTERVAL_MS = 1000
SHOW_SENSOR_DETAILS = False
OUTPUT_TEST_ONLY = False  # Synthetic +/- values, labelled TEST; no sensor I/O.
PRINT_SENSOR_VALUES = True  # False: acquire normally, without streaming values.


def _axis_text(raw, multiplier, divisor):
    # Integer-only physical-unit conversion with symmetric rounding.
    # Keep the real sign, including tiny negative values rounded to -0.00.
    sign = '-' if raw < 0 else '+'
    magnitude = -raw if raw < 0 else raw
    hundredths = (magnitude * multiplier + divisor // 2) // divisor
    return '%s%d.%02d' % (sign, hundredths // 100, hundredths % 100)


def _print_sample(ticks, acceleration, gyro, test=False):
    seconds, nanoseconds = BHI260AP.timestamp_parts(ticks)
    print(('[%sT: %d.%09d] AX:%7s AY:%7s AZ:%7s GX:%7s GY:%7s GZ:%7s') %
          ('TEST ' if test else '', seconds, nanoseconds,
           _axis_text(acceleration[0], 100, 4096),
           _axis_text(acceleration[1], 100, 4096),
           _axis_text(acceleration[2], 100, 4096),
           _axis_text(gyro[0], 3125, 512),
           _axis_text(gyro[1], 3125, 512),
           _axis_text(gyro[2], 3125, 512)))


def _output_test():
    print('OUTPUT TEST ONLY: synthetic values; sensor is not accessed. Ctrl-C stops.')
    ticks = 0
    negative = False
    while True:
        values = ((-3031, -1, -32768), (-392, -2487, -32768)) if negative else (
            (3031, 1, 32767), (392, 2487, 32767))
        _print_sample(ticks, values[0], values[1], test=True)
        ticks += PRINT_INTERVAL_MS * 64
        negative = not negative
        gc.collect()
        time.sleep_ms(PRINT_INTERVAL_MS)


def _startup_info():
    print('BHI260AP stable v3: Python only, no UF2 update')
    print('[BHI LIB] file=%s API=%s (expected 2)' % (
        getattr(bhi260ap, '__file__', '<frozen>'), getattr(BHI260AP, 'API_VERSION', 0)))
    reset_cause = getattr(machine, 'reset_cause', None)
    if reset_cause is not None:
        print('[MCU] reset_cause=%s (firmware-specific code)' % reset_cause())
    uname = getattr(os, 'uname', None)
    if uname is not None:
        print('[Firmware]', uname())
    mem_free = getattr(gc, 'mem_free', None)
    if mem_free is not None:
        print('[Memory] free=%d bytes' % mem_free())


def _make_sensor(i2c):
    try:
        scanned = i2c.scan()
        print('I2C scan:', ', '.join('0x%02X' % address for address in scanned))
    except AttributeError:
        scanned = (0x28, 0x29)
    for address in (0x28, 0x29):
        if address in scanned:
            sensor = BHI260AP(i2c, address, raw=True)
            if sensor.probe():
                print('BHI260AP found at 0x%02X' % address)
                return sensor
    raise OSError('BHI260AP not found at 0x28/0x29; check sensor power and I2C')


def main():
    _startup_info()
    if getattr(BHI260AP, 'API_VERSION', 0) != 2:
        print('Upload the matching bhi260ap.py to /flash/libraries and restart the interpreter.')
        print('Also replace any older same-name .mpy file. No UF2 is needed.')
        return
    if OUTPUT_TEST_ONLY:
        try:
            _output_test()
        except KeyboardInterrupt:
            print('Output test stopped.')
        return
    sensor = None
    enabled = []
    try:
        power_on()
        time.sleep_ms(100)
        print('Initializing Sensors...')
        sensor = _make_sensor(shared_i2c(freq=I2C_FREQUENCY_HZ))
        sensor.begin(cache_sensor_info=SHOW_SENSOR_DETAILS)
        if SHOW_SENSOR_DETAILS:
            for key, value in sensor.info().items():
                print('%s: %s' % (key, value))
            for sensor_id in sensor.available:
                print(sensor.sensor_info[sensor_id])
        print('BHI260AP: sample %d Hz, print every %d ms; Ctrl-C stops.' %
              (SAMPLE_RATE_HZ, PRINT_INTERVAL_MS))
        print('AX/AY/AZ: g; GX/GY/GZ: degrees/s; integer conversion, real signs.')
        if not PRINT_SENSOR_VALUES:
            print('Sensor-only mode: acquiring without streaming values; Ctrl-C stops.')
        for sensor_id in (sensor.ACCEL_PASSTHROUGH, sensor.GYRO_PASSTHROUGH):
            sensor.configure(sensor_id, SAMPLE_RATE_HZ, 0)
            enabled.append(sensor_id)
        gc.collect()
        last_print = last_gc = time.ticks_ms()
        accel_ready = gyro_ready = False
        while True:
            updated = sensor.update(max_bytes=FIFO_READ_BUDGET)
            accel_ready = accel_ready or sensor.ACCEL_PASSTHROUGH in updated
            gyro_ready = gyro_ready or sensor.GYRO_PASSTHROUGH in updated
            now = time.ticks_ms()
            if (PRINT_SENSOR_VALUES and accel_ready and gyro_ready and
                    time.ticks_diff(now, last_print) >= PRINT_INTERVAL_MS):
                ticks, acceleration = sensor.latest[sensor.ACCEL_PASSTHROUGH]
                gyro = sensor.latest[sensor.GYRO_PASSTHROUGH][1]
                _print_sample(ticks, acceleration, gyro)
                accel_ready = gyro_ready = False
                # No catch-up bursts after a slow USB write.
                last_print = time.ticks_ms()
            if time.ticks_diff(now, last_gc) >= GC_INTERVAL_MS:
                gc.collect()
                last_gc = time.ticks_ms()
            time.sleep_ms(POLL_INTERVAL_MS)
    except KeyboardInterrupt:
        print('Sensor output stopped.')
    except OSError as error:
        print('BHI260AP stopped:', error)
        raise
    finally:
        # Attempt both disables without replacing an earlier exception with a
        # cleanup I2C failure. Do not hide acquisition/formatting errors.
        for sensor_id in enabled:
            try:
                sensor.configure(sensor_id, 0.0)
            except OSError as error:
                print('Could not stop sensor %d: %s' % (sensor_id, error))


if __name__ == "__main__":
    main()
